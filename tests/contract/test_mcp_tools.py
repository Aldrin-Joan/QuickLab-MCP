"""Contract tests for ModelLab FastMCP Server.

Verifies protocol compliance, tool schema contracts, tool execution,
resource templates, and prompt rendering.
"""

import base64
import json

import pytest

from ml_mcp.application.models.service import ModelService
from ml_mcp.domain.policies import Principal, Role
from ml_mcp.infrastructure.postgres.base import Base
from ml_mcp.infrastructure.postgres.session import get_db_manager
from ml_mcp.server.app import create_server
from ml_mcp.server.context import set_current_principal


@pytest.fixture(autouse=True)
async def setup_db():
    db_mgr = get_db_manager()
    db_mgr.initialize(
        custom_url="sqlite+aiosqlite:///file:contract_mcp_db?mode=memory&cache=shared&uri=true"
    )
    async with db_mgr.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed model catalog & default project
    async with db_mgr.session() as sess:
        service = ModelService(sess)
        await service.seed_catalog()
        from ml_mcp.application.projects.service import ProjectService

        proj_service = ProjectService(sess)
        await proj_service.ensure_default_project("tenant-alpha")

    # Set admin principal for tool contract tests
    principal = Principal(
        principal_id="test-contract-user",
        tenant_id="tenant-alpha",
        role=Role.ADMIN,
        scopes={"*"},
    )
    set_current_principal(principal)
    yield
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
async def test_tool_catalog_contract():
    """Verify all 23 tools are registered with valid schemas and descriptions."""
    server = create_server()
    tools = await server.list_tools()
    assert len(tools) == 23

    tool_names = {t.name for t in tools}
    expected_tools = {
        "create_project",
        "list_projects",
        "list_models",
        "get_model",
        "list_model_versions",
        "list_datasets",
        "get_dataset",
        "list_dataset_versions",
        "register_dataset",
        "validate_dataset",
        "inspect_dataset",
        "create_experiment",
        "get_experiment",
        "cancel_experiment",
        "list_experiments",
        "get_experiment_metrics",
        "get_experiment_predictions",
        "list_experiment_artifacts",
        "read_experiment_artifact",
        "compare_experiments",
        "analyze_experiment",
        "analyze_model_errors",
        "check_experiment_validity",
    }
    assert tool_names == expected_tools

    # Verify each tool has a non-empty description
    for t in tools:
        assert t.description is not None
        assert len(t.description) > 10


@pytest.mark.asyncio
async def test_project_lifecycle_tools():
    """Verify create_project and list_projects via call_tool."""
    server = create_server()
    create_res = await server.call_tool(
        "create_project",
        {"name": "test-project-alpha", "description": "Test project for contract testing"},
    )
    assert not create_res.is_error
    created = extract_result(create_res)
    assert created["name"] == "test-project-alpha"
    assert "project_id" in created

    list_res = await server.call_tool("list_projects", {})
    assert not list_res.is_error
    projects = extract_result(list_res)
    assert len(projects) >= 1
    assert any(p["name"] == "test-project-alpha" for p in projects)


@pytest.mark.asyncio
async def test_model_discovery_tools():
    """Verify list_models, get_model, and list_model_versions via call_tool."""
    server = create_server()

    # 1. list_models
    res = await server.call_tool("list_models", {})
    assert not res.is_error
    models = extract_result(res)
    assert len(models) == 7

    # 2. get_model
    res_xgb = await server.call_tool("get_model", {"model_id": "xgboost"})
    assert not res_xgb.is_error
    xgb_details = extract_result(res_xgb)
    assert xgb_details["name"] == "XGBoost"
    assert len(xgb_details["versions"]) > 0

    # 3. list_model_versions and verify distinct digests
    res_versions = await server.call_tool("list_model_versions", {"model_id": "random_forest"})
    assert not res_versions.is_error
    versions = extract_result(res_versions)
    assert len(versions) >= 1
    assert versions[0]["version"] == "1.0.0"

    res_lr = await server.call_tool("list_model_versions", {"model_id": "logistic_regression"})
    assert not res_lr.is_error
    lr_versions = extract_result(res_lr)
    assert lr_versions[0]["container_image_digest"] != versions[0]["container_image_digest"]


@pytest.mark.asyncio
async def test_dataset_lifecycle_tools():
    """Verify validate_dataset, register_dataset, and inspect_dataset via call_tool."""
    from fastmcp.exceptions import ToolError

    server = create_server()

    csv_data = "feat1,feat2,label\n1.0,2.0,0\n3.0,4.0,1\n5.0,6.0,0\n7.0,8.0,1\n"
    csv_b64 = base64.b64encode(csv_data.encode("utf-8")).decode("ascii")

    # 1. validate_dataset
    val_res = await server.call_tool("validate_dataset", {"format": "csv", "data_base64": csv_b64})
    assert not val_res.is_error
    val_payload = extract_result(val_res)
    assert val_payload["row_count"] == 4
    assert val_payload["column_count"] == 3

    # 1b. validate_dataset with malformed base64
    with pytest.raises(ToolError):
        await server.call_tool(
            "validate_dataset", {"format": "csv", "data_base64": "not-valid-base64!!!"}
        )

    # Create project first
    proj_res = await server.call_tool("create_project", {"name": "Churn Project"})
    proj_id = extract_result(proj_res)["project_id"]

    # 2. register_dataset
    reg_res = await server.call_tool(
        "register_dataset",
        {
            "project_id": proj_id,
            "name": "churn_data",
            "description": "Customer churn dataset",
            "format": "csv",
            "data_base64": csv_b64,
            "version": "1.0",
        },
    )
    assert not reg_res.is_error
    reg_payload = extract_result(reg_res)
    assert reg_payload["dataset_id"] is not None
    assert reg_payload["version_id"] is not None
    assert reg_payload["format"] == "csv"
    dataset_id = reg_payload["dataset_id"]

    # 3. inspect_dataset
    insp_res = await server.call_tool(
        "inspect_dataset",
        {
            "dataset_id": dataset_id,
            "version": "1.0",
            "sample_rows": 2,
        },
    )
    assert not insp_res.is_error
    insp_payload = extract_result(insp_res)
    assert insp_payload["row_count"] == 4
    assert len(insp_payload["sample_rows"]) == 2
    assert "columns" in insp_payload["schema"]


@pytest.mark.asyncio
async def test_experiment_creation_and_listing_tools():
    """Verify create_experiment, get_experiment, list_experiments, and cancel_experiment."""
    server = create_server()

    # Create project first
    proj_res = await server.call_tool("create_project", {"name": "Exp Project"})
    proj_id = extract_result(proj_res)["project_id"]

    # First register dataset
    csv_data = "x1,x2,y\n1.0,2.0,0\n3.0,4.0,1\n5.0,6.0,0\n7.0,8.0,1\n9.0,10.0,0\n"
    csv_b64 = base64.b64encode(csv_data.encode("utf-8")).decode("ascii")
    reg_res = await server.call_tool(
        "register_dataset",
        {
            "project_id": proj_id,
            "name": "exp_dataset",
            "description": "Dataset for experiment creation",
            "format": "csv",
            "data_base64": csv_b64,
        },
    )
    dataset_version_id = extract_result(reg_res)["version_id"]

    # Retrieve a model version ID
    res_versions = await server.call_tool(
        "list_model_versions", {"model_id": "logistic_regression"}
    )
    model_version_id = extract_result(res_versions)[0]["version_id"]

    # 1. create_experiment
    exp_res = await server.call_tool(
        "create_experiment",
        {
            "project_id": proj_id,
            "dataset_version_id": dataset_version_id,
            "model_version_id": model_version_id,
            "task_type": "binary_classification",
            "target_column": "y",
            "primary_metric": "roc_auc",
            "hyperparameters": {"C": 1.0},
        },
    )
    assert not exp_res.is_error
    exp_payload = extract_result(exp_res)
    assert exp_payload["status"] == "QUEUED"
    experiment_id = exp_payload["experiment_id"]

    # 2. get_experiment
    get_res = await server.call_tool("get_experiment", {"experiment_id": experiment_id})
    assert not get_res.is_error
    assert extract_result(get_res)["status"] == "QUEUED"

    # 3. list_experiments
    list_res = await server.call_tool("list_experiments", {"project_id": proj_id})
    assert not list_res.is_error
    experiments = extract_result(list_res)
    assert len(experiments) >= 1
    assert any(e["experiment_id"] == experiment_id for e in experiments)

    # 4. cancel_experiment
    cancel_res = await server.call_tool("cancel_experiment", {"experiment_id": experiment_id})
    assert not cancel_res.is_error
    assert extract_result(cancel_res)["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_create_experiment_column_validation():
    """Verify create_experiment rejects nonexistent target or feature columns upfront."""
    from fastmcp.exceptions import ToolError

    server = create_server()
    proj_res = await server.call_tool("create_project", {"name": "Validation Project"})
    proj_id = extract_result(proj_res)["project_id"]

    csv_data = "feat1,feat2,label\n1.0,2.0,0\n3.0,4.0,1\n"
    csv_b64 = base64.b64encode(csv_data.encode("utf-8")).decode("ascii")
    reg_res = await server.call_tool(
        "register_dataset",
        {
            "project_id": proj_id,
            "name": "val_dataset",
            "description": "Validation test dataset",
            "format": "csv",
            "data_base64": csv_b64,
        },
    )
    dataset_version_id = extract_result(reg_res)["version_id"]

    res_versions = await server.call_tool(
        "list_model_versions", {"model_id": "logistic_regression"}
    )
    model_version_id = extract_result(res_versions)[0]["version_id"]

    # 1. Nonexistent target column
    with pytest.raises(ToolError, match="Target column 'nonexistent_target' not found"):
        await server.call_tool(
            "create_experiment",
            {
                "project_id": proj_id,
                "dataset_version_id": dataset_version_id,
                "model_version_id": model_version_id,
                "task_type": "binary_classification",
                "target_column": "nonexistent_target",
            },
        )

    # 2. Nonexistent feature column
    with pytest.raises(ToolError, match="Feature columns.*not found"):
        await server.call_tool(
            "create_experiment",
            {
                "project_id": proj_id,
                "dataset_version_id": dataset_version_id,
                "model_version_id": model_version_id,
                "task_type": "binary_classification",
                "target_column": "label",
                "feature_columns": ["feat1", "nonexistent_feat"],
            },
        )

    # 3. Target column included in feature columns
    with pytest.raises(ToolError, match="Target column.*cannot be included in feature_columns"):
        await server.call_tool(
            "create_experiment",
            {
                "project_id": proj_id,
                "dataset_version_id": dataset_version_id,
                "model_version_id": model_version_id,
                "task_type": "binary_classification",
                "target_column": "label",
                "feature_columns": ["feat1", "label"],
            },
        )


@pytest.mark.asyncio
async def test_get_experiment_failure_details():
    """Verify get_experiment surfaces error_message and error_type for failed experiments."""
    from ml_mcp.infrastructure.postgres.base import generate_uuid7
    from ml_mcp.infrastructure.postgres.models import ExperimentOrm, ExperimentRunOrm
    from ml_mcp.infrastructure.postgres.session import get_db_manager

    server = create_server()
    exp_id = generate_uuid7()
    run_id = generate_uuid7()

    # Seed an experiment with a failed run directly into DB
    db_mgr = get_db_manager()
    async with db_mgr.session() as sess:
        exp_orm = ExperimentOrm(
            id=exp_id,
            tenant_id="tenant-alpha",
            project_id="default-project",
            dataset_version_id="mock-dsv-id",
            model_version_id="mock-mv-id",
            spec_json={"task_type": "binary_classification"},
            status="FAILED",
            created_by="test-user",
        )
        run_orm = ExperimentRunOrm(
            id=run_id,
            experiment_id=exp_id,
            run_number=1,
            status="FAILED",
            failure_reason="TargetColumnNotFoundError: Target column 'missing' not found in dataset",
        )
        sess.add(exp_orm)
        sess.add(run_orm)
        await sess.commit()

    get_res = await server.call_tool("get_experiment", {"experiment_id": exp_id})
    assert not get_res.is_error
    exp_data = extract_result(get_res)
    assert exp_data["status"] == "FAILED"
    assert "Target column 'missing' not found" in exp_data["error_message"]
    assert exp_data["error_type"] == "TargetColumnNotFoundError"


@pytest.mark.asyncio
async def test_mcp_resources():
    """Verify static and parameterized MCP resource reading."""
    server = create_server()

    # 1. Static resource: modellab://models
    res = await server.read_resource("modellab://models")
    assert len(res.contents) == 1
    models = json.loads(res.contents[0].content)
    assert len(models) == 7

    # 2. Parameterized resource: modellab://models/{model_id}
    res_detail = await server.read_resource("modellab://models/xgboost")
    assert len(res_detail.contents) == 1
    xgb_info = json.loads(res_detail.contents[0].content)
    assert xgb_info["model_id"] == "xgboost"


@pytest.mark.asyncio
async def test_mcp_prompts():
    """Verify rendering of experiment_design and error_diagnosis prompts."""
    server = create_server()

    # 1. experiment_design prompt
    design_prompt = await server.render_prompt(
        "experiment_design",
        {
            "dataset_description": "Financial fraud detection, 50k rows, 1% fraud rate",
            "task_type": "binary_classification",
        },
    )
    text = design_prompt.messages[0].content.text
    assert "Financial fraud detection" in text
    assert "Stratified K-Fold" in text

    # 2. error_diagnosis prompt
    diag_prompt = await server.render_prompt(
        "error_diagnosis",
        {
            "experiment_id": "exp-12345",
            "problem_summary": "Train ROC-AUC 0.99 but Val ROC-AUC 0.52",
        },
    )
    diag_text = diag_prompt.messages[0].content.text
    assert "exp-12345" in diag_text
    assert "generalization gap" in diag_text
