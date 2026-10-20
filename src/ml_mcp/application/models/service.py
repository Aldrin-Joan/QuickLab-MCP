"""Model catalog application service."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ml_mcp.application.models.catalog_seed import APPROVED_MODELS
from ml_mcp.domain.errors import (
    ModelNotApprovedError,
    ResourceNotFoundError,
    ResourceVersionRevokedError,
)
from ml_mcp.domain.value_objects import ApprovalStatus
from ml_mcp.infrastructure.postgres.base import generate_uuid7
from ml_mcp.infrastructure.postgres.models import ModelOrm, ModelVersionOrm
from ml_mcp.infrastructure.postgres.repositories.models import ModelRepository


class ModelService:
    """Service managing approved ML model architectures and hyperparameter contracts."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ModelRepository(session)

    async def seed_catalog(self) -> None:
        """Seed all 7 approved tabular models and versions if missing, or synchronize digests."""
        for meta in APPROVED_MODELS:
            existing = await self.repo.get_by_id(meta.model_id)
            if not existing:
                model_orm = ModelOrm(
                    id=meta.model_id,
                    tenant_id="system",
                    name=meta.name,
                    description=meta.description,
                    family=meta.family.value,
                    publisher=meta.publisher,
                )
                await self.repo.create_model(model_orm)
            else:
                existing.name = meta.name
                existing.description = meta.description
                existing.family = meta.family.value

            existing_version = await self.repo.get_version(meta.model_id, meta.version)
            if not existing_version:
                version_orm = ModelVersionOrm(
                    id=generate_uuid7(),
                    model_id=meta.model_id,
                    version=meta.version,
                    container_image_digest=meta.container_image_digest,
                    supported_task_types=[t.value for t in meta.supported_task_types],
                    hyperparameters_schema=[h.model_dump() for h in meta.supported_hyperparameters],
                    approval_status=meta.approval_status.value,
                )
                await self.repo.create_version(version_orm)
            else:
                existing_version.container_image_digest = meta.container_image_digest
                existing_version.supported_task_types = [t.value for t in meta.supported_task_types]
                existing_version.hyperparameters_schema = [
                    h.model_dump() for h in meta.supported_hyperparameters
                ]
                existing_version.approval_status = meta.approval_status.value

    async def list_models(self) -> list[dict[str, Any]]:
        models = await self.repo.list_models()
        return [
            {
                "model_id": m.id,
                "name": m.name,
                "description": m.description,
                "family": m.family,
                "publisher": m.publisher,
                "versions": [
                    v.version
                    for v in m.versions
                    if v.approval_status == ApprovalStatus.APPROVED.value
                ],
            }
            for m in models
        ]

    async def get_model(self, model_id: str) -> dict[str, Any]:
        model = await self.repo.get_by_id(model_id)
        if not model:
            raise ResourceNotFoundError("Model", model_id)

        return {
            "model_id": model.id,
            "name": model.name,
            "description": model.description,
            "family": model.family,
            "publisher": model.publisher,
            "versions": [
                {
                    "version_id": v.id,
                    "version": v.version,
                    "container_image_digest": v.container_image_digest,
                    "supported_task_types": v.supported_task_types,
                    "hyperparameters_schema": v.hyperparameters_schema,
                    "approval_status": v.approval_status,
                }
                for v in model.versions
            ],
        }

    async def list_model_versions(self, model_id: str) -> list[dict[str, Any]]:
        versions = await self.repo.list_versions(model_id)
        if not versions:
            raise ResourceNotFoundError("Model", model_id)

        return [
            {
                "version_id": v.id,
                "version": v.version,
                "approval_status": v.approval_status,
                "supported_task_types": v.supported_task_types,
                "container_image_digest": v.container_image_digest,
            }
            for v in versions
        ]

    async def validate_model_for_experiment(
        self,
        task_type: str | None = None,
        hyperparameters: dict[str, Any] | None = None,
        model_version_id: str | None = None,
        model_id: str | None = None,
        version: str | None = None,
        **kwargs: Any,
    ) -> ModelVersionOrm:
        """Verify model approval, task compatibility, and hyperparameter bounds."""
        resolved_task_type = task_type or kwargs.get("task_type")
        resolved_hypers = (
            hyperparameters if hyperparameters is not None else kwargs.get("hyperparameters", {})
        )
        resolved_model_id = model_id or kwargs.get("model_id")
        resolved_version = version or kwargs.get("version")
        resolved_mv_id = model_version_id or kwargs.get("model_version_id")

        if resolved_mv_id:
            v = await self.repo.get_version_by_id(resolved_mv_id)
        elif resolved_model_id and resolved_version:
            v = await self.repo.get_version(resolved_model_id, resolved_version)
        else:
            raise ResourceNotFoundError(
                "ModelVersion", resolved_mv_id or f"{resolved_model_id}:{resolved_version}"
            )

        if not v:
            raise ResourceNotFoundError(
                "ModelVersion", resolved_mv_id or f"{resolved_model_id}:{resolved_version}"
            )

        mid = resolved_model_id or v.model_id
        ver = resolved_version or v.version

        if v.approval_status == ApprovalStatus.REVOKED.value:
            raise ResourceVersionRevokedError("Model", f"{mid}:{ver}")

        if v.approval_status != ApprovalStatus.APPROVED.value:
            raise ModelNotApprovedError(mid, ver)

        if resolved_task_type and resolved_task_type not in v.supported_task_types:
            raise ValueError(
                f"Model '{mid}' does not support task type '{resolved_task_type}'. Supported: {v.supported_task_types}"
            )

        # Validate hyperparameter constraints
        schema_map = {item["name"]: item for item in v.hyperparameters_schema}
        for param_name, param_val in resolved_hypers.items():
            if param_name not in schema_map:
                continue  # Allow extra params or warn
            constraint = schema_map[param_name]
            # Range validation
            if constraint.get("min_value") is not None and param_val < constraint["min_value"]:
                raise ValueError(
                    f"Hyperparameter '{param_name}' value {param_val} < minimum {constraint['min_value']}"
                )
            if constraint.get("max_value") is not None and param_val > constraint["max_value"]:
                raise ValueError(
                    f"Hyperparameter '{param_name}' value {param_val} > maximum {constraint['max_value']}"
                )
            if constraint.get("allowed_values") and param_val not in constraint["allowed_values"]:
                raise ValueError(
                    f"Hyperparameter '{param_name}' value {param_val} not in allowed: {constraint['allowed_values']}"
                )

        return v
