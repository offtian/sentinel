# Sentinel Architecture

## Overview

Sentinel is an AI-powered automation platform with two core capabilities:

1. **AI SRE** - Automatically triages and investigates production alerts from PagerDuty and Datadog, providing root cause analysis and remediation suggestions
2. **AI Support Agent** - Automatically reviews Jira Service Desk tickets, searches documentation (Notion, Confluence, S3), and drafts response suggestions

![Sentinel Architecture](images/architecture.svg)

*[Edit diagram in Excalidraw](https://excalidraw.com/#json=wRNI3EjHOiZB6RasBgYXH,Rllv-zTRH2clCN2e4ED2gQ)*

## Design Principles

- **Clean Architecture** - Strict layered boundaries enforced by import-linter
- **Async-first** - All I/O uses async/await with asyncio.TaskGroup for parallelism
- **Vendor-agnostic** - Abstract base classes for all external integrations; implementations are swappable
- **Feature-flagged** - Pipeline behaviour controlled via environment variables
- **Observable** - Structured logging (structlog), Datadog APM, Sentry error tracking
- **Testable** - Mock adapters for all external services; no LLM calls in unit tests

## Layer Architecture

```
interfaces/    → FastAPI routers, Pydantic Graph pipelines, webhook handlers, PydanticAI agents, MCP server
  mcp/         → FastMCP server exposing Sentinel tools to external agents
    ↓
application/   → Use cases and orchestration (investigate, triage, review_ticket, search_docs)
  supervisor/  → Quality-gate orchestration (supervise_sre_investigation, supervise_support_review)
    ↓
domain/        → Business entities, search abstractions, vendor adapters, confidence scoring
  sre/         → Alert, Investigation, BaseInvestigationAdapter hierarchy, K8s adapters
  pipeline/    → Pipeline error types (NodeError, PipelineNodeFailed)
  approval/    → ApprovalRequest, ApprovalDecision entities
  supervisor/  → QualityVerdict, SupervisorDecision, quality gate evaluation functions
  evaluation/  → Pipeline-agnostic EvaluationMetrics, ComparisonResult for adapter comparison
    ↓
evals/         → Evaluation framework (pydantic_evals): cases/, evaluators/, runner, reporting, rendering
    ↓
plugins/       → Plugin adapters: toolsets (PydanticAI tool wrappers), MCP client builder
    ↓
data/          → SQLModel database models, Alembic migrations
    ↓
vendors/       → External SDK wrappers (Slack, PagerDuty, Jira, Datadog)
    ↓
utils/         → Logging, shared helpers
    ↓
_config        → Centralised configuration (environs)
```

**Rule**: Lower layers cannot import from higher layers. This is enforced via `import-linter` contracts in `pyproject.toml`.

## AI SRE Pipeline

The SRE investigation pipeline is implemented as a Pydantic Graph with the following nodes:

```
ClassifyAlert
  │  PydanticAI agent classifies severity, service, category
  │  (error → NodeError with context, pipeline continues gracefully)
  ↓
InvestigateWithHolmes
  │  HolmesGPT adapter queries observability systems (Datadog, K8s, Prometheus)
  │  (error → PipelineNodeFailed wrapping NodeError)
  ↓
AnalyseRootCause
  │  PydanticAI agent synthesises findings into root cause + remediation
  ↓
DetermineConfidence
  │  Calculate confidence score; if below threshold → approval gate
  │  (requires human approval via POST .../approve or .../reject)
  ↓
PublishFindings
  │  Posts to Slack, adds PagerDuty incident note, persists to database
  ↓
End(InvestigationReply)
```

### Entry Points
- `POST /api/sre/webhooks/pagerduty` - PagerDuty V3 webhook receiver (dedup via `_handle_webhook()`)
- `POST /api/sre/webhooks/datadog` - Datadog webhook receiver (dedup via `_handle_webhook()`)
- `POST /api/sre/investigate` - Manual investigation trigger
- `POST /api/sre/investigations/{id}/approve` - Approve a pending investigation
- `POST /api/sre/investigations/{id}/reject` - Reject a pending investigation
- `GET /api/sre/investigations/{id}/approval-status` - Check approval status

### PublishFindings Integrations

The `PublishFindings` node is wired to three output channels via the `Dependencies` dataclass:

1. **Slack** - Calls `vendors.slack.post_investigation_summary()` with formatted blocks (controlled by `post_to_slack` flag)
2. **PagerDuty** - Calls `PagerDutyClient.add_incident_note()` with markdown-formatted investigation note (skipped if client not configured)
3. **Database** - Calls an injected `PersistInvestigationFn` callback that saves an `InvestigationRecord` via the application persistence layer

### Investigation Adapter Hierarchy

All investigation backends implement `BaseInvestigationAdapter` (defined in `domain/sre/investigation.py`), which provides a unified contract with typed audit trail:

```
BaseInvestigationAdapter (ABC)           domain/sre/investigation.py
├── BaseHolmesAdapter                    domain/sre/holmes_adapter.py
│   ├── HolmesAdapter (stub)
│   └── DirectToolsetAdapter             Queries Datadog/Grafana directly
└── K8sInvestigationAdapter (ABC)        domain/sre/investigation.py
    ├── NativeK8sAgent                   domain/sre/k8s_native_agent.py
    └── KagentAdapter                    domain/sre/kagent_adapter.py
```

- **DirectToolsetAdapter** — queries observability backends directly (resolves HolmesGPT pydantic-ai dependency conflict)
- **NativeK8sAgent** — PydanticAI agent with kubernetes Python client tools (pods, deployments, events, logs)
- **KagentAdapter** — delegates to kagent K8s operator via CRD creation/polling (skeleton, pending operator deployment)

All adapters return `InvestigationResult` with an `audit_trail` (typed envelope + freeform payload) for hedge fund compliance traceability. Config-driven backend selection via `K8S_INVESTIGATION_BACKEND` (native/kagent/both).

### MCP Integration

Sentinel integrates with MCP (Model Context Protocol) in both directions:

- **MCP Server** (`interfaces/mcp/server.py`) — FastMCP server exposing observability, documentation, and investigation tools to external agents
- **MCP Client** (`plugins/toolsets/mcp.py`) — consumes external MCP servers (e.g., kubectl MCP server) as PydanticAI toolsets, configured via `MCP_SERVERS` env var

## AI Support Pipeline

The support review pipeline follows the same Pydantic Graph pattern:

```
ClassifyTicket
  │  PydanticAI agent classifies category, urgency, extracts key questions + search queries
  │  (error → NodeError with context)
  ↓
SearchDocumentation
  │  Parallel search across Notion, Confluence, S3, and past tickets
  ↓
DraftResponse
  │  PydanticAI agent synthesises documentation into response suggestion
  ↓
DetermineConfidence
  │  Calculate confidence score; approval gate for low-confidence results
  ↓
End(SupportReply)
```

### Entry Points
- `POST /api/support/webhooks/jira` - Jira Service Desk webhook receiver
- `POST /api/support/review` - Manual review trigger

### Search Abstraction

All search backends implement abstract base classes from `domain/search/searcher.py`:

- `BaseDocumentSearcher` - For Notion, Confluence, S3 documents
- `BasePastTicketSearcher` - For searching resolved Jira tickets
- `BaseMetricsSearcher` - For querying metrics/logs (SRE use)

**Note**: Concrete search implementations (NotionSearcher, ConfluenceSearcher, etc.) are planned for Phase 3.

## Vendor Adapters

All vendor adapters live in `domain/vendor_adapters/` and follow a consistent pattern:

- Accept explicit constructor parameters or fall back to `settings` defaults
- Expose an `is_configured` property — operations are no-ops when not configured
- All methods are async-safe

### Observability (pluggable backend)

`domain/vendor_adapters/observability/` implements the `BaseObservabilityClient` ABC with two backends:

| Backend | SDK | When Used | Query Languages |
|---------|-----|-----------|-----------------|
| `DatadogClient` | `datadog-api-client` | Production (`OBSERVABILITY_BACKEND=datadog`) | Datadog query syntax |
| `GrafanaClient` | `httpx` | Local dev / open-source (`OBSERVABILITY_BACKEND=grafana`) | PromQL, LogQL, TraceQL |

When `OBSERVABILITY_BACKEND` is unset, auto-selects Grafana for `ENVIRONMENT=localdev` and Datadog otherwise.

The `GrafanaClient` queries Prometheus (metrics), Loki (logs), and Tempo (traces) through Grafana's unified `/api/ds/query` endpoint. One URL, one API token.

### Other Vendor Adapters

| Adapter | SDK | Key Methods |
|---------|-----|-------------|
| `PagerDutyClient` | `pdpyras` | `add_incident_note()`, `get_incident()`, `update_incident_status()` |
| `JiraClient` | `jira` | `get_issue()`, `search_issues()`, `add_internal_comment()`, `transition_issue()` |
| `ConfluenceClient` | `atlassian-python-api` | `search()`, `get_page_content()` |

## PydanticAI Agents

| Agent | Purpose | Default Model |
|-------|---------|---------------|
| `alert_classifier` | Classify alert severity, service, category | GPT-4.1-mini |
| `root_cause_analyser` | Synthesise findings into root cause + remediation | GPT-4.1 |
| `k8s_investigator` | Diagnose K8s incidents using cluster state tools | GPT-4.1 |
| `ticket_reviewer` | Classify ticket, extract questions, generate search queries | GPT-4.1-mini |
| `response_drafter` | Draft customer response from documentation | GPT-4.1 |

All agents are defined with `model="test"` at module level to avoid import-time validation. The actual model is injected at runtime via `model=utils.get_model_with_gateway(_config.MODEL_SETTING)`.

All agents route through a LiteLLM gateway for model management, cost tracking, and fallback.

## Configuration

Two modules:

- **`settings.py`** — Pydantic `Settings` class with env var overrides, `get_settings()` singleton
- **`config.py`** — Application-level `Configuration` class with `load_vendors()`, `build_holmes_adapter()`, `build_k8s_investigation_adapter()`, `build_document_searcher()`, LLM model name properties

Key settings groups:

- **Environment** - `ENVIRONMENT` (`localdev`/`production`), `DATABASE_URL`
- **Observability** - `OBSERVABILITY_BACKEND` (auto: grafana for localdev, datadog for production), Datadog or Grafana credentials
- **LLM models** - Per-agent model selection via LiteLLM gateway
- **SRE config** - PagerDuty API key, HolmesGPT toggle
- **Support config** - Jira/Confluence URLs and tokens
- **Feature flags** - `SRE_AUTO_INVESTIGATE`, `SUPPORT_AUTO_DRAFT`
- **Approval** - `REQUIRE_APPROVAL_BELOW_CONFIDENCE` (default 0.7), `APPROVAL_TIMEOUT_SECONDS` (default 0)
- **K8s agent** - `K8S_INVESTIGATION_BACKEND` (native/kagent/both/disabled), `K8S_INVESTIGATOR_LLM`, cluster/namespace config
- **MCP** - `MCP_SERVERS` (JSON list of HTTP/stdio server configs), `K8S_MCP_SERVER_URL`, `MCP_SERVER_PORT`
- **Slack** - Bot token, app token, channel IDs

## Database

PostgreSQL with SQLModel (async via asyncpg). Tables:

| Table | Purpose |
|-------|---------|
| `investigation_records` | Persisted SRE investigations with findings, root cause, confidence, trace_id |
| `ticket_review_records` | Persisted support reviews with suggested responses, sources, feedback status, trace_id |
| `job_requests` | PostgreSQL-backed job queue with FOR UPDATE SKIP LOCKED, trace_id |
| `job_results` | Job execution outcomes (completed/failed/timed_out) |
| `audit_log` | Append-only regulatory audit trail with SHA-256 input hashes |
| `comparison_runs` | Side-by-side investigation backend comparison results |
| `eval_runs` | Evaluation framework execution records |
| `pipeline_runs` | Pipeline execution traces linked by trace_id |
| `node_executions` | Per-node execution traces within a pipeline run |
| `agent_calls` | PydanticAI agent invocation records with message history |

Migrations managed by Alembic.

## Database Access

Two async database clients coexist:

- **`data/database.py`** (SQLAlchemy AsyncSession) — Session factory with connection pooling (`pool_size=5`, `max_overflow=10`). Used by application layer.
- **`data/db.py`** (`databases.Database` singleton) — `get_db()` returns a cached instance; `connect_db()`/`disconnect_db()` manage lifecycle. Used by domain layer queries/operations.

Both are initialised during FastAPI lifespan and worker startup.

## Persistence Layer

Persistence functions live in the **domain layer**, organised by category:

| Domain | Reads | Writes |
|--------|-------|--------|
| SRE | `domain/sre/queries.py` | `domain/sre/operations.py` |
| Support | `domain/support/queries.py` | `domain/support/operations.py` |
| Audit | — | `domain/audit/operations.py` |
| Jobs | `domain/jobs/queries.py` | `domain/jobs/operations.py` |
| Evaluation | `domain/evaluation/queries.py` | `domain/evaluation/operations.py` |
| Pipeline Tracing | `domain/pipeline/queries.py` | `domain/pipeline/operations.py` |

All functions accept `db: databases.Database` as an explicit keyword argument and use SQLAlchemy Core expressions (`select()`, `insert()`, `update()`) with SQLModel classes for type-safe queries.

## Pipeline Traceability

A `trace_id` UUID propagates through three levels, enabling end-to-end correlation across all tables:

```
pipeline_runs (trace_id, pipeline_type, status, duration_ms)
  └── node_executions (trace_id, node_name, node_order, status, duration_ms)
       └── agent_calls (trace_id, agent_name, model_id, messages_json, token_usage_json)
```

**Domain types:** `data/tracing_models.py` defines `PipelineRunRecord`, `NodeExecutionRecord`, `AgentCallRecord`.

**ExecutionTracer** (`domain/pipeline/tracer.py`, pending) — DB-backed replacement for the in-memory `TraceCollector`. Satisfies the same `.record()` interface for backward compatibility with the Streamlit chat UI.

## Deployment

## Helm Chart

Located in `helm/sentinel/`. Multi-deployment chart supporting:

- **api** deployment - FastAPI server (webhook receivers + API endpoints)
- **worker** deployment - Background task processor (optional, configurable via `values.yaml`)
- **mcp-server** deployment - FastMCP server exposing tools (optional, `mcpServer.enabled`)
- **migration-job** - Pre-install/upgrade Helm hook running `alembic upgrade head`
- **ClusterRole/ClusterRoleBinding** - Read-only K8s API access for investigation agent (optional, `k8sAgent.enabled`)
- **HPA** - Per-deployment horizontal pod autoscaler (configurable min/max replicas, CPU target)
- **PDB** - Pod disruption budget (`minAvailable: 1`)
- **Ingress** - AWS ALB with optional Zscaler security group
- **ServiceAccount** - With IRSA annotation for AWS IAM integration

## CI/CD (CircleCI)

Pipeline: `mypy` → `test-and-lint` → `publish-image` → `package-chart`

- Uses PostgreSQL sidecar for integration tests
- Publishes Docker image and Helm chart to ECR as OCI artifacts
- Uses `krakentech/ktl-services-deployment-orb` for chart packaging

## GitOps

Deployed to Kubernetes via ArgoCD through `ktl-services-deployment` repository:

- Secrets encrypted via SOPS + KMS
- IAM roles via IRSA for AWS service access
- Datadog APM for observability

## Test Count

555+ tests covering:

- Domain entities and operations (SRE, Support, Confidence, Search, Pipeline errors, Approval, Supervisor)
- Webhook parsers (PagerDuty, Datadog) with dedup handling
- Vendor adapters (Datadog, PagerDuty, Jira, Confluence)
- DirectToolsetAdapter (14 tests)
- Persistence layer (database session management)
- API routers (support feedback, SRE approval endpoints)
- API app lifecycle
- Slack message formatting
- Pipeline node error handling tests
- Approval gate and supervisor quality gate tests
- Evaluation framework (pydantic_evals based)
- Domain layer persistence (queries and operations via databases library)
- Pipeline traceability (trace_id correlation, pipeline/node/agent recording)
