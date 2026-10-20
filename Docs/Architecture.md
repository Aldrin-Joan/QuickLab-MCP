# Architecture.md

# QuickLab MCP --- Production Architecture (formerly ModelLab)

**Status:** Production architecture baseline\
**Protocol baseline:** MCP `2026-07-28`\
**Primary implementation:** Python + FastMCP 4\
**Transports:** STDIO and Streamable HTTP\
**Deployment model:** local process for STDIO; authenticated HTTPS
service for remote access

## 1. System Goal

This project exposes a secure, reproducible machine-learning
experimentation control plane through MCP.

An AI client/agent can discover approved models and datasets, validate
experiment specifications, submit experiments, inspect
metrics/artifacts, and request structured result analysis.

The MCP server is **not** the place where arbitrary model code is
executed. It is the authenticated control plane between AI clients and
trusted ML execution infrastructure.

The architecture deliberately separates:

1.  **MCP protocol layer** --- FastMCP tools/resources/prompts and
    transport handling.
2.  **Application layer** --- experiment/domain rules, authorization,
    validation, quotas.
3.  **Persistence layer** --- PostgreSQL and object storage.
4.  **Execution layer** --- isolated ML workers.
5.  **Observability layer** --- OpenTelemetry, metrics, structured logs
    and audit events.
6.  **Security boundary** --- authentication, authorization, tenant
    isolation, network policy and sandboxing.

## 2. Design Principles

-   MCP tools are thin adapters; business logic lives in application
    services.
-   No arbitrary Python execution from tool arguments.
-   No shell command execution from MCP tools.
-   No model code supplied directly by an LLM.
-   Every model and dataset is registered, versioned and identified by
    immutable IDs.
-   Every experiment is reproducible from a persisted specification.
-   Experiment inputs and artifacts are content-addressed where
    practical.
-   Long-running work is asynchronous; MCP request paths never block on
    model training.
-   Remote MCP is stateless at the protocol layer; application state is
    stored explicitly.
-   STDIO is treated as a local trusted-process boundary, not as a
    remote transport.
-   HTTPS is terminated at a trusted ingress/reverse proxy or directly
    at the application boundary.
-   Authentication and authorization are separate concerns.
-   Least privilege is applied to tools, datasets, models and execution
    resources.

### Architecture Decision Records (ADRs)
Key architectural trade-offs, rationale, and technology choices are formally recorded in [Architecture Decision Records](decisions/README.md):
- [ADR-0001: Streamable HTTP Transport with OAuth 2.1](decisions/0001-streamable-http-mcp-transport-and-oauth2-auth.md)
- [ADR-0002: Asynchronous Worker Queue Architecture](decisions/0002-asynchronous-worker-queue-architecture.md)
- [ADR-0003: Multi-Tenant Data Isolation, Scoped RBAC, and Sliding-Window Rate Limiting](decisions/0003-multi-tenant-isolation-and-rbac.md)
- [ADR-0004: Parquet Format Optimization for High-Volume Tabular Ingestion](decisions/0004-tabular-data-ingestion-and-parquet-optimization.md)
- [ADR-0005: Database Exception Sanitization and Anti-Disclosure Error Handling](decisions/0005-sanitized-error-handling-and-anti-disclosure.md)
- [ADR-0006: First-Class Project Management and Workspace Hierarchy](decisions/0006-project-hierarchy-and-workspace-management.md)

-   All external boundaries have explicit timeouts, retry budgets and
    size limits.
-   Every production mutation is auditable.
-   Fail closed on authentication, authorization, schema validation and
    policy errors.
-   No silent fallback from secure execution to unsafe execution.
-   Exact dependency versions are pinned and continuously scanned.
-   Compatibility with the current MCP protocol is tested as a contract.

## 3. High-Level Architecture

``` mermaid
flowchart TB
    U["User / AI Agent / MCP Host"]

    subgraph T["MCP Transport Boundary"]
        S["STDIO Transport"]
        H["Streamable HTTP Transport"]
        TLS["TLS / HTTPS Ingress"]
        AUTH["OAuth/OIDC + JWT Validation"]
    end

    subgraph MCP["ML-MCP Server"]
        ROUTER["FastMCP Router"]
        TOOLS["MCP Tools"]
        RES["MCP Resources"]
        PROMPTS["MCP Prompts"]
        POLICY["Authorization + Policy Engine"]
        APP["Application Services"]
    end

    subgraph DATA["Control-Plane Data"]
        PG["PostgreSQL"]
        REDIS["Redis / Valkey"]
        OBJ["S3-Compatible Object Storage"]
    end

    subgraph EXEC["Execution Plane"]
        Q["Task Queue"]
        W["Sandboxed ML Workers"]
        GPU["GPU Worker Pool"]
    end

    subgraph OBS["Observability"]
        OTEL["OpenTelemetry"]
        LOG["Structured Logs"]
        MET["Metrics"]
        TRACE["Distributed Traces"]
        AUDIT["Audit Store"]
    end

    U --> S
    U --> TLS
    TLS --> H
    H --> AUTH
    S --> ROUTER
    AUTH --> ROUTER

    ROUTER --> TOOLS
    ROUTER --> RES
    ROUTER --> PROMPTS
    TOOLS --> POLICY
    RES --> POLICY
    POLICY --> APP

    APP --> PG
    APP --> REDIS
    APP --> OBJ
    APP --> Q

    Q --> W
    Q --> GPU
    W --> OBJ
    GPU --> OBJ
    W --> PG
    GPU --> PG

    ROUTER --> OTEL
    APP --> OTEL
    W --> OTEL
    OTEL --> LOG
    OTEL --> MET
    OTEL --> TRACE
    APP --> AUDIT
```

## 4. Transport Architecture

### 4.1 STDIO

STDIO is the local deployment mode.

``` mermaid
sequenceDiagram
    participant Host as MCP Host
    participant Proc as ML-MCP Process
    participant App as Application Services
    participant DB as PostgreSQL

    Host->>Proc: JSON-RPC over stdin
    Proc->>App: validated tool call
    App->>DB: authorized operation
    DB-->>App: result
    App-->>Proc: structured result
    Proc-->>Host: JSON-RPC over stdout
```

Rules:

-   MCP protocol traffic uses stdout exclusively.
-   Application logs use stderr.
-   Never print diagnostics to stdout.
-   Never expose a listening network port from the STDIO entry point.
-   Local filesystem access is allowlisted.
-   Credentials are loaded from the process environment or OS secret
    store.
-   STDIO does not bypass application authorization; local identity is
    still mapped to a principal.
-   The STDIO launcher must run with the minimum filesystem and OS
    privileges required by the installation.
-   The server must not trust model-provided file paths.

### 4.2 Streamable HTTP

Remote deployment uses MCP Streamable HTTP rather than legacy HTTP+SSE.

``` mermaid
flowchart LR
    C["MCP Client"] -->|HTTPS| EDGE["TLS / WAF / Reverse Proxy"]
    EDGE --> API["FastMCP HTTP Server"]
    API --> AUTH["JWT / OAuth Authorization"]
    AUTH --> APP["Application Services"]
    APP --> DB["PostgreSQL"]
    APP --> QUEUE["Redis/Valkey Task Queue"]
```

The MCP `2026-07-28` protocol is designed around stateless HTTP
requests, standard MCP headers and cacheable list responses. This allows
ordinary load balancing without protocol-level sticky sessions. The
architecture therefore does **not** use an MCP session store for normal
remote requests. citeturn0search0turn0search1

The application can still maintain state, but that state is explicit:

``` text
experiment_id
dataset_version_id
model_version_id
task_id
analysis_id
artifact_id
```

These IDs are persisted and passed through tool calls.

### 4.3 HTTP vs HTTPS

Development:

``` text
http://127.0.0.1:8000/mcp
```

Production:

``` text
https://mcp.example.com/mcp
```

Production traffic must not expose plain HTTP publicly.

Recommended production topology:

``` text
Internet
   |
   v
[Cloud Load Balancer / WAF]
   |
   | HTTPS
   v
[FastMCP replicas]
   |
   +---- PostgreSQL
   +---- Redis/Valkey
   +---- Object Storage
   +---- Worker Queue
```

TLS 1.2+ is required; TLS 1.3 is preferred. HSTS is enabled at the edge.

## 5. MCP Surface

The initial public surface is intentionally small.

### Discovery tools

``` text
list_models
get_model
list_model_versions

list_datasets
get_dataset
list_dataset_versions
```

### Dataset tools

``` text
register_dataset
validate_dataset
inspect_dataset
```

### Experiment tools

``` text
create_experiment
get_experiment
cancel_experiment
list_experiments
```

### Result tools

``` text
get_experiment_metrics
get_experiment_predictions
list_experiment_artifacts
read_experiment_artifact
compare_experiments
```

### Analysis tools

``` text
analyze_experiment
analyze_model_errors
check_experiment_validity
```

The MCP surface must not expose:

``` text
execute_python
execute_shell
run_arbitrary_code
download_and_execute_model
install_package
mount_host_filesystem
```

These are deliberately prohibited.

## 6. Tool Classification

Every tool has a policy class.

  Class        Example                        Auth      Audit   Rate Limit
  ------------ ---------------------------- ------ ---------- ------------
  Read         `get_model`                     Yes   Optional         High
  Read         `inspect_dataset`               Yes        Yes       Medium
  Mutation     `register_dataset`              Yes        Yes          Low
  Experiment   `create_experiment`             Yes        Yes          Low
  Control      `cancel_experiment`             Yes        Yes          Low
  Analysis     `analyze_experiment`            Yes        Yes       Medium
  Artifact     `read_experiment_artifact`      Yes        Yes       Medium

## 7. Authorization Model

Use RBAC plus resource-level authorization.

Roles:

``` text
viewer
researcher
operator
admin
```

Example permissions:

``` text
viewer:
    model:read
    dataset:read
    experiment:read
    artifact:read

researcher:
    viewer permissions
    dataset:create
    experiment:create
    experiment:cancel
    analysis:create

operator:
    researcher permissions
    worker:operate
    experiment:override

admin:
    all permissions
```

Authorization is evaluated at:

``` text
identity
    ↓
role
    ↓
permission
    ↓
tenant/project
    ↓
resource ownership/access policy
```

A valid JWT does not automatically grant access to every dataset or
experiment.

## 8. Authentication

Remote HTTP uses OAuth 2.1/OIDC-compatible authorization with
short-lived access tokens.

JWT validation must verify:

``` text
issuer
audience
signature
algorithm allowlist
expiration
not-before
token type
required scopes
```

Issuer validation is particularly important under the current MCP
authorization model. MCP's 2026-07-28 security hardening explicitly
addresses issuer validation and credential binding.
citeturn0search0turn0search3

For service-to-service callers:

``` text
OAuth client credentials
        +
short-lived JWT
        +
narrow scopes
```

For human-driven clients:

``` text
OIDC login
    ↓
authorization
    ↓
short-lived access token
```

No long-lived bearer tokens are stored in PostgreSQL.

## 9. Request Security Pipeline

Every remote request follows:

``` mermaid
flowchart TD
    R["HTTP Request"]
    TLS["TLS Validation"]
    SIZE["Body / Header Size Limits"]
    RATE["Rate Limiter"]
    AUTH["JWT Authentication"]
    CLAIMS["Claims Validation"]
    SCOPE["Scope Authorization"]
    SCHEMA["MCP Schema Validation"]
    POLICY["Application Policy"]
    SERVICE["Application Service"]
    AUDIT["Audit Event"]
    RESP["Response"]

    R --> TLS --> SIZE --> RATE --> AUTH --> CLAIMS --> SCOPE --> SCHEMA --> POLICY --> SERVICE
    SERVICE --> AUDIT
    SERVICE --> RESP
```

Any failed stage terminates the request.

## 10. Experiment Lifecycle

``` mermaid
stateDiagram-v2
    [*] --> CREATED
    CREATED --> VALIDATING
    VALIDATING --> REJECTED
    VALIDATING --> QUEUED
    QUEUED --> RUNNING
    RUNNING --> SUCCEEDED
    RUNNING --> FAILED
    RUNNING --> CANCEL_REQUESTED
    CANCEL_REQUESTED --> CANCELLED
    FAILED --> [*]
    CANCELLED --> [*]
    SUCCEEDED --> ANALYZING
    ANALYZING --> ANALYSIS_READY
```

An experiment specification contains:

``` text
experiment_id
tenant_id
project_id
dataset_version_id
model_version_id
task_type
feature_config
preprocessing_config
split_strategy
evaluation_config
hyperparameters
resource_policy
random_seed
code/package fingerprint
created_by
created_at
```

The server stores the complete specification before queueing.

## 11. Experiment Reproducibility

Each experiment records:

``` text
dataset content hash
dataset schema hash
model version
model package digest
container image digest
Python/runtime version
dependency lock hash
random seed
CPU/GPU class
evaluation strategy
preprocessing configuration
hyperparameters
git commit
execution timestamp
```

An experiment can therefore be reconstructed from immutable references.

### 11.1 Approved Model Families & Metric Reporting Nuances

The platform supports 7 approved tabular model families:
1. **Logistic Regression** (`logistic_regression`): Regularized linear model with native probability calibration.
2. **Random Forest** (`random_forest`): Bagging ensemble with tree-based probability distributions.
3. **XGBoost** (`xgboost`): Gradient boosted decision trees optimized for speed.
4. **LightGBM** (`lightgbm`): Leaf-wise gradient boosted trees with histogram binning.
5. **CatBoost** (`catboost`): Symmetrical gradient boosted trees with native categorical handling and non-disk logging (`allow_writing_files=False`).
6. **Linear SVM** (`linear_svm`): Linear support vector machine (`LinearSVC` / `LinearSVR`). *Note:* `LinearSVC` classifies via hyperplane margin distance rather than probability calibration. Consequently, probability-dependent metrics (`roc_auc`, `pr_auc`, `log_loss`, `brier_score`) are omitted from evaluation metrics and comparison tables for `linear_svm`.
7. **MLP** (`mlp`): Multi-layer perceptron neural network with softmax/sigmoid probability outputs.


## 12. Execution Boundary

The MCP server never directly imports arbitrary registered model code.

``` mermaid
flowchart LR
    MCP["MCP Server"]
    SPEC["Validated Experiment Spec"]
    QUEUE["Task Queue"]
    RUNNER["Execution Orchestrator"]
    SANDBOX["Isolated Worker"]
    ART["Artifact Store"]

    MCP --> SPEC --> QUEUE --> RUNNER --> SANDBOX
    SANDBOX --> ART
    ART --> MCP
```

Workers run in isolated containers.

Required worker controls:

-   non-root user
-   read-only base filesystem
-   ephemeral writable workspace
-   no host Docker socket
-   no host PID namespace
-   restricted Linux capabilities
-   seccomp/AppArmor/SELinux profile as supported by platform
-   CPU limit
-   memory limit
-   process limit
-   wall-clock timeout
-   output-size limit
-   network egress disabled by default
-   explicit dataset/object-store access only
-   temporary credentials with narrow permissions
-   artifact allowlist
-   automatic workspace deletion

## 13. Dataset Security

Datasets are treated as untrusted input.

Before execution:

``` text
upload/register
    ↓
size validation
    ↓
format validation
    ↓
schema extraction
    ↓
malformed-file detection
    ↓
content hash
    ↓
metadata scan
    ↓
policy classification
    ↓
immutable dataset version
```

The server must not execute files from a dataset.

Allowed initial formats:

``` text
CSV
Parquet
JSON Lines
```

Archives are not accepted initially. This avoids archive traversal and
decompression-bomb risk.

## 14. Model Registry Security

A model version is immutable.

Each registered model contains:

``` text
model_id
version
task_types
input_schema
output_schema
runtime
container_image_digest
artifact_digest
supported_metrics
resource_requirements
license
publisher
security_status
```

Model execution requires:

``` text
registered
AND
approved
AND
not revoked
AND
digest matches
```

The agent cannot change the image digest or execution command.

## 15. Storage Architecture

### PostgreSQL

System of record for:

``` text
tenants
users/principals
projects
models
model_versions
datasets
dataset_versions
experiments
experiment_runs
metrics
analysis_runs
audit_events
```

### Object Storage

Stores:

``` text
datasets
predictions
model artifacts
confusion matrices
feature importance
evaluation reports
logs
plots
```

Objects are immutable after finalization.

### Redis / Valkey

Used for:

``` text
rate limiting
short-lived cache
task queue
distributed coordination where required
```

Redis/Valkey is not the system of record.

## 16. Artifact Access

The MCP server should not stream huge artifacts through the MCP
response.

Instead:

``` text
MCP tool
   ↓
authorize artifact
   ↓
create short-lived signed URL
   ↓
return metadata + URL
```

Signed URLs:

``` text
short TTL
read-only
single object
tenant-scoped
no listing permission
```

For small JSON artifacts, direct MCP reads are acceptable.

## 17. Caching

Cache only deterministic, authorization-safe data.

Good candidates:

``` text
model metadata
dataset metadata
model catalog
dataset catalog
experiment summary
```

Do not cache:

``` text
private artifact contents
authorization decisions across long TTLs
unbounded tool outputs
```

Cache keys include:

``` text
tenant_id
resource_id
resource_version
authorization scope where relevant
```

The MCP 2026-07-28 release provides explicit cache hints for
list/resource responses, so the server can publish safe TTL/cache-scope
metadata for appropriate catalog responses. citeturn0search0

## 18. Rate Limits

Use layered limits:

``` text
per IP
per principal
per tenant
per tool
per project
per experiment resource class
```

Example baseline:

``` text
read tools:
    120 requests/min/principal

metadata tools:
    60 requests/min/principal

experiment creation:
    10 requests/min/principal

analysis:
    20 requests/min/principal
```

These are application defaults and should be configuration-driven, not
hardcoded.

Experiment concurrency is separately limited:

``` text
CPU concurrent runs
GPU concurrent runs
tenant concurrent runs
project concurrent runs
```

## 19. Idempotency

Mutation requests that create expensive work use idempotency keys.

``` text
Idempotency-Key
        ↓
request fingerprint
        ↓
existing result?
   /          \
 yes           no
 |              |
return         execute
existing
```

The idempotency record is persisted in PostgreSQL.

## 20. Long-Running Operations

Training and analysis never run synchronously inside an MCP HTTP
request.

``` text
create_experiment
        ↓
validate
        ↓
persist
        ↓
enqueue
        ↓
return experiment/task ID
```

Then:

``` text
get_experiment
get_experiment_metrics
get_experiment_artifacts
```

The MCP 2026-07-28 ecosystem has a Tasks extension for long-running
work; FastMCP 4 supports background tasks and Redis-backed workers. For
this project, the domain-level experiment record remains the source of
truth even when MCP Tasks are used as the transport-facing task
mechanism. citeturn0search0turn2search5

## 21. Agent Analysis Boundary

The analysis agent must not mutate experiment results.

``` text
Experiment Results
        ↓
Read-only Analysis Context
        ↓
Analysis Agent
        ↓
Structured Analysis
```

Analysis output should contain:

``` text
summary
metric_comparison
statistical_observations
error_patterns
data_quality_findings
overfitting_signals
leakage_signals
limitations
recommended_next_experiments
confidence
```

The agent must distinguish:

``` text
observed fact
derived statistic
heuristic interpretation
hypothesis
```

## 22. Observability

Use OpenTelemetry throughout.

Trace:

``` text
MCP request
  └── authentication
  └── authorization
  └── application service
       └── database
       └── queue
       └── worker
            └── artifact store
```

Required metrics:

``` text
mcp_requests_total
mcp_request_duration_seconds
mcp_errors_total
auth_failures_total
authorization_denials_total
rate_limit_denials_total
experiments_created_total
experiments_succeeded_total
experiments_failed_total
experiment_runtime_seconds
worker_queue_depth
worker_utilization
artifact_bytes_written
analysis_runtime_seconds
```

Never log:

``` text
access tokens
refresh tokens
passwords
API keys
dataset contents
authorization headers
signed URLs after issuance
```

## 23. Audit Logging

Audit events include:

``` text
timestamp
principal_id
tenant_id
project_id
action
resource_type
resource_id
request_id
trace_id
result
reason_code
client_identity
```

Audit logs are append-only from the application perspective.

Sensitive payloads are never copied into audit logs.

## 24. Failure Handling

### Database unavailable

``` text
fail closed
return service-unavailable
do not create an untracked experiment
```

### Queue unavailable

``` text
persist experiment as QUEUE_PENDING
retry enqueue using durable outbox
```

### Worker failure

``` text
mark run FAILED
capture sanitized failure reason
retain diagnostic artifact
release resources
```

### Client timeout

The server must not cancel an experiment merely because the MCP HTTP
request ended.

The experiment continues independently and can be queried later.

### Duplicate request

Return the existing idempotent result.

## 25. Durable Queue / Outbox

For reliable experiment submission:

``` mermaid
sequenceDiagram
    participant API as MCP Server
    participant DB as PostgreSQL
    participant Q as Queue

    API->>DB: Transaction: experiment + outbox event
    DB-->>API: commit
    API-->>Client: experiment_id

    loop Outbox worker
        API->>DB: read pending event
        API->>Q: publish experiment
        Q-->>API: acknowledged
        API->>DB: mark outbox delivered
    end
```

This prevents the dangerous state:

``` text
database says experiment exists
BUT
queue never received it
```

## 26. Horizontal Scaling

Remote server replicas are stateless at the MCP protocol layer.

``` mermaid
flowchart LR
    LB["Load Balancer"]

    LB --> A["MCP Replica A"]
    LB --> B["MCP Replica B"]
    LB --> C["MCP Replica C"]

    A --> PG["PostgreSQL"]
    B --> PG
    C --> PG

    A --> R["Redis/Valkey"]
    B --> R
    C --> R
```

No sticky sessions are required for modern MCP HTTP requests.

## 27. Network Segmentation

``` text
Public Network
    |
    +-- WAF / Load Balancer
             |
             v
        MCP API subnet
             |
       +-----+------+
       |            |
       v            v
 PostgreSQL      Redis
 private         private
       |
       v
 Object Storage
 private endpoint
       |
       v
 Worker subnet
```

Workers have no inbound public connectivity.

## 28. Deployment Units

Production deployment consists of:

``` text
ml-mcp-api
ml-mcp-outbox-worker
ml-mcp-task-workers
ml-ml-workers-cpu
ml-ml-workers-gpu
postgresql
redis/valkey
object storage
otel collector
```

Development can run the API, queue and dependencies locally, but
production components remain separated.

## 29. Repository Architecture

``` text
ml-mcp/
├── src/
│   └── ml_mcp/
│       ├── server/
│       │   ├── app.py
│       │   ├── stdio.py
│       │   ├── http.py
│       │   ├── auth.py
│       │   └── middleware.py
│       │
│       ├── mcp/
│       │   ├── tools/
│       │   ├── resources/
│       │   ├── prompts/
│       │   └── schemas/
│       │
│       ├── application/
│       │   ├── models/
│       │   ├── datasets/
│       │   ├── experiments/
│       │   ├── analysis/
│       │   └── artifacts/
│       │
│       ├── domain/
│       │   ├── entities/
│       │   ├── value_objects/
│       │   ├── policies/
│       │   └── errors/
│       │
│       ├── infrastructure/
│       │   ├── postgres/
│       │   ├── redis/
│       │   ├── object_storage/
│       │   ├── queue/
│       │   └── telemetry/
│       │
│       └── config/
│           └── settings.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── security/
│   └── e2e/
│
├── migrations/
├── deploy/
│   ├── docker/
│   ├── kubernetes/
│   └── terraform/
│
├── scripts/
├── pyproject.toml
├── uv.lock
├── Dockerfile
├── Architecture.md
└── Tech-Stack.md
```

## 30. Security Test Matrix

CI must test:

``` text
unauthenticated HTTP request
expired JWT
wrong issuer
wrong audience
invalid signature
missing scope
cross-tenant resource access
cross-project resource access
path traversal
oversized request
malformed dataset
duplicate experiment
idempotency collision
rate-limit bypass attempt
artifact authorization bypass
revoked model execution
worker timeout
worker memory exhaustion
network egress attempt
container privilege escalation attempt
secret leakage in logs
STDIO stdout contamination
```

## 31. Production Readiness Gates

A release is production-ready only when:

``` text
all unit tests pass
all integration tests pass
all MCP contract tests pass
security tests pass
dependency audit passes
container image scan passes
type checking passes
lint/format checks pass
database migration tests pass
load tests pass
failure-recovery tests pass
observability checks pass
artifact authorization tests pass
tenant-isolation tests pass
```

No feature is considered complete merely because its MCP tool returns a
successful response.

## 32. Architectural Decision

The central architectural decision is:

> **ML-MCP is a secure control plane, not an arbitrary-code execution
> engine.**

That separation makes the system safer, easier to scale, easier to audit
and suitable for connecting multiple AI clients to a common ML
experimentation infrastructure.

The future agentic workflow is therefore:

``` text
AI Agent
   ↓
ML-MCP
   ↓
Validated Experiment
   ↓
Durable Queue
   ↓
Sandboxed ML Worker
   ↓
Immutable Results
   ↓
ML-MCP
   ↓
Analysis Agent
   ↓
Structured Findings
```

This is the foundation on which automated model comparison,
hyperparameter optimization, error analysis, dataset diagnostics and
research-oriented experimentation can be added without weakening the MCP
security boundary.

## 33. Current MCP Compatibility Notes

The architecture targets MCP `2026-07-28`. The official specification
release introduced the stateless protocol core, header-based routing,
cache hints, authorization hardening and the Tasks extension model.
Legacy HTTP+SSE is deprecated in favor of Streamable HTTP.
citeturn0search0turn0search1

FastMCP 4 is currently GA and supports the 2026-07-28 protocol while
retaining compatibility with older clients. citeturn1search0

The implementation must pin exact FastMCP and MCP-compatible dependency
versions in the lockfile rather than tracking an unconstrained latest
version. FastMCP's own release guidance recommends exact version pinning
for production. citeturn2search1
