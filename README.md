<p align="center">
  <img src="assets/quicklab-social-banner.png" alt="QuickLab-MCP — Production Tabular ML Experimentation Control Plane for AI Agents" width="100%">
</p>

# QuickLab MCP

[![Python Version](https://img.shields.io/badge/python-3.13%2B-blue.svg)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/badge/pypi-QuickLab--MCP-blue.svg)](https://pypi.org/project/QuickLab-MCP/)
[![MCP Protocol](https://img.shields.io/badge/MCP%20Protocol-2026--07--28-orange.svg)](https://modelcontextprotocol.io/)
[![FastMCP](https://img.shields.io/badge/FastMCP-4.0.5%20GA-green.svg)](https://github.com/jlowin/fastmcp)
[![Tests](https://img.shields.io/badge/tests-108%20passed%20%7C%20100%25-brightgreen.svg)]()
[![Code Style](https://img.shields.io/badge/code%20style-ruff-black.svg)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Security](https://img.shields.io/badge/security-OAuth%202.1%20%2F%20OIDC-purple.svg)]()

**QuickLab MCP** (formerly ModelLab) is an enterprise-grade Machine Learning Experimentation Control Plane exposed via the **Model Context Protocol (MCP)**. Engineered with FastMCP 4 and conforming to the standard MCP baseline (`2026-07-28`), QuickLab enables autonomous AI agents and human data scientists to discover approved architectures, validate tabular datasets, submit reproducible training experiments, inspect predictions/metrics, and perform deep diagnostic analysis—**with zero arbitrary code execution**.

---

## Key Highlights

- **Zero Placeholders, Real Tabular ML**: Fully implemented, sandboxed training algorithms for all 7 approved tabular architectures: **Logistic Regression**, **Random Forest**, **XGBoost**, **LightGBM**, **CatBoost**, **Linear SVM**, and **Multi-Layer Perceptron (MLP)**.
- **23 MCP Tools, 3 Resources, 2 Prompts**: Comprehensive tool catalog across Projects, Discovery, Ingestion, Experimentation, Artifacts, and Diagnostics.
- **Strict Leak-Free Preprocessing**: Preprocessing transforms (median/mode imputation, standard scaling, one-hot encoding) are fitted strictly on training folds and serialized alongside models.
- **Dual Production Transports**:
  - **STDIO Transport**: Pure JSON-RPC on standard output (`sys.stdout`) with all structured logging isolated strictly to `sys.stderr`.
  - **Streamable HTTP Transport**: Modern HTTP transport mounted at `/mcp` with Bearer auth middleware and operational probes (`/health/live`, `/health/ready`).
- **OAuth 2.1 & Multi-Tenant Security**: Tenant boundary enforcement, RBAC matrix (`Viewer`, `Researcher`, `Operator`, `Admin`), token scope attenuation, and anti-enumeration protections (HTTP 404 instead of 403 on cross-tenant probes).
- **Transactional Outbox Architecture**: Guarantees zero lost experiment submissions using PostgreSQL transactional Outbox events dispatched to Redis queues.
- **S3 / MinIO Object Storage**: Immutable tenant-isolated artifact storage with SHA-256 integrity verification, presigned download URLs, and offline memory fallback.
- **Full Test Coverage**: **108 tests passing (100% green)** across unit, integration, security matrix, MCP contract, zero-config fallbacks, and full end-to-end experiment lifecycle.

---

## Architectural Topology

```mermaid
flowchart TD
    subgraph Clients["AI Agents & IDE Interfaces"]
        Claude["Claude Desktop / VS Code / Cursor"]
        Autonomous["Autonomous Agent Mesh"]
    end

    subgraph ControlPlane["ModelLab Control Plane (FastMCP 4)"]
        HTTPGateway["Streamable HTTP Gateway (/mcp)"]
        STDIOGateway["STDIO Transport (Local IPC)"]
        AuthMiddleware["OAuth 2.1 / OIDC Bearer Validator"]
        RateLimiter["Sliding-Window Rate Limiter"]
        ToolRouter["21 MCP Tools & Resource Engine"]
    end

    subgraph Persistence["Persistence & Message Broker"]
        Postgres[(PostgreSQL 16\nSchema & Outbox Events)]
        Redis[(Redis 7\nQueue & Rate Limit Window)]
        MinIO[(MinIO / S3\nDatasets & Model Artifacts)]
    end

    subgraph WorkerLayer["Sandboxed Worker Execution"]
        WorkerEngine["Tabular ML Execution Runner"]
        Preproc["Leak-Free Preprocessing Pipeline"]
        Trainers["7 Approved Tabular Trainers\n(RF, XGB, LGBM, CatB, LR, SVM, MLP)"]
    end

    subgraph Telemetry["Observability & Diagnostics"]
        OTEL["OpenTelemetry Collector"]
        JSONStderr["Structured JSON Logs (stderr)"]
    end

    Claude -->|STDIO| STDIOGateway
    Autonomous -->|HTTP Bearer| HTTPGateway
    HTTPGateway --> AuthMiddleware
    AuthMiddleware --> RateLimiter
    RateLimiter --> ToolRouter
    STDIOGateway --> ToolRouter

    ToolRouter --> Postgres
    ToolRouter --> Redis
    ToolRouter --> MinIO

    Postgres -->|Transactional Outbox| Redis
    Redis --> WorkerEngine
    WorkerEngine --> Preproc
    Preproc --> Trainers
    Trainers --> MinIO
    Trainers --> Postgres

    ControlPlane --> JSONStderr
    ControlPlane --> OTEL
    WorkerEngine --> OTEL
```

---

## Complete MCP Tool Catalog

ModelLab exposes 23 granular tools across 6 functional categories:

### 1. Project Management Tools

| Tool Name | Parameters | Required Scope | Description |
| :--- | :--- | :--- | :--- |
| `create_project` | `name`, `description` *(opt)* | `ml:projects:write` | Create a new project workspace under the caller's tenant boundary. |
| `list_projects` | `limit` *(opt)*, `offset` *(opt)* | `ml:projects:read` | List active projects accessible within the caller's tenant boundary. |

### 2. Discovery Tools

| Tool Name | Parameters | Required Scope | Description |
| :--- | :--- | :--- | :--- |
| `list_models` | *none* | `ml:models:read` | List all approved tabular ML models in the catalog. |
| `get_model` | `model_id` | `ml:models:read` | Inspect hyperparameter schemas, bounds, and container digests for a model. |
| `list_model_versions` | `model_id` | `ml:models:read` | List registered immutable versions for a specific model family. |
| `list_datasets` | `project_id` *(opt)* | `ml:datasets:read` | List datasets available within the caller's tenant/project boundary. |
| `get_dataset` | `dataset_id` | `ml:datasets:read` | Retrieve metadata, format, and version history for a specific dataset. |
| `list_dataset_versions` | `dataset_id` | `ml:datasets:read` | List immutable versions, row counts, and SHA-256 hashes for a dataset. |

### 3. Dataset Management & Validation Tools

| Tool Name | Parameters | Required Scope | Description |
| :--- | :--- | :--- | :--- |
| `register_dataset` | `project_id`, `name`, `description`, `format`, `data_base64`, `version` | `ml:datasets:write` | Ingest and validate a tabular dataset (CSV/Parquet/JSONL), store as Parquet, and record content hash. |
| `validate_dataset` | `format`, `data_base64` | `ml:datasets:read` | Validate raw dataset bytes against schema rules and row limits without persisting. |
| `inspect_dataset` | `dataset_id`, `version`, `sample_rows` | `ml:datasets:read` | Inspect schema column types, missingness, and head preview rows. |

### 4. Experimentation Tools

| Tool Name | Parameters | Required Scope | Description |
| :--- | :--- | :--- | :--- |
| `create_experiment` | `project_id`, `dataset_version_id`, `model_version_id`, `task_type`, `target_column`, `feature_columns`, `primary_metric`, `additional_metrics`, `hyperparameters`, `random_seed`, `idempotency_key` | `ml:experiments:create` | Formulate, validate (target/feature columns, model bounds), and queue an ML experiment with transactional Outbox event. |
| `get_experiment` | `experiment_id` | `ml:experiments:read` | Retrieve current lifecycle state (`QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED`), run counts, timestamps, and error details (`error_message`, `error_type`) if failed. |
| `cancel_experiment` | `experiment_id` | `ml:experiments:cancel` | Gracefully cancel an active or queued experiment run. |
| `list_experiments` | `project_id`, `status`, `limit`, `offset` | `ml:experiments:read` | Paginated listing of experiments within a project with optional status filter. |

### 5. Results & Artifacts Tools

| Tool Name | Parameters | Required Scope | Description |
| :--- | :--- | :--- | :--- |
| `get_experiment_metrics` | `experiment_id` | `ml:experiments:read` | Retrieve computed metrics across `train` and `validation` splits. |
| `get_experiment_predictions` | `experiment_id`, `limit` | `ml:artifacts:read` | Retrieve sample prediction records (`y_true`, `y_pred`, `y_proba_*`) and presigned download URL. |
| `list_experiment_artifacts` | `experiment_id` | `ml:artifacts:read` | List all persisted artifacts (`model.joblib`, `preprocessor.joblib`, `predictions.parquet`, `metrics.json`). |
| `read_experiment_artifact` | `artifact_id` | `ml:artifacts:read` | Retrieve artifact metadata and secure presigned S3 download URL. |
| `compare_experiments` | `experiment_ids` | `ml:experiments:read` | Side-by-side comparison of validation metrics and hyperparameters across runs. |

### 6. Structured Diagnostic Analysis Tools

| Tool Name | Parameters | Required Scope | Description |
| :--- | :--- | :--- | :--- |
| `analyze_experiment` | `experiment_id` | `ml:analysis:create` | Automated diagnostic report evaluating generalization gap, overfitting severity, and data leakage risks. |
| `analyze_model_errors` | `experiment_id` | `ml:analysis:create` | Deep error slice analysis including residual skew, false positives vs false negatives, and anomaly counts. |
| `check_experiment_validity` | `experiment_id` | `ml:analysis:create` | Automated verification of experiment validity, dataset lineage, and metric reproducibility. |

---

## MCP Resources & Prompts

### Resources
- `modellab://models`: Static resource cataloging all approved model architectures.
- `modellab://models/{model_id}`: Parameterized template for full model hyperparameter schemas.
- `modellab://experiments/{experiment_id}/metrics`: Parameterized template for experiment metrics.

### Prompts
- `experiment_design`: Guided architectural workflow prompting agents to formulate sound hypotheses, choose candidate models, and select non-leaking split strategies.
- `error_diagnosis`: Structured diagnostic workflow assisting agents in diagnosing training regressions, severe class imbalances, or generalization gaps.

---

## Approved Model Families & Hyperparameter Bounds

| Model Family | `model_id` | Supported Task Types | Key Hyperparameters (Strictly Validated) |
| :--- | :--- | :--- | :--- |
| **Random Forest** | `random_forest` | Binary / Multiclass Classification, Regression | `n_estimators` [10–1000], `max_depth` [1–50], `min_samples_split` [2–20] |
| **XGBoost** | `xgboost` | Binary / Multiclass Classification, Regression | `n_estimators` [10–2000], `max_depth` [1–16], `learning_rate` [0.001–1.0], `subsample` [0.1–1.0] |
| **LightGBM** | `lightgbm` | Binary / Multiclass Classification, Regression | `n_estimators` [10–2000], `num_leaves` [7–256], `learning_rate` [0.001–1.0] |
| **CatBoost** | `catboost` | Binary / Multiclass Classification, Regression | `iterations` [10–2000], `depth` [1–12], `learning_rate` [0.001–1.0] |
| **Logistic Regression** | `logistic_regression` | Binary / Multiclass Classification | `C` [0.001–1000.0], `penalty` (`l1`, `l2`, `elasticnet`, `none`), `solver` |
| **Linear SVM** | `linear_svm` | Binary / Multiclass Classification, Regression | `C` [0.001–100.0], `loss` (`hinge`, `squared_hinge`), `max_iter` [100–10000] |
| **Multi-Layer Perceptron** | `mlp` | Binary / Multiclass Classification, Regression | `hidden_layer_sizes`, `activation` (`relu`, `tanh`), `alpha` [1e-6–1.0], `learning_rate_init` |

---

## Quickstart

### Prerequisites
- Python 3.13+ (or Conda environment `ML_LLM`)
- Docker & Docker Compose (optional, for local services)

### 1. Installation

#### Instant Execution via `uvx` (Recommended)
No cloning required — run QuickLab directly with Astral `uv`:
```bash
uvx quicklab-mcp
```

#### Local Development Installation
```bash
# Clone the repository
git clone https://github.com/Aldrin-Joan/QuickLab-MCP.git
cd QuickLab-MCP

# Install dependencies using uv
uv sync
```

### 2. Run the Full Test Suite

Verify all 75 automated tests across unit, integration, contract, security, and e2e suites:

```bash
pytest tests/ -v
```

Output:
```text
================= 75 passed, 7 warnings in 187.73s (0:03:07) ==================
```

### 3. Launch Local Infrastructure (Optional)

Spin up PostgreSQL 16, Redis 7, MinIO S3, and OpenTelemetry Collector:

```bash
docker compose up -d
```

Apply database migrations:

```bash
alembic upgrade head
```

### 4. Running ModelLab

#### Option A: Streamable HTTP Mode (Production & Remote Agents)

```bash
python -m ml_mcp.server.http
```

Access operational endpoints:
- Liveness Probe: `http://127.0.0.1:8000/health/live`
- Readiness Probe: `http://127.0.0.1:8000/health/ready`
- MCP Endpoint: `http://127.0.0.1:8000/mcp`

#### Option B: STDIO Transport Mode (IDE & Desktop Agents)

```bash
python -m ml_mcp.server.stdio
```

---

## MCP Client Configuration

ModelLab supports both **STDIO** (local subprocess IPC) and **Streamable HTTP** (networked SSE + POST) transports across both **Docker** and **UV / local Python**.

Select your preferred runtime configuration below:

### 1. Docker + STDIO (Pre-Built Image)
Runs QuickLab inside an isolated Docker container with standard I/O (`sys.stdin` / `sys.stdout`) piped directly to the MCP client:

```json
{
  "mcpServers": {
    "quicklab": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "--entrypoint", "python",
        "-e", "APP_ENV=production",
        "-e", "DATABASE_URL=postgresql+asyncpg://postgres:postgres@host.docker.internal:5433/modellab",
        "-e", "REDIS_URL=redis://host.docker.internal:6379/0",
        "-e", "OBJECT_STORE_ENDPOINT_URL=http://host.docker.internal:9000",
        "-e", "OBJECT_STORE_ACCESS_KEY_ID=minioadmin",
        "-e", "OBJECT_STORE_SECRET_ACCESS_KEY=minioadmin",
        "-e", "OBJECT_STORE_BUCKET_NAME=modellab-artifacts",
        "ghcr.io/aldrin-joan/quicklab-api:latest",
        "-m", "ml_mcp.server.stdio"
      ]
    }
  }
}
```

> **Note for Docker Networking**: If running local services on your host, `host.docker.internal` allows the container to connect to your host ports seamlessly on macOS, Windows, and Linux. If using Docker Compose, connect via the shared Docker network:
> ```json
> "args": [
>   "run", "-i", "--rm",
>   "--network", "modellab_default",
>   "-e", "DATABASE_URL=postgresql+asyncpg://postgres:postgres@postgres:5432/modellab",
>   "-e", "REDIS_URL=redis://redis:6379/0",
>   "-e", "OBJECT_STORE_ENDPOINT_URL=http://minio:9000",
>   "-e", "OBJECT_STORE_ACCESS_KEY_ID=minioadmin",
>   "-e", "OBJECT_STORE_SECRET_ACCESS_KEY=minioadmin",
>   "-e", "OBJECT_STORE_BUCKET_NAME=modellab-artifacts",
>   "ghcr.io/aldrin-joan/quicklab-api:latest",
>   "-m", "ml_mcp.server.stdio"
> ]
> ```

---

### Database Schema Initialization & Migrations

When pointing QuickLab to a new PostgreSQL instance (local, AWS RDS, Supabase, Neon, or GCP Cloud SQL), the schema must be initialized with Alembic migrations:

#### Option A: Docker Compose (Fully Automated)
If deploying via [`docker-compose.stdio.yml`](docker-compose.stdio.yml) or [`docker-compose.http.yml`](docker-compose.http.yml), migrations execute automatically prior to application launch via the `db-migrate` service:
```bash
docker compose -f docker-compose.stdio.yml up -d
```

#### Option B: Standalone Docker (External / Managed Database)
Run migrations on a remote or standalone PostgreSQL instance without installing Python or cloning the repo:
```bash
docker run --rm \
  -e DATABASE_URL="postgresql+asyncpg://<USER>:<PASSWORD>@<HOST>:<PORT>/<DB_NAME>" \
  ghcr.io/aldrin-joan/quicklab-api:latest \
  -m alembic upgrade head
```

#### Option C: Local Python / UV (Source Repository)
If running from source:
```bash
uv run alembic upgrade head
```

> [!NOTE]
> **Automatic Seed Data**: Once Alembic creates the schema, QuickLab's startup lifespan automatically seeds the 7 approved tabular ML models and ensures a default project workspace exists for the tenant.


---

### 2. Docker + HTTP
Connects to the ModelLab API service running inside Docker Compose (`docker compose -f docker-compose.http.yml up -d`).

```json
{
  "mcpServers": {
    "modellab": {
      "url": "http://localhost:8000/mcp",
      "headers": {
        "Authorization": "Bearer <YOUR_JWT_BEARER_TOKEN>"
      }
    }
  }
}
```

---

### 3. UVX + STDIO (Recommended Turnkey Setup)
Runs QuickLab globally via `uvx` directly from PyPI.

#### Zero-Configuration Mode (Zero Dependencies Required)
If you don't have PostgreSQL or Docker installed, QuickLab automatically provisions an embedded SQLite database (`~/.quicklab/quicklab.db`), local filesystem artifact storage (`~/.quicklab/artifacts`), and an in-process queue:

```json
{
  "mcpServers": {
    "quicklab": {
      "command": "uvx",
      "args": ["quicklab-mcp"]
    }
  }
}
```

#### Production Mode (Connecting to PostgreSQL & MinIO / S3)
To connect QuickLab to your own PostgreSQL and S3-compatible storage:

```json
{
  "mcpServers": {
    "quicklab": {
      "command": "uvx",
      "args": ["quicklab-mcp"],
      "env": {
        "APP_ENV": "production",
        "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@localhost:5433/modellab",
        "REDIS_URL": "redis://localhost:6379/0",
        "OBJECT_STORE_ENDPOINT_URL": "http://localhost:9000",
        "OBJECT_STORE_ACCESS_KEY_ID": "minioadmin",
        "OBJECT_STORE_SECRET_ACCESS_KEY": "minioadmin",
        "OBJECT_STORE_BUCKET_NAME": "modellab-artifacts"
      }
    }
  }
}
```

Or for local source development with `uv`:
```json
{
  "mcpServers": {
    "quicklab": {
      "command": "uv",
      "args": [
        "run",
        "--directory", "/absolute/path/to/QuickLab-MCP",
        "python", "-m", "ml_mcp.server.stdio"
      ],
      "env": {
        "APP_ENV": "production",
        "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@localhost:5433/modellab",
        "REDIS_URL": "redis://localhost:6379/0",
        "OBJECT_STORE_ENDPOINT_URL": "http://localhost:9000",
        "OBJECT_STORE_ACCESS_KEY_ID": "minioadmin",
        "OBJECT_STORE_SECRET_ACCESS_KEY": "minioadmin",
        "OBJECT_STORE_BUCKET_NAME": "modellab-artifacts"
      }
    }
  }
}
```

---

### 4. UV + HTTP
Starts the ModelLab HTTP service locally with `uv` and connects client agents via Streamable HTTP.

**1. Launch the server:**
```bash
uv run python -m ml_mcp.server.http
```

**2. Client Configuration (`mcp.json` / `claude_desktop_config.json`):**
```json
{
  "mcpServers": {
    "modellab": {
      "url": "http://localhost:8000/mcp",
      "headers": {
        "Authorization": "Bearer <YOUR_JWT_BEARER_TOKEN>"
      }
    }
  }
}
```

---

### Generating an OAuth 2.1 Bearer Token (for HTTP Modes)

Generate a cryptographically signed JWT token for authentication in HTTP mode:

```bash
uv run python -c "
from ml_mcp.server.auth import TokenValidator
from ml_mcp.domain.policies import Role, Scope

validator = TokenValidator()
token = validator.create_access_token(
    principal_id='agent-user',
    tenant_id='default-tenant',
    role=Role.ADMIN,
    scopes=[s.value for s in Scope],
)
print('Bearer Token:\n' + token)
"
```

---

## Security & Access Control (RBAC)

ModelLab implements a zero-trust security architecture adhering to OAuth 2.1:

| Scope | Viewer | Researcher | Operator | Admin |
| :--- | :---: | :---: | :---: | :---: |
| `ml:models:read` | ✅ | ✅ | ✅ | ✅ |
| `ml:datasets:read` | ✅ | ✅ | ✅ | ✅ |
| `ml:datasets:write` | ❌ | ✅ | ✅ | ✅ |
| `ml:experiments:read` | ✅ | ✅ | ✅ | ✅ |
| `ml:experiments:create` | ❌ | ✅ | ✅ | ✅ |
| `ml:experiments:cancel` | ❌ | ✅ | ✅ | ✅ |
| `ml:analysis:create` | ❌ | ✅ | ✅ | ✅ |
| `ml:artifacts:read` | ✅ | ✅ | ✅ | ✅ |
| `ml:worker:operate` | ❌ | ❌ | ✅ | ✅ |
| `ml:experiment:override` | ❌ | ❌ | ❌ | ✅ |

- **Scope Attenuation**: Access tokens carrying specific scopes attenuate privileges below role defaults.
- **Anti-Enumeration Defense**: Requests attempting cross-tenant resource access consistently return HTTP 404 (`ResourceNotFoundError`) rather than 403, preventing attackers from probing valid resource IDs.
- **Sensitive Key Redaction**: Telemetry and logs automatically scrub sensitive credentials (`password`, `secret`, `token`, `key`, `authorization`).

---

## Production Containerization

Multi-stage [Dockerfile](Dockerfile) provides hardened images running as unprivileged users:

```bash
# Build API Server container
docker build --target ml-mcp-api -t modellab/ml-mcp-api:latest .

# Build Sandboxed Worker container
docker build --target ml-worker-tabular-cpu -t modellab/ml-worker-tabular-cpu:latest .
```

Both container images run with read-only root filesystems and non-root users (`uid 10001` / `uid 10002`).

---

## Project Repository Structure

```text
ModelLab/
├── .github/workflows/ci.yml       # Automated CI/CD pipeline (lint, test, build)
├── alembic/versions/              # Database migration scripts
├── deploy/docker/                 # OTEL Collector and container deployment configs
├── Docs/                          # Architecture, Tech-Stack & Operational Guide
├── src/ml_mcp/                    # Core ModelLab application source
│   ├── application/               # Application services (models, datasets, analysis)
│   ├── config/                    # Pydantic Settings & environment parsing
│   ├── domain/                    # Domain models, policies, RBAC & error hierarchy
│   ├── infrastructure/            # Postgres ORM, Redis queue, S3 storage, OTEL
│   ├── mcp/                       # FastMCP 4 tools, schemas, resources & prompts
│   ├── server/                    # Server factory, auth validator, HTTP & STDIO
│   └── workers/                   # Preprocessing, ML trainers & execution runner
├── tests/                         # 75 Automated Test Cases
│   ├── contract/                  # FastMCP tool contract compliance tests
│   ├── e2e/                       # Full ML experiment lifecycle end-to-end tests
│   ├── integration/               # Database, Outbox, Redis & Worker integration
│   ├── security/                  # Multi-tenant isolation & RBAC security matrix
│   └── unit/                      # Fast unit tests across all domain layers
├── docker-compose.yml             # Local PostgreSQL, Redis, MinIO & OTEL stack
├── Dockerfile                     # Multi-stage production container build
├── pyproject.toml                 # Package definition and locked dependencies
└── README.md                      # Project documentation
```

---

## License

ModelLab is licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for details.
