# ModelLab Operational & Production Deployment Guide

ModelLab is an enterprise-grade Machine Learning Experimentation Control Plane exposing 21 Model Context Protocol (MCP protocol baseline `2026-07-28` and FastMCP 4) tools, resources, and prompts to autonomous AI agents and human data scientists.

---

## 1. Architectural Topology

```mermaid
flowchart TD
    subgraph Clients["AI Agents & IDE Clients"]
        Claude["Claude Desktop / Cursor / Antigravity"]
        AgentWorker["Autonomous Agent Mesh"]
    end

    subgraph ControlPlane["ModelLab Control Plane (FastMCP 4)"]
        HTTPGateway["Streamable HTTP Gateway (/mcp)"]
        STDIOGateway["STDIO Transport (Local IPC)"]
        AuthMiddleware["OAuth 2.1 / OIDC Bearer Validator"]
        RateLimiter["Sliding-Window Rate Limiter"]
        ToolRouter["21 MCP Tools & Resource Catalog"]
    end

    subgraph Persistence["Persistence & Message Broker"]
        Postgres[(PostgreSQL 16\nSchema & Outbox Table)]
        Redis[(Redis 7\nQueue & Rate Limit Store)]
        MinIO[(MinIO / S3\nDatasets & Artifacts)]
    end

    subgraph Workers["Sandboxed Worker Execution"]
        WorkerEngine["Tabular ML Worker Engine"]
        Sandbox["In-Process Sandboxed Subprocess"]
        Models["7 Approved Tabular Models\n(RF, XGB, LGBM, CatB, LR, SVM, MLP)"]
    end

    subgraph Observability["Telemetry Pipeline"]
        OTEL["OpenTelemetry Collector"]
        JSONStderr["Structured Logs (stderr)"]
    end

    Claude -->|STDIO| STDIOGateway
    AgentWorker -->|HTTP Bearer| HTTPGateway
    HTTPGateway --> AuthMiddleware
    AuthMiddleware --> RateLimiter
    RateLimiter --> ToolRouter
    STDIOGateway --> ToolRouter

    ToolRouter --> Postgres
    ToolRouter --> Redis
    ToolRouter --> MinIO

    Postgres -->|Transactional Outbox| Redis
    Redis --> WorkerEngine
    WorkerEngine --> Sandbox
    Sandbox --> Models
    WorkerEngine --> MinIO
    WorkerEngine --> Postgres

    ControlPlane --> JSONStderr
    ControlPlane --> OTEL
    WorkerEngine --> OTEL
```

---

## 2. Environment Configuration Reference

All settings are managed via Pydantic Settings with typed environment variable overrides.

### 2.1 Core Application & MCP

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `APP_ENVIRONMENT` | `string` | `development` | Environment tier: `development`, `staging`, `production` |
| `APP_DEBUG` | `boolean` | `false` | Enable verbose diagnostic mode |
| `APP_NAME` | `string` | `ModelLab` | Application service name |
| `MCP_HOST` | `string` | `127.0.0.1` | Binding host for HTTP server |
| `MCP_PORT` | `integer` | `8000` | Binding port for HTTP server |
| `MCP_STREAMABLE_HTTP_PATH` | `string` | `/mcp` | Path mounted for FastMCP streamable HTTP transport |
| `MCP_PROTOCOL_VERSION` | `string` | `2026-07-28` | Model Context Protocol baseline version |

### 2.2 Authentication & Security (OAuth 2.1 / OIDC)

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `AUTH_ENABLED` | `boolean` | `true` | Enforce Bearer token authentication on HTTP endpoints |
| `AUTH_ISSUER` | `string` | `https://auth.modellab.local` | Expected JWT issuer (`iss`) claim |
| `AUTH_AUDIENCE` | `string` | `modellab-mcp-api` | Expected JWT audience (`aud`) claim |
| `AUTH_ALGORITHMS` | `list` | `["RS256", "HS256"]` | Permitted signature algorithms (rejects `none`) |
| `AUTH_SECRET_KEY` | `string` | *(dev-secret)* | Secret key for symmetric HS256 tokens |
| `AUTH_PUBLIC_KEY_PEM` | `string` | `None` | Public key PEM for asymmetric RS256 token validation |

### 2.3 Database & Message Broker

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `DATABASE_URL` | `string` | `postgresql+asyncpg://...` | PostgreSQL async connection URI |
| `DATABASE_POOL_SIZE` | `integer` | `10` | SQLAlchemy connection pool size |
| `DATABASE_MAX_OVERFLOW` | `integer` | `20` | Max overflow pool connections |
| `REDIS_URL` | `string` | `redis://localhost:6379/0` | Redis connection URI |
| `REDIS_MAX_CONNECTIONS` | `integer` | `50` | Redis client connection pool ceiling |

### 2.4 Object Storage (S3 / MinIO)

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `OBJECT_STORE_ENDPOINT_URL` | `string` | `http://localhost:9000` | S3 API endpoint URL (or MinIO) |
| `OBJECT_STORE_BUCKET_NAME` | `string` | `modellab-artifacts` | Destination S3 bucket |
| `OBJECT_STORE_REGION` | `string` | `us-east-1` | AWS S3 region |
| `OBJECT_STORE_PRESIGNED_TTL` | `integer` | `900` | Presigned URL lifetime (seconds) |

---

## 3. Quickstart & Local Conda Workflow

### 3.1 Activating Conda Environment

```powershell
conda activate ML_LLM
```

### 3.2 Running the Full Automated Verification Suite

```powershell
pytest tests/ -v
```

All 75 tests (unit, integration, security matrix, contract, and full e2e) run and pass in under 3 minutes.

### 3.3 Running Database Migrations

QuickLab uses Alembic to manage database schema migrations.

#### Option A: Local Python / UV (Source Checkout)
```powershell
uv run alembic upgrade head
```

#### Option B: Standalone Docker (External / Managed Database)
To migrate a remote database (AWS RDS, Supabase, Neon, Cloud SQL) without needing Python installed locally:
```powershell
docker run --rm `
  -e DATABASE_URL="postgresql+asyncpg://<USER>:<PASSWORD>@<HOST>:<PORT>/<DB_NAME>" `
  ghcr.io/aldrin-joan/quicklab-api:latest `
  -m alembic upgrade head
```

#### Option C: Docker Compose (Automated)
When launching via `docker-compose.stdio.yml` or `docker-compose.http.yml`, migrations run automatically before services boot:
```powershell
docker compose -f docker-compose.stdio.yml up -d
```


### 3.4 Launching the HTTP Server

```powershell
python -m ml_mcp.server.http
```
Health checks will be available at:
- `GET http://127.0.0.1:8000/health/live`
- `GET http://127.0.0.1:8000/health/ready`

### 3.5 Launching STDIO Server (for IDE Agents)

```powershell
python -m ml_mcp.server.stdio
```

> [!IMPORTANT]
> In STDIO mode, all logging strictly routes to `sys.stderr` in structured JSON. Standard output (`sys.stdout`) is reserved exclusively for valid MCP JSON-RPC protocol frames.

---

## 4. Docker Compose Stack Deployment

Start the complete persistence and telemetry stack:

```powershell
docker compose up -d
```

Containers provisioned:
1. `modellab-postgres` (PostgreSQL 16 on port 5432)
2. `modellab-redis` (Redis 7 on port 6379)
3. `modellab-minio` (MinIO S3 on port 9000 and console on port 9001)
4. `modellab-otel-collector` (OpenTelemetry Collector on ports 4317, 4318, 8889)

---

## 5. MCP Client Configuration Guide

### 5.1 Claude Desktop Configuration (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "modellab": {
      "command": "C:\\Users\\Aldrin Joan\\.conda\\envs\\ML_LLM\\python.exe",
      "args": ["-m", "ml_mcp.server.stdio"],
      "env": {
        "APP_ENVIRONMENT": "production",
        "DATABASE_URL": "postgresql+asyncpg://postgres:postgrespassword@localhost:5432/modellab",
        "REDIS_URL": "redis://localhost:6379/0",
        "OBJECT_STORE_ENDPOINT_URL": "http://localhost:9000",
        "OBJECT_STORE_ACCESS_KEY_ID": "minioadmin",
        "OBJECT_STORE_SECRET_ACCESS_KEY": "minioadmin"
      }
    }
  }
}
```

### 5.2 Antigravity IDE / Cursor / VS Code Configuration

```json
{
  "mcpServers": {
    "modellab": {
      "url": "http://127.0.0.1:8000/mcp",
      "headers": {
        "Authorization": "Bearer <YOUR_OIDC_BEARER_TOKEN>"
      }
    }
  }
}
```

---

## 6. Security Matrix & RBAC Reference

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

### Anti-Enumeration Principle
Requests for resources belonging to another tenant return HTTP 404 (`ResourceNotFoundError`) rather than 403, preventing attackers from discovering resource identifiers belonging to other tenants.

---

## 7. Operational Incident Runbooks

### 7.1 Redis Broker Failure
- **Symptom**: `health/ready` probe reports `"redis": "degraded"`.
- **System Behavior**: The sliding-window rate limiter automatically falls back to in-memory tracking without rejecting legitimate caller requests. The Outbox processor temporarily retains events in Postgres until connection restores.
- **Remedy**: Inspect Redis container logs: `docker logs modellab-redis`. Restart Redis instance if necessary.

### 7.2 Database Degradation
- **Symptom**: `health/ready` returns HTTP 503 with `"database": "unhealthy"`.
- **System Behavior**: Read/write operations return sanitized 500 error envelopes without exposing SQL connection strings or credentials.
- **Remedy**: Check PostgreSQL connection pool saturation and vacuum status. Inspect slow queries in `pg_stat_activity`.
