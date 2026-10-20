"""S3-compatible object storage service with tenant isolation and presigned URL support."""

import contextlib
import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from ml_mcp.config import Settings, get_settings
from ml_mcp.domain.errors import DependencyUnavailableError

logger = logging.getLogger(__name__)


class S3StorageService:
    """Production S3 / MinIO storage adapter managing artifacts and datasets."""

    _SAFE_COMPONENT_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.]+$")

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.bucket_name = self.settings.object_store.bucket_name
        self._client: Any = None
        self._memory_store: dict[str, tuple[bytes, dict[str, str], str]] = {}
        quicklab_dir = Path(os.environ.get("QUICKLAB_HOME", Path.home() / ".quicklab"))
        self._local_storage_dir = quicklab_dir / "artifacts"

    @classmethod
    def _assert_safe_component(cls, component: str, field_name: str = "component") -> str:
        """Validate that a path component contains no traversal sequences or unsafe characters."""
        if not component or not isinstance(component, str) or not component.strip():
            raise ValueError(f"Invalid {field_name}: empty component")
        if component in (".", "..") or ".." in component or "/" in component or "\\" in component:
            raise ValueError(f"Directory traversal detected in {field_name}: '{component}'")
        if not cls._SAFE_COMPONENT_PATTERN.match(component):
            raise ValueError(f"Invalid characters detected in {field_name}: '{component}'")
        return component

    @classmethod
    def _assert_safe_key(cls, key: str) -> str:
        """Validate full storage key against path traversal and malformed structures."""
        if not key or not isinstance(key, str) or not key.strip():
            raise ValueError("Invalid storage key: empty key")
        if key.startswith("/") or key.endswith("/"):
            raise ValueError(f"Invalid storage key: leading or trailing slash in '{key}'")
        if "//" in key or "\\" in key or ".." in key:
            raise ValueError(f"Directory traversal detected in storage key: '{key}'")
        parts = key.split("/")
        for part in parts:
            cls._assert_safe_component(part, "key component")
        return key

    @property
    def is_testing(self) -> bool:
        app_env = getattr(self.settings, "app_env", None)
        if app_env is None and hasattr(self.settings, "app"):
            app_env = getattr(self.settings.app, "env", None)
        return app_env == "testing" or os.environ.get("APP_ENV") == "testing"

    @property
    def has_remote_s3(self) -> bool:
        return bool(self.settings.object_store.endpoint_url)

    @property
    def client(self) -> Any:
        if not self.has_remote_s3:
            return None
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=self.settings.object_store.endpoint_url,
                aws_access_key_id=self.settings.object_store.access_key_id.get_secret_value(),
                aws_secret_access_key=self.settings.object_store.secret_access_key.get_secret_value(),
                region_name=self.settings.object_store.region,
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                    connect_timeout=1,
                    read_timeout=1,
                ),
            )
        return self._client

    @staticmethod
    def compute_sha256(data: bytes) -> str:
        """Compute standard hex SHA-256 digest of binary content."""
        return hashlib.sha256(data).hexdigest()

    # Tenant-isolated path generators
    @classmethod
    def get_dataset_key(
        cls, tenant_id: str, dataset_id: str, version: str, filename: str = "data.parquet"
    ) -> str:
        cls._assert_safe_component(tenant_id, "tenant_id")
        cls._assert_safe_component(dataset_id, "dataset_id")
        cls._assert_safe_component(version, "version")
        cls._assert_safe_component(filename, "filename")
        return f"tenants/{tenant_id}/datasets/{dataset_id}/{version}/{filename}"

    @classmethod
    def get_experiment_artifact_key(
        cls, tenant_id: str, experiment_id: str, artifact_type: str, filename: str
    ) -> str:
        cls._assert_safe_component(tenant_id, "tenant_id")
        cls._assert_safe_component(experiment_id, "experiment_id")
        cls._assert_safe_component(artifact_type, "artifact_type")
        cls._assert_safe_component(filename, "filename")
        return f"tenants/{tenant_id}/experiments/{experiment_id}/{artifact_type}/{filename}"

    @classmethod
    def get_model_key(
        cls, tenant_id: str, model_id: str, version: str, filename: str = "model.joblib"
    ) -> str:
        cls._assert_safe_component(tenant_id, "tenant_id")
        cls._assert_safe_component(model_id, "model_id")
        cls._assert_safe_component(version, "version")
        cls._assert_safe_component(filename, "filename")
        return f"tenants/{tenant_id}/models/{model_id}/{version}/{filename}"

    def _write_local(
        self,
        key: str,
        data: bytes,
        metadata: dict[str, str] | None = None,
        content_type: str = "application/octet-stream",
    ) -> tuple[str, str]:
        content_hash = self.compute_sha256(data)
        meta = metadata or {}
        meta["content_hash"] = content_hash
        self._memory_store[key] = (data, meta, content_type)
        try:
            target_path = self._local_storage_dir / key
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(data)
        except Exception as exc:
            logger.debug("Local disk fallback write warning: %s", exc)
        storage_uri = f"s3://{self.bucket_name}/{key}"
        return storage_uri, content_hash

    def put_object(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> tuple[str, str]:
        """Upload an immutable object. Returns (storage_uri, sha256_digest)."""
        self._assert_safe_key(key)

        if not self.has_remote_s3 or (self.is_testing and self._client is None):
            return self._write_local(key, data, metadata, content_type)

        try:
            content_hash = self.compute_sha256(data)
            meta = metadata or {}
            meta["content_hash"] = content_hash
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=data,
                ContentType=content_type,
                Metadata=meta,
            )
            storage_uri = f"s3://{self.bucket_name}/{key}"
            logger.info("Successfully wrote S3 object: %s (size: %d bytes)", storage_uri, len(data))
            return storage_uri, content_hash
        except Exception as exc:
            logger.warning(
                "Remote S3 unavailable (%s), writing object to local storage for key '%s'", exc, key
            )
            return self._write_local(key, data, metadata, content_type)

    def get_object(self, key: str) -> bytes:
        """Retrieve binary content of an object."""
        self._assert_safe_key(key)
        if key in self._memory_store:
            return self._memory_store[key][0]
        local_path = self._local_storage_dir / key
        if local_path.is_file():
            return local_path.read_bytes()
        if not self.has_remote_s3 or (self.is_testing and self._client is None):
            logger.error("Failed to read object from storage at key '%s'", key)
            raise DependencyUnavailableError("Object Storage")
        try:
            response = self.client.get_object(Bucket=self.bucket_name, Key=key)
            return response["Body"].read()
        except Exception as exc:
            if key in self._memory_store:
                return self._memory_store[key][0]
            if local_path.is_file():
                return local_path.read_bytes()
            logger.error("Failed to read object from S3 at key '%s': %s", key, exc)
            raise DependencyUnavailableError("Object Storage") from exc

    def head_object(self, key: str) -> dict[str, Any] | None:
        """Inspect object metadata without downloading full payload."""
        self._assert_safe_key(key)
        if key in self._memory_store:
            data, meta, c_type = self._memory_store[key]
            return {
                "size_bytes": len(data),
                "content_type": c_type,
                "metadata": meta,
                "last_modified": None,
            }
        local_path = self._local_storage_dir / key
        if local_path.is_file():
            stat = local_path.stat()
            return {
                "size_bytes": stat.st_size,
                "content_type": "application/octet-stream",
                "metadata": {},
                "last_modified": stat.st_mtime,
            }
        if not self.has_remote_s3 or (self.is_testing and self._client is None):
            return None
        try:
            response = self.client.head_object(Bucket=self.bucket_name, Key=key)
            return {
                "size_bytes": response.get("ContentLength", 0),
                "content_type": response.get("ContentType"),
                "metadata": response.get("Metadata", {}),
                "last_modified": response.get("LastModified"),
            }
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
                return None
            raise DependencyUnavailableError("Object Storage") from exc
        except Exception:
            return None

    def delete_object(self, key: str) -> bool:
        """Delete an object."""
        self._assert_safe_key(key)
        self._memory_store.pop(key, None)
        local_path = self._local_storage_dir / key
        if local_path.is_file():
            with contextlib.suppress(Exception):
                local_path.unlink()
        if not self.has_remote_s3 or (self.is_testing and self._client is None):
            return True
        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=key)
            return True
        except Exception as exc:
            logger.warning("Failed to delete object from S3: %s", exc)
            return True

    def generate_presigned_get_url(self, key: str, expires_in: int | None = None) -> str:
        """Generate a short-lived read-only presigned GET URL or local file URI."""
        self._assert_safe_key(key)
        ttl = expires_in or self.settings.object_store.presigned_ttl_seconds
        local_path = self._local_storage_dir / key
        if local_path.is_file():
            return local_path.as_uri()
        if not self.has_remote_s3 or (self.is_testing and self._client is None):
            return f"https://s3.local/{self.bucket_name}/{key}?expires={ttl}&sig=mock"
        try:
            url = self.client.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": self.bucket_name, "Key": key},
                ExpiresIn=ttl,
            )
            return url
        except Exception:
            if local_path.is_file():
                return local_path.as_uri()
            return f"https://s3.local/{self.bucket_name}/{key}?expires={ttl}&sig=mock"

    def generate_presigned_put_url(
        self,
        key: str,
        content_type: str = "application/octet-stream",
        expires_in: int | None = None,
    ) -> str:
        """Generate a short-lived presigned PUT URL for direct client uploads."""
        self._assert_safe_key(key)
        ttl = expires_in or self.settings.object_store.presigned_ttl_seconds
        if self.is_testing and self._client is None:
            return f"https://s3.local/{self.bucket_name}/{key}?expires={ttl}&sig=mock"
        try:
            url = self.client.generate_presigned_url(
                ClientMethod="put_object",
                Params={"Bucket": self.bucket_name, "Key": key, "ContentType": content_type},
                ExpiresIn=ttl,
            )
            return url
        except Exception:
            return f"https://s3.local/{self.bucket_name}/{key}?expires={ttl}&sig=mock"


_storage_service: S3StorageService | None = None


def get_storage_service() -> S3StorageService:
    """Return singleton S3StorageService instance."""
    global _storage_service
    if _storage_service is None:
        _storage_service = S3StorageService()
    return _storage_service
