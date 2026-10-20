"""Experiment application service managing lifecycle, validation, idempotency, and outbox publication."""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ml_mcp.application.models.service import ModelService
from ml_mcp.domain.errors import (
    ExperimentNotCancellableError,
    InvalidExperimentError,
    ResourceNotFoundError,
)
from ml_mcp.domain.value_objects import (
    EvaluationConfig,
    ExperimentSpec,
    ResourcePolicy,
    TaskType,
)
from ml_mcp.infrastructure.postgres.base import generate_uuid7
from ml_mcp.infrastructure.postgres.models import ExperimentOrm, OutboxEventOrm
from ml_mcp.infrastructure.postgres.repositories.datasets import DatasetRepository
from ml_mcp.infrastructure.postgres.repositories.experiments import ExperimentRepository

logger = logging.getLogger(__name__)


class ExperimentService:
    """Encapsulates experiment creation, validation, querying, cancellation, and outbox emission."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ExperimentRepository(session)
        self.model_svc = ModelService(session)
        self.dataset_repo = DatasetRepository(session)

    async def create_experiment(
        self,
        tenant_id: str,
        project_id: str,
        dataset_version_id: str,
        model_version_id: str,
        task_type: str,
        target_column: str,
        principal_id: str,
        feature_columns: list[str] | None = None,
        primary_metric: str = "roc_auc",
        additional_metrics: list[str] | None = None,
        hyperparameters: dict[str, Any] | None = None,
        random_seed: int = 42,
        idempotency_key: str | None = None,
        resource_policy: ResourcePolicy | None = None,
    ) -> dict[str, Any]:
        """Validate model approval, create experiment record and transactional outbox event."""
        # 1. Enforce Model Approval and bounds validation
        await self.model_svc.validate_model_for_experiment(
            task_type=task_type,
            hyperparameters=hyperparameters or {},
            model_version_id=model_version_id,
        )

        # 2. Validate Dataset Version and Column Existence upfront
        dsv = await self.dataset_repo.get_version_by_id(tenant_id, dataset_version_id)
        if not dsv:
            raise ResourceNotFoundError("DatasetVersion", dataset_version_id)

        available_columns = [
            col["name"]
            for col in dsv.schema_json.get("columns", [])
            if isinstance(col, dict) and "name" in col
        ]
        if available_columns:
            if target_column not in available_columns:
                raise InvalidExperimentError(
                    f"Target column '{target_column}' not found in dataset '{dsv.dataset_id}' version '{dsv.version}'. Available columns: {available_columns}"
                )
            if feature_columns is not None:
                if len(feature_columns) == 0:
                    raise InvalidExperimentError("feature_columns cannot be empty when specified")
                missing_features = [c for c in feature_columns if c not in available_columns]
                if missing_features:
                    raise InvalidExperimentError(
                        f"Feature columns {missing_features} not found in dataset '{dsv.dataset_id}' version '{dsv.version}'. Available columns: {available_columns}"
                    )
                if target_column in feature_columns:
                    raise InvalidExperimentError(
                        f"Target column '{target_column}' cannot be included in feature_columns"
                    )

        # 3. Check Idempotency
        if idempotency_key:
            existing = await self.repo.get_by_idempotency_key(
                tenant_id, project_id, idempotency_key
            )
            if existing:
                return {
                    "experiment_id": existing.id,
                    "status": existing.status,
                    "idempotent_replay": True,
                    "created_at": existing.created_at.isoformat(),
                }

        # 3. Build ExperimentSpec
        spec = ExperimentSpec(
            tenant_id=tenant_id,
            project_id=project_id,
            dataset_version_id=dataset_version_id,
            model_version_id=model_version_id,
            task_type=TaskType(task_type),
            target_column=target_column,
            feature_columns=feature_columns,
            evaluation_config=EvaluationConfig(
                primary_metric=primary_metric,
                additional_metrics=additional_metrics or [],
            ),
            hyperparameters=hyperparameters or {},
            resource_policy=resource_policy or ResourcePolicy(),
            random_seed=random_seed,
            idempotency_key=idempotency_key,
            created_by=principal_id,
        )

        # 4. Create ORM entities
        experiment_id = generate_uuid7()
        exp_orm = ExperimentOrm(
            id=experiment_id,
            tenant_id=tenant_id,
            project_id=project_id,
            dataset_version_id=dataset_version_id,
            model_version_id=model_version_id,
            spec_json=spec.model_dump(),
            status="QUEUED",
            idempotency_key=idempotency_key,
            created_by=principal_id,
        )

        outbox_event = OutboxEventOrm(
            id=generate_uuid7(),
            event_type="EXPERIMENT_SUBMITTED",
            aggregate_type="EXPERIMENT",
            aggregate_id=experiment_id,
            payload_json={
                "experiment_id": experiment_id,
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        )

        await self.repo.create_experiment(exp_orm, initial_outbox_event=outbox_event)

        # 5. Direct enqueue to Redis for immediate execution
        try:
            from ml_mcp.infrastructure.queue.task_queue import RedisTaskQueue

            queue = RedisTaskQueue()
            await queue.enqueue(
                experiment_id=experiment_id,
                tenant_id=tenant_id,
                payload={
                    "experiment_id": experiment_id,
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                },
            )
        except Exception as q_exc:
            logger.debug("Direct Redis enqueue deferred to outbox processor: %s", q_exc)

        return {
            "experiment_id": experiment_id,
            "status": "QUEUED",
            "idempotent_replay": False,
            "task_type": task_type,
            "target_column": target_column,
            "created_at": exp_orm.created_at.isoformat(),
        }

    async def get_experiment(self, tenant_id: str, experiment_id: str) -> dict[str, Any]:
        """Fetch experiment by tenant-scoped ID with full run details."""
        exp = await self.repo.get_experiment(tenant_id, experiment_id)
        if not exp:
            raise ResourceNotFoundError("Experiment", experiment_id)

        sorted_runs = sorted(
            exp.runs, key=lambda r: (r.run_number or 0, r.created_at), reverse=True
        )
        latest_run = sorted_runs[0] if sorted_runs else None

        error_message = (
            latest_run.failure_reason if latest_run and latest_run.failure_reason else None
        )
        error_type = None
        if error_message:
            if ":" in error_message:
                error_type = error_message.split(":")[0].strip()
            else:
                error_type = "ExecutionError"

        return {
            "experiment_id": exp.id,
            "project_id": exp.project_id,
            "status": exp.status,
            "spec": exp.spec_json,
            "runs_count": len(exp.runs),
            "latest_run_status": latest_run.status if latest_run else None,
            "error_message": error_message,
            "error_type": error_type,
            "metrics_count": len(exp.metrics),
            "artifacts_count": len(exp.artifacts),
            "created_at": exp.created_at.isoformat(),
            "updated_at": exp.updated_at.isoformat(),
        }

    async def cancel_experiment(self, tenant_id: str, experiment_id: str) -> dict[str, Any]:
        """Cancel an in-flight or queued experiment."""
        exp = await self.repo.get_experiment(tenant_id, experiment_id)
        if not exp:
            raise ResourceNotFoundError("Experiment", experiment_id)

        if exp.status in ("SUCCEEDED", "FAILED", "CANCELLED"):
            raise ExperimentNotCancellableError(experiment_id, exp.status)

        await self.repo.update_status(tenant_id, experiment_id, "CANCELLED")
        return {
            "experiment_id": experiment_id,
            "status": "CANCELLED",
            "message": f"Experiment {experiment_id} successfully cancelled.",
        }

    async def list_experiments(
        self,
        tenant_id: str,
        project_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List experiments for tenant."""
        experiments = await self.repo.list_experiments(
            tenant_id=tenant_id,
            project_id=project_id,
            status=status,
            limit=limit,
            offset=offset,
        )
        return [
            {
                "experiment_id": e.id,
                "project_id": e.project_id,
                "status": e.status,
                "primary_metric": e.spec_json.get("evaluation_config", {}).get("primary_metric"),
                "created_at": e.created_at.isoformat(),
            }
            for e in experiments
        ]
