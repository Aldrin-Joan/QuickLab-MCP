"""Centralized production configuration using Pydantic Settings."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load local .env if present (explicit environment variables take precedence)
load_dotenv(override=False)


def get_default_database_url() -> str:
    """Return embedded SQLite database URL in user home directory when no DB URL is configured."""
    quicklab_dir = Path(os.environ.get("QUICKLAB_HOME", Path.home() / ".quicklab"))
    quicklab_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{(quicklab_dir / 'quicklab.db').as_posix()}"


class AppSettings(BaseSettings):
    """Application level configuration."""

    model_config = SettingsConfigDict(env_prefix="APP_", extra="ignore")

    name: str = "ml-mcp"
    env: Literal["development", "staging", "production", "testing"] = "development"
    debug: bool = False
    secret_key: SecretStr = Field(
        default=SecretStr("insecure-dev-secret-key-change-in-production-min32bytes"),
        description="Master cryptographic key for token signing or derivation",
    )


class McpSettings(BaseSettings):
    """MCP protocol and transport settings."""

    model_config = SettingsConfigDict(env_prefix="MCP_", extra="ignore")

    server_name: str = "ModelLab-ML-MCP"
    server_version: str = "0.1.0"
    protocol_version: str = "2026-07-28"
    host: str = "127.0.0.1"
    port: int = 8000
    streamable_http_path: str = "/mcp"


class AuthSettings(BaseSettings):
    """Authentication and OIDC configuration."""

    model_config = SettingsConfigDict(env_prefix="AUTH_", extra="ignore")

    enabled: bool = True
    issuer: str = "https://auth.modellab.local"
    audience: str = "modellab-mcp-api"
    algorithms: list[str] = ["RS256", "HS256"]
    secret_key: SecretStr = Field(
        default=SecretStr("dev-jwt-secret-key-min-32-characters-long-production-change"),
        description="Secret key for symmetric JWT algorithms like HS256",
    )
    public_key_pem: str | None = None
    jwks_url: str | None = None


class DatabaseSettings(BaseSettings):
    """PostgreSQL or embedded SQLite database configuration."""

    model_config = SettingsConfigDict(env_prefix="DATABASE_", extra="ignore")

    url: str = Field(default_factory=get_default_database_url)
    pool_size: int = 10
    max_overflow: int = 20
    pool_timeout: float = 30.0
    pool_recycle: int = 1800
    echo: bool = False

    @field_validator("url", mode="after")
    @classmethod
    def ensure_async_driver(cls, v: str) -> str:
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        if v.startswith("sqlite://") and not v.startswith("sqlite+aiosqlite://"):
            return v.replace("sqlite://", "sqlite+aiosqlite://", 1)
        return v


class RedisSettings(BaseSettings):
    """Redis / Valkey configuration for caching, rate limiting, and queues."""

    model_config = SettingsConfigDict(env_prefix="REDIS_", extra="ignore")

    url: str = "redis://localhost:6379/0"
    max_connections: int = 20
    task_queue_name: str = "ml_mcp:tasks:experiments"
    retry_backoff_seconds: float = 2.0


class ObjectStoreSettings(BaseSettings):
    """S3-compatible object storage configuration."""

    model_config = SettingsConfigDict(env_prefix="OBJECT_STORE_", extra="ignore")

    endpoint_url: str | None = None
    access_key_id: SecretStr = Field(default=SecretStr("minioadmin"))
    secret_access_key: SecretStr = Field(default=SecretStr("minioadmin"))
    bucket_name: str = "modellab-artifacts"
    region: str = "us-east-1"
    presigned_ttl_seconds: int = 900


class WorkerSettings(BaseSettings):
    """ML Worker execution policy and resources."""

    model_config = SettingsConfigDict(env_prefix="WORKER_", extra="ignore")

    in_process: bool = True
    max_concurrent_experiments: int = 4
    default_timeout_seconds: int = 1800
    max_memory_mb: int = 4096
    tmp_dir: str = "./tmp/worker"


class RateLimitSettings(BaseSettings):
    """Layered rate limiting thresholds (requests per minute)."""

    model_config = SettingsConfigDict(env_prefix="RATE_LIMIT_", extra="ignore")

    read_per_minute: int = 120
    metadata_per_minute: int = 60
    experiment_per_minute: int = 10
    analysis_per_minute: int = 20


class OtelSettings(BaseSettings):
    """OpenTelemetry configuration."""

    model_config = SettingsConfigDict(env_prefix="OTEL_", extra="ignore")

    enabled: bool = False
    service_name: str = "ml-mcp-service"
    exporter_otlp_endpoint: str = "http://localhost:4317"


class SecuritySettings(BaseSettings):
    """Input limits and security bounds."""

    model_config = SettingsConfigDict(env_prefix="SECURITY_", extra="ignore")

    max_request_body_bytes: int = 10 * 1024 * 1024  # 10 MB
    max_dataset_upload_bytes: int = 100 * 1024 * 1024  # 100 MB
    max_dataset_rows: int = 5_000_000
    max_dataset_columns: int = 1_000


class Settings(BaseSettings):
    """Master aggregated settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app: AppSettings = Field(default_factory=AppSettings)
    mcp: McpSettings = Field(default_factory=McpSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    object_store: ObjectStoreSettings = Field(default_factory=ObjectStoreSettings)
    worker: WorkerSettings = Field(default_factory=WorkerSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    otel: OtelSettings = Field(default_factory=OtelSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)

    @property
    def app_env(self) -> str:
        return self.app.env


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached singleton Settings instance."""
    return Settings()
