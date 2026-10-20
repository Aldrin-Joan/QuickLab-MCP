"""Comprehensive integration tests verifying all fixes from the ModelLab MCP Test Report.

Verifies:
1. Default project seeding (default-project for default-tenant) on server startup.
2. Explicit project management tools (create_project, list_projects).
3. Dataset registration format preservation (CSV, JSONL, Parquet) - Fix for Issue 2.
4. Information disclosure elimination on DB foreign key / constraint errors - Fix for Issue 3.
5. Consistent project validation in list_experiments and list_datasets - Fix for Issue 4.
6. Malformed input / strict base64 decoding in validate_dataset - Fix for Issue 5.
7. Distinct SHA-256 container image digests across model families - Fix for Issue 6.
8. Unblocked downstream tools:
   - create_experiment
   - list_experiment_artifacts
   - read_experiment_artifact
   - analyze_experiment
   - analyze_model_errors
   - get_experiment_predictions
   - check_experiment_validity
   - list_dataset_versions
"""

import base64
import json
import re

import pytest
from fastmcp.exceptions import ToolError

from ml_mcp.domain.policies import Principal, Role
from ml_mcp.infrastructure.postgres.base import Base
from ml_mcp.infrastructure.postgres.session import get_db_manager
from ml_mcp.server.app import app_lifespan, create_server
from ml_mcp.server.context import set_current_principal


@pytest.fixture
async def test_server():
    db_mgr = get_db_manager()
    db_mgr.initialize(
        custom_url="sqlite+aiosqlite:///file:test_remediation_db?mode=memory&cache=shared&uri=true"
    )
    async with db_mgr.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    server = create_server()
    async with app_lifespan(server):
        principal = Principal(
            principal_id="remediation-tester",
            tenant_id="default-tenant",
            role=Role.ADMIN,
            scopes={"*"},
        )
        set_current_principal(principal)
        yield server
        set_current_principal(None)
    await db_mgr.close()


def extract_result(tool_res):
    """Unwrap structured content or JSON text from FastMCP tool result."""
    if tool_res.structured_content is not None:
        if (
            isinstance(tool_res.structured_content, dict)
            and "result" in tool_res.structured_content
            and len(tool_res.structured_content) == 1
        ):
            return tool_res.structured_content["result"]
        return tool_res.structured_content
    return json.loads(tool_res.content[0].text)


@pytest.mark.asyncio
async def test_default_project_seeding_on_startup(test_server):
    """Issue 1 Remediation: Verify default-project is seeded on startup for default-tenant."""
    res = await test_server.call_tool("list_projects", {})
    assert not res.is_error
    projects = extract_result(res)
    assert len(projects) >= 1
    assert any(p["project_id"] == "default-project" for p in projects)
    default_proj = next(p for p in projects if p["project_id"] == "default-project")
    assert default_proj["name"] == "Default Project"


@pytest.mark.asyncio
async def test_explicit_project_lifecycle(test_server):
    """Issue 1 Remediation: Verify create_project and list_projects tools."""
    create_res = await test_server.call_tool(
        "create_project",
        {"name": "Customer Churn Q3", "description": "Predicting Q3 telecom churn"},
    )
    assert not create_res.is_error
    created = extract_result(create_res)
    assert created["name"] == "Customer Churn Q3"
    assert created["project_id"] is not None
    proj_id = created["project_id"]

    list_res = await test_server.call_tool("list_projects", {})
    assert not list_res.is_error
    projects = extract_result(list_res)
    assert any(p["project_id"] == proj_id for p in projects)


@pytest.mark.asyncio
async def test_dataset_format_preservation_issue_2(test_server):
    """Issue 2 Remediation: Verify register_dataset preserves input data_format ("csv")."""
    csv_data = "col_a,col_b,label\n1.0,2.0,1\n3.0,4.0,0\n5.0,6.0,1\n"
    csv_b64 = base64.b64encode(csv_data.encode("utf-8")).decode("ascii")

    reg_res = await test_server.call_tool(
        "register_dataset",
        {
            "project_id": "default-project",
            "name": "csv_telecom_data",
            "description": "Tabular CSV dataset",
            "format": "csv",
            "data_base64": csv_b64,
            "version": "1.0",
        },
    )
    assert not reg_res.is_error
    reg_payload = extract_result(reg_res)
    assert reg_payload["format"] == "csv"
    dataset_id = reg_payload["dataset_id"]

    # Verify get_dataset preserves format
    get_res = await test_server.call_tool("get_dataset", {"dataset_id": dataset_id})
    assert not get_res.is_error
    assert extract_result(get_res)["format"] == "csv"

    # Verify list_datasets preserves format
    list_res = await test_server.call_tool("list_datasets", {"project_id": "default-project"})
    assert not list_res.is_error
    datasets = extract_result(list_res)
    matched = next(d for d in datasets if d["dataset_id"] == dataset_id)
    assert matched["format"] == "csv"


@pytest.mark.asyncio
async def test_information_disclosure_prevention_issue_3(test_server):
    """Issue 3 Remediation: Verify raw SQL / parameters do not leak on constraint failure."""
    csv_data = "x,y\n1,0\n2,1\n"
    csv_b64 = base64.b64encode(csv_data.encode("utf-8")).decode("ascii")

    # Attempt to register dataset with non-existent project_id
    with pytest.raises(ToolError) as exc_info:
        await test_server.call_tool(
            "register_dataset",
            {
                "project_id": "nonexistent-project-uuid-9999",
                "name": "leak_test_dataset",
                "description": "Test dataset",
                "format": "csv",
                "data_base64": csv_b64,
            },
        )

    err_msg = str(exc_info.value)
    # Check that database implementation details and SQL dumps are sanitized
    assert "sqlalchemy" not in err_msg.lower()
    assert "asyncpg" not in err_msg.lower()
    assert "insert into" not in err_msg.lower()
    assert "parameters:" not in err_msg.lower()
    # Check that a clean domain message is presented
    assert "project" in err_msg.lower() or "not found" in err_msg.lower()


@pytest.mark.asyncio
async def test_consistent_project_validation_issue_4(test_server):
    """Issue 4 Remediation: Verify list_experiments and list_datasets validate project existence."""
    # list_experiments with non-existent project
    with pytest.raises(ToolError) as exc_info_exp:
        await test_server.call_tool("list_experiments", {"project_id": "nonexistent-project-xyz"})
    assert "nonexistent-project-xyz" in str(exc_info_exp.value)
    assert "not found" in str(exc_info_exp.value)

    # list_datasets with non-existent project
    with pytest.raises(ToolError) as exc_info_ds:
        await test_server.call_tool("list_datasets", {"project_id": "nonexistent-project-xyz"})
    assert "nonexistent-project-xyz" in str(exc_info_ds.value)
    assert "not found" in str(exc_info_ds.value)


@pytest.mark.asyncio
async def test_malformed_base64_rejection_issue_5(test_server):
    """Issue 5 Remediation: Verify malformed or truncated base64 is rejected with clean error."""
    with pytest.raises(ToolError) as exc_info:
        await test_server.call_tool(
            "validate_dataset",
            {"format": "csv", "data_base64": "invalid_base64_truncated!!!"},
        )
    assert "Invalid base64 payload" in str(exc_info.value)


@pytest.mark.asyncio
async def test_distinct_model_digests_issue_6(test_server):
    """Issue 6 Remediation: Verify each model family has a distinct SHA-256 container digest."""
    models_res = await test_server.call_tool("list_models", {})
    assert not models_res.is_error
    models = extract_result(models_res)
    assert len(models) == 7

    digests: dict[str, str] = {}
    for m in models:
        v_res = await test_server.call_tool("list_model_versions", {"model_id": m["model_id"]})
        assert not v_res.is_error
        versions = extract_result(v_res)
        assert len(versions) >= 1
        digest = versions[0]["container_image_digest"]
        assert digest.startswith("sha256:")
        # Check valid 64-hex char SHA256
        assert re.match(r"^sha256:[0-9a-f]{64}$", digest) is not None
        digests[m["model_id"]] = digest

    # Verify all 7 digests are unique
    assert len(set(digests.values())) == 7


@pytest.mark.asyncio
async def test_all_downstream_tools_unblocked(test_server):
    """Downstream Verification: Test all 8 previously blocked tools end-to-end."""
    # 1. Register a valid dataset under default-project
    csv_data = (
        "feature_1,feature_2,label\n"
        "1.2,3.4,1\n"
        "0.8,2.1,0\n"
        "2.5,4.9,1\n"
        "0.3,1.1,0\n"
        "1.9,3.8,1\n"
        "0.5,1.5,0\n"
    )
    csv_b64 = base64.b64encode(csv_data.encode("utf-8")).decode("ascii")

    reg_res = await test_server.call_tool(
        "register_dataset",
        {
            "project_id": "default-project",
            "name": "downstream_dataset",
            "description": "Dataset for downstream testing",
            "format": "csv",
            "data_base64": csv_b64,
            "version": "1.0",
        },
    )
    assert not reg_res.is_error
    dataset_info = extract_result(reg_res)
    dataset_id = dataset_info["dataset_id"]
    dataset_version_id = dataset_info["version_id"]

    # Tool 1: list_dataset_versions
    dsv_res = await test_server.call_tool("list_dataset_versions", {"dataset_id": dataset_id})
    assert not dsv_res.is_error
    versions = extract_result(dsv_res)
    assert len(versions) >= 1
    assert versions[0]["version"] == "1.0"

    # Tool 2: create_experiment
    mv_res = await test_server.call_tool("list_model_versions", {"model_id": "logistic_regression"})
    model_version_id = extract_result(mv_res)[0]["version_id"]

    exp_res = await test_server.call_tool(
        "create_experiment",
        {
            "project_id": "default-project",
            "dataset_version_id": dataset_version_id,
            "model_version_id": model_version_id,
            "task_type": "binary_classification",
            "target_column": "label",
            "primary_metric": "roc_auc",
            "hyperparameters": {"C": 0.5},
        },
    )
    assert not exp_res.is_error
    exp_info = extract_result(exp_res)
    experiment_id = exp_info["experiment_id"]
    assert exp_info["status"] == "QUEUED"

    # Guardrail Check (New Issue B): Calling analyze_experiment before experiment completes must fail
    with pytest.raises(ToolError) as guard_exc:
        await test_server.call_tool("analyze_experiment", {"experiment_id": experiment_id})
    assert "not completed successfully" in str(guard_exc.value)

    # Execute experiment with WorkerRunner to generate real metrics & artifacts
    from ml_mcp.workers.runner import WorkerRunner

    db_mgr = get_db_manager()
    async with db_mgr.session() as sess:
        runner = WorkerRunner(sess)
        await runner.execute_experiment(experiment_id, "default-tenant")

    # Tool 3: list_experiment_artifacts
    art_res = await test_server.call_tool(
        "list_experiment_artifacts", {"experiment_id": experiment_id}
    )
    assert not art_res.is_error
    artifacts = extract_result(art_res)
    assert isinstance(artifacts, list)
    assert len(artifacts) >= 1

    # Tool 4: read_experiment_artifact (verifying real artifact)
    read_art_res = await test_server.call_tool(
        "read_experiment_artifact", {"artifact_id": artifacts[0]["artifact_id"]}
    )
    assert not read_art_res.is_error
    assert "download_url" in extract_result(read_art_res)

    # Tool 5: get_experiment_metrics
    metrics_res = await test_server.call_tool(
        "get_experiment_metrics", {"experiment_id": experiment_id}
    )
    assert not metrics_res.is_error
    metrics_data = extract_result(metrics_res)
    assert metrics_data["experiment_id"] == experiment_id
    assert len(metrics_data["metrics"]) > 0

    # Tool 6: get_experiment_predictions
    pred_res = await test_server.call_tool(
        "get_experiment_predictions", {"experiment_id": experiment_id, "limit": 5}
    )
    assert not pred_res.is_error
    pred_data = extract_result(pred_res)
    assert pred_data["experiment_id"] == experiment_id
    assert "download_url" in pred_data

    # Tool 7: analyze_experiment
    analyze_res = await test_server.call_tool(
        "analyze_experiment", {"experiment_id": experiment_id}
    )
    assert not analyze_res.is_error
    analysis = extract_result(analyze_res)
    assert "overfitting_signals" in analysis
    assert "leakage_signals" in analysis
    assert "recommended_next_experiments" in analysis

    # Tool 8: analyze_model_errors
    errors_res = await test_server.call_tool(
        "analyze_model_errors", {"experiment_id": experiment_id}
    )
    assert not errors_res.is_error
    error_data = extract_result(errors_res)
    assert "error_patterns" in error_data
    assert "overfitting_diagnosis" in error_data

    # Tool 9: check_experiment_validity
    validity_res = await test_server.call_tool(
        "check_experiment_validity", {"experiment_id": experiment_id}
    )
    assert not validity_res.is_error
    validity_data = extract_result(validity_res)
    assert "is_valid" in validity_data
