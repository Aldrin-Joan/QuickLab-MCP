"""Dataset application service managing registration, validation, and inspection."""

import io
from typing import Any

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from ml_mcp.application.datasets.validator import DatasetValidator
from ml_mcp.domain.errors import ResourceNotFoundError
from ml_mcp.domain.value_objects import DatasetFormat
from ml_mcp.infrastructure.object_storage.s3 import S3StorageService, get_storage_service
from ml_mcp.infrastructure.postgres.base import generate_uuid7
from ml_mcp.infrastructure.postgres.models import DatasetOrm, DatasetVersionOrm
from ml_mcp.infrastructure.postgres.repositories.datasets import DatasetRepository


class DatasetService:
    """Application service for dataset lifecycle and profiling."""

    def __init__(
        self,
        session: AsyncSession,
        storage_service: S3StorageService | None = None,
        validator: DatasetValidator | None = None,
    ) -> None:
        self.session = session
        self.repo = DatasetRepository(session)
        self.storage = storage_service or get_storage_service()
        self.validator = validator or DatasetValidator()

    async def register_dataset(
        self,
        tenant_id: str,
        project_id: str,
        name: str,
        description: str,
        data_format: DatasetFormat | str,
        raw_bytes: bytes,
        version: str = "1.0",
    ) -> dict[str, Any]:
        """Validate, store to S3, and register a new dataset version."""
        # 1. Validate & extract schema
        df, parquet_bytes, schema = self.validator.process(raw_bytes, data_format)

        # 2. Check or create parent Dataset record
        dataset_id = generate_uuid7()
        version_id = generate_uuid7()

        dataset_key = self.storage.get_dataset_key(tenant_id, dataset_id, version, "data.parquet")
        storage_uri, _ = self.storage.put_object(
            key=dataset_key,
            data=parquet_bytes,
            content_type="application/vnd.apache.parquet",
            metadata={"row_count": str(schema.row_count), "column_count": str(schema.column_count)},
        )

        # 3. Persist metadata to DB
        fmt = (
            data_format.value
            if isinstance(data_format, DatasetFormat)
            else str(data_format).lower()
        )
        dataset_orm = DatasetOrm(
            id=dataset_id,
            tenant_id=tenant_id,
            project_id=project_id,
            name=name,
            description=description,
            format=fmt,
        )
        await self.repo.create_dataset(dataset_orm)

        version_orm = DatasetVersionOrm(
            id=version_id,
            dataset_id=dataset_id,
            version=version,
            schema_json=schema.model_dump(),
            row_count=schema.row_count,
            column_count=schema.column_count,
            size_bytes=schema.size_bytes,
            content_hash=schema.content_hash,
            storage_uri=storage_uri,
            validation_status="VALIDATED",
        )
        await self.repo.create_version(version_orm)

        return {
            "dataset_id": dataset_id,
            "version_id": version_id,
            "version": version,
            "name": name,
            "format": fmt,
            "row_count": schema.row_count,
            "column_count": schema.column_count,
            "content_hash": schema.content_hash,
            "storage_uri": storage_uri,
        }

    async def validate_only(
        self,
        raw_bytes: bytes,
        data_format: DatasetFormat | str,
    ) -> dict[str, Any]:
        """Validate dataset and extract schema without persisting."""
        _, _, schema = self.validator.process(raw_bytes, data_format)
        return schema.model_dump()

    async def get_dataset(self, tenant_id: str, dataset_id: str) -> dict[str, Any]:
        ds = await self.repo.get_dataset(tenant_id, dataset_id)
        if not ds:
            raise ResourceNotFoundError("Dataset", dataset_id)

        return {
            "dataset_id": ds.id,
            "name": ds.name,
            "description": ds.description,
            "format": ds.format,
            "versions": [
                {
                    "version_id": v.id,
                    "version": v.version,
                    "row_count": v.row_count,
                    "column_count": v.column_count,
                    "content_hash": v.content_hash,
                    "created_at": v.created_at.isoformat(),
                }
                for v in ds.versions
            ],
        }

    async def list_datasets(
        self, tenant_id: str, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        datasets = await self.repo.list_datasets(tenant_id, project_id)
        return [
            {
                "dataset_id": ds.id,
                "project_id": ds.project_id,
                "name": ds.name,
                "format": ds.format,
                "latest_version": ds.versions[0].version if ds.versions else None,
            }
            for ds in datasets
        ]

    async def inspect_dataset(
        self,
        tenant_id: str,
        dataset_id: str,
        version: str = "1.0",
        sample_rows: int = 5,
    ) -> dict[str, Any]:
        """Retrieve dataset schema, summary statistics, and head sample rows."""
        dsv = await self.repo.get_version(tenant_id, dataset_id, version)
        if not dsv:
            raise ResourceNotFoundError("DatasetVersion", f"{dataset_id}:{version}")

        # Download parquet from storage and load sample rows
        key = self.storage.get_dataset_key(tenant_id, dataset_id, version, "data.parquet")
        data = self.storage.get_object(key)
        df = pd.read_parquet(io.BytesIO(data))

        head_df = df.head(sample_rows)
        # Convert to records format with string/native conversion
        sample_data = head_df.to_dict(orient="records")

        # Generate presigned download URL for full dataset access
        download_url = self.storage.generate_presigned_get_url(key, expires_in=900)

        return {
            "dataset_id": dataset_id,
            "version": version,
            "schema": dsv.schema_json,
            "row_count": dsv.row_count,
            "column_count": dsv.column_count,
            "size_bytes": dsv.size_bytes,
            "sample_rows": sample_data,
            "download_url": download_url,
        }
