"""Unit and integration tests verifying zero-configuration SQLite and local filesystem fallback."""

from pathlib import Path

import pytest

from ml_mcp.application.datasets.service import DatasetService
from ml_mcp.application.experiments.service import ExperimentService
from ml_mcp.application.models.service import ModelService
from ml_mcp.application.projects.service import ProjectService
from ml_mcp.config import Settings
from ml_mcp.config.settings import get_default_database_url
from ml_mcp.infrastructure.object_storage.s3 import S3StorageService
from ml_mcp.infrastructure.postgres.base import Base
from ml_mcp.infrastructure.postgres.session import DatabaseManager
from ml_mcp.infrastructure.queue.task_queue import RedisTaskQueue
from ml_mcp.infrastructure.redis.client import RedisManager
from ml_mcp.workers.daemon import WorkerDaemon


def test_default_database_url_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that absent DATABASE_URL yields an embedded SQLite URL under ~/.quicklab/."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("QUICKLAB_HOME", str(tmp_path))
    default_url = get_default_database_url()
    assert default_url.startswith("sqlite+aiosqlite:///")
    assert "quicklab.db" in default_url
    assert tmp_path.exists()


def test_empty_string_env_variables_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify that empty string DATABASE_URL and OBJECT_STORE_ENDPOINT_URL fall back cleanly."""
    monkeypatch.setenv("QUICKLAB_HOME", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "  ")
    monkeypatch.setenv("OBJECT_STORE_ENDPOINT_URL", "")

    settings = Settings()
    assert settings.database.url.startswith("sqlite+aiosqlite:///")
    assert "quicklab.db" in settings.database.url

    storage = S3StorageService(settings)
    assert not storage.has_remote_s3


def test_local_storage_fallback_read_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that S3StorageService writes and reads from local filesystem when S3 is unconfigured."""
    monkeypatch.setenv("QUICKLAB_HOME", str(tmp_path))
    settings = Settings()
    settings.object_store.endpoint_url = None

    storage = S3StorageService(settings)
    assert not storage.has_remote_s3

    test_key = "tenants/t1/datasets/d1/1.0/data.parquet"
    test_data = b"PAR1_test_parquet_binary_content"

    uri, content_hash = storage.put_object(test_key, test_data)
    assert uri == "s3://modellab-artifacts/tenants/t1/datasets/d1/1.0/data.parquet"
    assert len(content_hash) == 64

    # Assert physical file exists on local filesystem
    local_file = tmp_path / "artifacts" / test_key
    assert local_file.is_file()
    assert local_file.read_bytes() == test_data

    # Read back through service
    retrieved = storage.get_object(test_key)
    assert retrieved == test_data

    # Head object inspection
    head = storage.head_object(test_key)
    assert head is not None
    assert head["size_bytes"] == len(test_data)

    # Presigned URL generation gives file URI
    presigned = storage.generate_presigned_get_url(test_key)
    assert presigned.startswith("file://")
    assert "data.parquet" in presigned

    # Delete object
    assert storage.delete_object(test_key) is True
    assert not local_file.exists()


@pytest.mark.asyncio
async def test_zero_config_lifespan_and_experiment_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify that an end-to-end training experiment succeeds with zero external services (SQLite + Local Storage + In-Memory Queue)."""
    monkeypatch.setenv("QUICKLAB_HOME", str(tmp_path))
    settings = Settings()
    settings.database.url = f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}"
    settings.object_store.endpoint_url = None
    settings.redis.url = (
        "redis://127.0.0.1:9999/0"  # Invalid redis port to test in-memory queue fallback
    )
    settings.app.env = "testing"

    db_mgr = DatabaseManager(settings)
    db_mgr.initialize(settings.database.url)

    # Auto-create tables for SQLite
    async with db_mgr.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    storage = S3StorageService(settings)
    redis_mgr = RedisManager(settings)
    queue = RedisTaskQueue(redis_mgr, settings)

    daemon = WorkerDaemon(
        task_queue=queue,
        storage_service=storage,
        db_manager=db_mgr,
        drain_outbox=True,
    )

    import asyncio

    daemon_task = asyncio.create_task(daemon.run())

    try:
        async with db_mgr.session() as sess:
            # Seed catalog & default project
            m_svc = ModelService(sess)
            await m_svc.seed_catalog()
            p_svc = ProjectService(sess)
            proj = await p_svc.ensure_default_project("default-tenant")
            project_id = proj["project_id"]

            # Register dataset (stored to local filesystem)
            csv_content = "feat1,feat2,target\n" + "\n".join(
                f"{i * 0.1:.2f},{i * 0.5:.2f},{i % 2}" for i in range(50)
            )
            d_svc = DatasetService(sess, storage)
            ds = await d_svc.register_dataset(
                tenant_id="default-tenant",
                project_id=project_id,
                name="zero-config-ds",
                description="Test dataset",
                data_format="csv",
                raw_bytes=csv_content.encode(),
            )
            version_id = ds["version_id"]

            # Create training experiment
            xgb_versions = await m_svc.list_model_versions("xgboost")
            xgb_ver = xgb_versions[0]["version_id"]

            exp_svc = ExperimentService(sess)
            exp = await exp_svc.create_experiment(
                principal_id="local-user",
                tenant_id="default-tenant",
                project_id=project_id,
                dataset_version_id=version_id,
                model_version_id=xgb_ver,
                task_type="binary_classification",
                target_column="target",
                hyperparameters={"n_estimators": 10, "max_depth": 2},
            )
            exp_id = exp["experiment_id"]

        # Wait for worker daemon to process via in-memory queue
        success = False
        for _ in range(15):
            await asyncio.sleep(0.5)
            async with db_mgr.session() as sess:
                exp_svc = ExperimentService(sess)
                record = await exp_svc.get_experiment("default-tenant", exp_id)
                if record.get("status") == "SUCCEEDED":
                    assert record.get("metrics_count", 0) > 0
                    assert record.get("artifacts_count", 0) > 0
                    success = True
                    break

        assert success, "Experiment failed to complete in zero-config environment"

    finally:
        daemon.stop()
        await daemon_task
        await db_mgr.close()
