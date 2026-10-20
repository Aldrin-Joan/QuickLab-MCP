"""Initial production schema migration.

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-09-19 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Tenants
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 2. Principals
    op.create_table(
        "principals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("username", sa.String(128), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, default="viewer"),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "username", name="uq_principals_tenant_username"),
    )
    op.create_index("ix_principals_tenant_created", "principals", ["tenant_id", "created_at"])

    # 3. Projects
    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_projects_tenant_name"),
    )
    op.create_index("ix_projects_tenant_created", "projects", ["tenant_id", "created_at"])

    # 4. Models
    op.create_table(
        "models",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, default="system"),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, default=""),
        sa.Column("family", sa.String(64), nullable=False),
        sa.Column("publisher", sa.String(128), nullable=False, default="ModelLab"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_models_tenant_created", "models", ["tenant_id", "created_at"])

    # 5. Model Versions
    op.create_table(
        "model_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "model_id",
            sa.String(64),
            sa.ForeignKey("models.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("container_image_digest", sa.String(128), nullable=False),
        sa.Column("artifact_digest", sa.String(128), nullable=True),
        sa.Column("supported_task_types", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "hyperparameters_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("approval_status", sa.String(32), nullable=False, default="APPROVED"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("model_id", "version", name="uq_model_versions_model_version"),
    )
    op.create_index("ix_model_versions_created", "model_versions", ["created_at"])

    # 6. Datasets
    op.create_table(
        "datasets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, default=""),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "name", name="uq_datasets_project_name"),
    )
    op.create_index("ix_datasets_tenant_created", "datasets", ["tenant_id", "created_at"])
    op.create_index("ix_datasets_project_created", "datasets", ["project_id", "created_at"])

    # 7. Dataset Versions
    op.create_table(
        "dataset_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "dataset_id",
            sa.String(36),
            sa.ForeignKey("datasets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("schema_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("column_count", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.Column("storage_uri", sa.String(512), nullable=False),
        sa.Column("validation_status", sa.String(32), nullable=False, default="VALIDATED"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("dataset_id", "version", name="uq_dataset_versions_dataset_version"),
    )
    op.create_index("ix_dataset_versions_hash", "dataset_versions", ["content_hash"])

    # 8. Experiments
    op.create_table(
        "experiments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dataset_version_id",
            sa.String(36),
            sa.ForeignKey("dataset_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "model_version_id", sa.String(36), sa.ForeignKey("model_versions.id"), nullable=False
        ),
        sa.Column("spec_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, default="CREATED"),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_experiments_tenant_created", "experiments", ["tenant_id", "created_at"])
    op.create_index("ix_experiments_project_created", "experiments", ["project_id", "created_at"])
    op.create_index("ix_experiments_status_created", "experiments", ["status", "created_at"])
    op.create_index(
        "ix_experiments_idempotency", "experiments", ["tenant_id", "project_id", "idempotency_key"]
    )

    # 9. Experiment Runs
    op.create_table(
        "experiment_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "experiment_id",
            sa.String(36),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_number", sa.Integer(), nullable=False, default=1),
        sa.Column("status", sa.String(32), nullable=False, default="QUEUED"),
        sa.Column("worker_id", sa.String(128), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_experiment_runs_experiment", "experiment_runs", ["experiment_id"])

    # 10. Metrics
    op.create_table(
        "metrics",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "experiment_id",
            sa.String(36),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("experiment_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("metric_name", sa.String(64), nullable=False),
        sa.Column("metric_value", sa.Float(), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False, default=0),
        sa.Column("split", sa.String(32), nullable=False, default="validation"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_metrics_experiment_name", "metrics", ["experiment_id", "metric_name"])
    op.create_index("ix_metrics_run", "metrics", ["run_id"])

    # 11. Analysis Runs
    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "experiment_id",
            sa.String(36),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("diagnostics_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_analysis_tenant_created", "analysis_runs", ["tenant_id", "created_at"])

    # 12. Artifacts
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column(
            "experiment_id",
            sa.String(36),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("artifact_type", sa.String(64), nullable=False),
        sa.Column("storage_uri", sa.String(512), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_artifacts_experiment", "artifacts", ["experiment_id"])
    op.create_index("ix_artifacts_tenant_created", "artifacts", ["tenant_id", "created_at"])

    # 13. Outbox Events
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("aggregate_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.String(64), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, default="PENDING"),
        sa.Column("retry_count", sa.Integer(), nullable=False, default=0),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_outbox_status_scheduled", "outbox_events", ["status", "scheduled_at"])

    # 14. Audit Events
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("principal_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("result", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("client_ip", sa.String(64), nullable=True),
    )
    op.create_index("ix_audit_tenant_timestamp", "audit_events", ["tenant_id", "timestamp"])
    op.create_index("ix_audit_principal_timestamp", "audit_events", ["principal_id", "timestamp"])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("outbox_events")
    op.drop_table("artifacts")
    op.drop_table("analysis_runs")
    op.drop_table("metrics")
    op.drop_table("experiment_runs")
    op.drop_table("experiments")
    op.drop_table("dataset_versions")
    op.drop_table("datasets")
    op.drop_table("model_versions")
    op.drop_table("models")
    op.drop_table("projects")
    op.drop_table("principals")
    op.drop_table("tenants")
