"""ModelLab FastMCP Server Factory.

Assembles all 21 ML experimentation control plane tools, resources, and prompts
into a production-grade FastMCP 4 server instance.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from fastmcp import FastMCP

from ml_mcp.mcp.prompts import error_diagnosis_prompt, experiment_design_prompt
from ml_mcp.mcp.resources import (
    read_experiment_metrics_resource,
    read_model_details_resource,
    read_models_resource,
)
from ml_mcp.mcp.tools.analysis import (
    handle_analyze_experiment,
    handle_analyze_model_errors,
    handle_check_experiment_validity,
)
from ml_mcp.mcp.tools.datasets import (
    handle_inspect_dataset,
    handle_register_dataset,
    handle_validate_dataset,
)
from ml_mcp.mcp.tools.discovery import (
    handle_get_dataset,
    handle_get_model,
    handle_list_dataset_versions,
    handle_list_datasets,
    handle_list_model_versions,
    handle_list_models,
)
from ml_mcp.mcp.tools.experiments import (
    handle_cancel_experiment,
    handle_create_experiment,
    handle_get_experiment,
    handle_list_experiments,
)
from ml_mcp.mcp.tools.projects import (
    handle_create_project,
    handle_list_projects,
)
from ml_mcp.mcp.tools.results import (
    handle_compare_experiments,
    handle_get_experiment_metrics,
    handle_get_experiment_predictions,
    handle_list_experiment_artifacts,
    handle_read_experiment_artifact,
)
from ml_mcp.server.context import get_current_principal
from ml_mcp.server.middleware import get_security_pipeline

logger = logging.getLogger(__name__)


async def _execute_secured[T](
    tool_name: str,
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    **kwargs: Any,
) -> T:
    """Execute a tool handler with rate limiting, scope checks, and audit logging."""
    principal = get_current_principal()
    pipeline = get_security_pipeline()
    await pipeline.pre_tool_call(tool_name, principal)
    try:
        res = await fn(*args, principal=principal, **kwargs)
        await pipeline.post_tool_call(tool_name, principal, res, success=True)
        return res
    except Exception as exc:
        await pipeline.post_tool_call(
            tool_name,
            principal,
            None,
            success=False,
            error_code=type(exc).__name__,
        )
        from sqlalchemy.exc import DBAPIError, IntegrityError

        from ml_mcp.domain.errors import InvalidInputError, ResourceNotFoundError

        if isinstance(exc, IntegrityError):
            orig_msg = str(getattr(exc, "orig", exc)).lower()
            if "foreign key constraint" in orig_msg:
                if "project_id" in orig_msg or "projects" in orig_msg:
                    raise ResourceNotFoundError("Project", "specified in request") from None
                if "dataset_id" in orig_msg or "dataset_version_id" in orig_msg:
                    raise ResourceNotFoundError("Dataset", "specified in request") from None
                if "model_id" in orig_msg or "model_version_id" in orig_msg:
                    raise ResourceNotFoundError("Model", "specified in request") from None
                raise ResourceNotFoundError("Referenced resource", "specified in request") from None
            if "unique constraint" in orig_msg:
                raise InvalidInputError(
                    "A resource with this name or identifier already exists."
                ) from None
            raise InvalidInputError("Database constraint violation.") from None
        if isinstance(exc, DBAPIError):
            raise InvalidInputError("Database operation failed due to invalid input.") from None
        raise


@contextlib.asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[None]:
    """Lifespan context manager: seed model catalog, ensure default project, and start in-process worker if configured."""
    logger.info("Initializing ModelLab server lifespan...")
    worker_task: asyncio.Task[None] | None = None
    daemon: Any = None
    try:
        from ml_mcp.application.models.service import ModelService
        from ml_mcp.application.projects.service import ProjectService
        from ml_mcp.config import get_settings
        from ml_mcp.infrastructure.postgres.session import get_db_manager

        db_mgr = get_db_manager()
        # Auto-create schema for embedded SQLite if tables do not exist
        if db_mgr.settings.database.url.startswith("sqlite"):
            from ml_mcp.infrastructure.postgres.base import Base

            async with db_mgr.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        async with db_mgr.session() as sess:
            service = ModelService(sess)
            await service.seed_catalog()
            proj_service = ProjectService(sess)
            await proj_service.ensure_default_project("default-tenant")
        logger.info("Model catalog and default project initialized and verified.")

        settings = get_settings()
        if settings.app.env != "testing" and getattr(settings.worker, "in_process", True):
            from ml_mcp.workers.daemon import WorkerDaemon

            daemon = WorkerDaemon(drain_outbox=True)
            worker_task = asyncio.create_task(daemon.run())
            logger.info("In-process ML worker daemon started successfully.")
    except Exception as exc:
        logger.warning("Startup initialization warning: %s", exc)

    try:
        yield
    finally:
        logger.info("Shutting down ModelLab server lifespan...")
        if daemon is not None and worker_task is not None:
            daemon.stop()
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(worker_task, timeout=5.0)
            logger.info("In-process ML worker daemon stopped.")
        logger.info("ModelLab server lifespan shutdown complete.")


def create_server(name: str = "ModelLab") -> FastMCP:
    """Construct and configure the FastMCP server instance with tools, resources, and prompts."""
    server = FastMCP(
        name=name,
        instructions=(
            "ModelLab ML Experimentation Control Plane. "
            "Enables AI agents to discover approved ML architectures, validate tabular datasets, "
            "submit reproducible training experiments, retrieve predictions/metrics, and perform "
            "deep diagnostic analysis."
        ),
        lifespan=app_lifespan,
    )

    # -------------------------------------------------------------------------
    # 0. Project Management Tools
    # -------------------------------------------------------------------------

    @server.tool(
        name="create_project",
        description="Create a new ML experimentation project for organizing datasets and experiments.",
    )
    async def create_project(
        name: str,
        description: str = "",
    ) -> dict[str, Any]:
        """Create a new project."""
        return await _execute_secured(
            "create_project",
            handle_create_project,
            name=name,
            description=description,
        )

    @server.tool(
        name="list_projects",
        description="List all ML experimentation projects for the current tenant.",
    )
    async def list_projects(
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List all projects."""
        return await _execute_secured(
            "list_projects",
            handle_list_projects,
            limit=limit,
            offset=offset,
        )

    # -------------------------------------------------------------------------
    # 1. Discovery Tools
    # -------------------------------------------------------------------------

    @server.tool(
        name="list_models",
        description="List all approved tabular ML models in the catalog.",
    )
    async def list_models() -> list[dict[str, Any]]:
        return await _execute_secured("list_models", handle_list_models)

    @server.tool(
        name="get_model",
        description="Get detailed specification, bounds, and hyperparameter schema for a model.",
    )
    async def get_model(model_id: str) -> dict[str, Any]:
        return await _execute_secured("get_model", handle_get_model, model_id)

    @server.tool(
        name="list_model_versions",
        description="List all registered versions for a specific model family.",
    )
    async def list_model_versions(model_id: str) -> list[dict[str, Any]]:
        return await _execute_secured("list_model_versions", handle_list_model_versions, model_id)

    @server.tool(
        name="list_datasets",
        description="List datasets available for the caller's tenant and project.",
    )
    async def list_datasets(project_id: str | None = None) -> list[dict[str, Any]]:
        return await _execute_secured("list_datasets", handle_list_datasets, project_id=project_id)

    @server.tool(
        name="get_dataset",
        description="Get metadata, schema, and version lineage for a specific dataset.",
    )
    async def get_dataset(dataset_id: str) -> dict[str, Any]:
        return await _execute_secured("get_dataset", handle_get_dataset, dataset_id)

    @server.tool(
        name="list_dataset_versions",
        description="List immutable versions for a specific dataset.",
    )
    async def list_dataset_versions(dataset_id: str) -> list[dict[str, Any]]:
        return await _execute_secured(
            "list_dataset_versions", handle_list_dataset_versions, dataset_id
        )

    # -------------------------------------------------------------------------
    # 2. Dataset Management & Validation Tools
    # -------------------------------------------------------------------------

    @server.tool(
        name="register_dataset",
        description="Register a new tabular dataset version with validation and SHA-256 hash.",
    )
    async def register_dataset(
        project_id: str,
        name: str,
        description: str,
        format: str,
        data_base64: str,
        version: str = "1.0",
    ) -> dict[str, Any]:
        return await _execute_secured(
            "register_dataset",
            handle_register_dataset,
            project_id=project_id,
            name=name,
            description=description,
            format=format,
            data_base64=data_base64,
            version=version,
        )

    @server.tool(
        name="validate_dataset",
        description="Validate raw dataset bytes against tabular constraints without persisting.",
    )
    async def validate_dataset(format: str, data_base64: str) -> dict[str, Any]:
        return await _execute_secured(
            "validate_dataset",
            handle_validate_dataset,
            format=format,
            data_base64=data_base64,
        )

    @server.tool(
        name="inspect_dataset",
        description="Inspect dataset schema, data types, null counts, and preview rows.",
    )
    async def inspect_dataset(
        dataset_id: str,
        version: str = "1.0",
        sample_rows: int = 5,
    ) -> dict[str, Any]:
        return await _execute_secured(
            "inspect_dataset",
            handle_inspect_dataset,
            dataset_id=dataset_id,
            version=version,
            sample_rows=sample_rows,
        )

    # -------------------------------------------------------------------------
    # 3. Experimentation Tools
    # -------------------------------------------------------------------------

    @server.tool(
        name="create_experiment",
        description="Create and queue an ML experiment for asynchronous training execution.",
    )
    async def create_experiment(
        project_id: str,
        dataset_version_id: str,
        model_version_id: str,
        task_type: str,
        target_column: str,
        feature_columns: list[str] | None = None,
        primary_metric: str = "roc_auc",
        additional_metrics: list[str] | None = None,
        hyperparameters: dict[str, Any] | None = None,
        random_seed: int = 42,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return await _execute_secured(
            "create_experiment",
            handle_create_experiment,
            project_id=project_id,
            dataset_version_id=dataset_version_id,
            model_version_id=model_version_id,
            task_type=task_type,
            target_column=target_column,
            feature_columns=feature_columns,
            primary_metric=primary_metric,
            additional_metrics=additional_metrics,
            hyperparameters=hyperparameters,
            random_seed=random_seed,
            idempotency_key=idempotency_key,
        )

    @server.tool(
        name="get_experiment",
        description="Get current status, execution logs, timing, and error details (if failed) of an experiment.",
    )
    async def get_experiment(experiment_id: str) -> dict[str, Any]:
        return await _execute_secured("get_experiment", handle_get_experiment, experiment_id)

    @server.tool(
        name="cancel_experiment",
        description="Request graceful cancellation of an active or queued experiment.",
    )
    async def cancel_experiment(experiment_id: str) -> dict[str, Any]:
        return await _execute_secured("cancel_experiment", handle_cancel_experiment, experiment_id)

    @server.tool(
        name="list_experiments",
        description="List experiments within a project with optional status filter.",
    )
    async def list_experiments(
        project_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return await _execute_secured(
            "list_experiments",
            handle_list_experiments,
            project_id=project_id,
            status=status,
            limit=limit,
            offset=offset,
        )

    # -------------------------------------------------------------------------
    # 4. Results & Artifacts Tools
    # -------------------------------------------------------------------------

    @server.tool(
        name="get_experiment_metrics",
        description="Retrieve all computed evaluation metrics across train/validation splits.",
    )
    async def get_experiment_metrics(experiment_id: str) -> dict[str, Any]:
        return await _execute_secured(
            "get_experiment_metrics", handle_get_experiment_metrics, experiment_id
        )

    @server.tool(
        name="get_experiment_predictions",
        description="Retrieve sample predictions with ground truth, predicted values, and probabilities.",
    )
    async def get_experiment_predictions(experiment_id: str, limit: int = 100) -> dict[str, Any]:
        return await _execute_secured(
            "get_experiment_predictions",
            handle_get_experiment_predictions,
            experiment_id=experiment_id,
            limit=limit,
        )

    @server.tool(
        name="list_experiment_artifacts",
        description="List all persisted artifacts (model weights, preprocessor, metrics) for an experiment.",
    )
    async def list_experiment_artifacts(experiment_id: str) -> list[dict[str, Any]]:
        return await _execute_secured(
            "list_experiment_artifacts", handle_list_experiment_artifacts, experiment_id
        )

    @server.tool(
        name="read_experiment_artifact",
        description="Get artifact metadata and presigned download URL.",
    )
    async def read_experiment_artifact(artifact_id: str) -> dict[str, Any]:
        return await _execute_secured(
            "read_experiment_artifact", handle_read_experiment_artifact, artifact_id
        )

    @server.tool(
        name="compare_experiments",
        description="Compare hyperparameters and performance metrics across multiple experiments.",
    )
    async def compare_experiments(experiment_ids: list[str]) -> dict[str, Any]:
        return await _execute_secured(
            "compare_experiments", handle_compare_experiments, experiment_ids
        )

    # -------------------------------------------------------------------------
    # 5. Diagnostic Analysis Tools
    # -------------------------------------------------------------------------

    @server.tool(
        name="analyze_experiment",
        description="Run automated diagnostic checks for overfitting, data leakage, and training health.",
    )
    async def analyze_experiment(experiment_id: str) -> dict[str, Any]:
        return await _execute_secured(
            "analyze_experiment", handle_analyze_experiment, experiment_id
        )

    @server.tool(
        name="analyze_model_errors",
        description="Perform deep error slice analysis (residuals, false positives/negatives, error patterns).",
    )
    async def analyze_model_errors(experiment_id: str) -> dict[str, Any]:
        return await _execute_secured(
            "analyze_model_errors", handle_analyze_model_errors, experiment_id
        )

    @server.tool(
        name="check_experiment_validity",
        description="Perform automated verification of experiment reproducibility, hashes, and validity.",
    )
    async def check_experiment_validity(experiment_id: str) -> dict[str, Any]:
        return await _execute_secured(
            "check_experiment_validity", handle_check_experiment_validity, experiment_id
        )

    # -------------------------------------------------------------------------
    # MCP Resources
    # -------------------------------------------------------------------------

    @server.resource("modellab://models")
    async def res_models() -> str:
        """Read full catalog of approved models."""
        return await read_models_resource()

    @server.resource("modellab://models/{model_id}")
    async def res_model_details(model_id: str) -> str:
        """Read detailed specification of a model."""
        return await read_model_details_resource(model_id)

    @server.resource("modellab://experiments/{experiment_id}/metrics")
    async def res_experiment_metrics(experiment_id: str) -> str:
        """Read metrics for an experiment."""
        principal = get_current_principal()
        return await read_experiment_metrics_resource(experiment_id, tenant_id=principal.tenant_id)

    # -------------------------------------------------------------------------
    # MCP Prompts
    # -------------------------------------------------------------------------

    @server.prompt("experiment_design")
    def prompt_experiment_design(dataset_description: str, task_type: str) -> str:
        """Prompt guiding agents to design high-yield ML experiments."""
        return experiment_design_prompt(dataset_description, task_type)

    @server.prompt("error_diagnosis")
    def prompt_error_diagnosis(experiment_id: str, problem_summary: str) -> str:
        """Prompt assisting agents in diagnosing training regressions or anomalies."""
        return error_diagnosis_prompt(experiment_id, problem_summary)

    return server
