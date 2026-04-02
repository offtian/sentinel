# Sentinel Architecture

## Overview

Sentinel is an AI-powered automation platform with two core capabilities:

1. **AI SRE** - Automatically triages and investigates production alerts from PagerDuty and Datadog, providing root cause analysis and remediation suggestions
2. **AI Support Agent** - Automatically reviews Jira Service Desk tickets, searches documentation (Notion, Confluence, S3), and drafts response suggestions

## Design Principles

- **Clean Architecture** - Strict layered boundaries enforced by import-linter
- **Async-first** - All I/O uses async/await with asyncio.TaskGroup for parallelism
- **Vendor-agnostic** - Abstract base classes for all external integrations; implementations are swappable
- **Feature-flagged** - Pipeline behaviour controlled via environment variables
- **Observable** - Structured logging (structlog), Datadog APM, Sentry error tracking
- **Testable** - Mock adapters for all external services; no LLM calls in unit tests

## Layer Architecture

```
interfaces/    → FastAPI routers, Pydantic Graph pipelines, webhook handlers, PydanticAI agents
    ↓
application/   → Use cases and orchestration (investigate, triage, review_ticket, search_docs)
  supervisor/  → Quality-gate orchestration (supervise_sre_investigation, supervise_support_review)
    ↓
domain/        → Business entities, search abstractions, vendor adapters, confidence scoring
  pipeline/    → Pipeline error types (NodeError, PipelineNodeFailed)
  approval/    → ApprovalRequest, ApprovalDecision entities
  supervisor/  → QualityVerdict, SupervisorDecision, quality gate evaluation functions
    ↓
evals/         → Evaluation framework (pydantic_evals): cases/, evaluators/, runner, reporting, rendering
    ↓
plugins/       → Plugin adapters: toolsets (PydanticAI tool wrappers), prompts (Jinja2 templates)
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

### HolmesGPT Integration (Hybrid Approach)

HolmesGPT has a dependency conflict with pydantic-ai>=1.0.7 (pinned mcp versions). Our approach:

1. **Adapter pattern** (`domain/sre/holmes_adapter.py`) defines `BaseHolmesAdapter` ABC
2. **Production adapter** wraps HolmesGPT's toolsets for data gathering
3. **Mock adapter** (`tests/factories/`) provides test fixtures without SDK dependency
4. **Our pipeline** handles orchestration, analysis, confidence scoring, and output formatting

When the upstream dependency conflict is resolved, the production adapter will import HolmesGPT's `ToolExecutor`, `DatadogToolset`, `KubernetesToolset`, and `PrometheusToolset` directly.

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
| `ticket_reviewer` | Classify ticket, extract questions, generate search queries | GPT-4.1-mini |
| `response_drafter` | Draft customer response from documentation | GPT-4.1 |

All agents are defined with `model="test"` at module level to avoid import-time validation. The actual model is injected at runtime via `model=utils.get_model_with_gateway(_config.MODEL_SETTING)`.

All agents route through a LiteLLM gateway for model management, cost tracking, and fallback.

## Configuration

Two modules:

- **`settings.py`** — Pydantic `Settings` class with env var overrides, `get_settings()` singleton
- **`config.py`** — Application-level `Configuration` class with `load_vendors()`, `build_holmes_adapter()`, `build_document_searcher()`, LLM model name properties

Key settings groups:

- **Environment** - `ENVIRONMENT` (`localdev`/`production`), `DATABASE_URL`
- **Observability** - `OBSERVABILITY_BACKEND` (auto: grafana for localdev, datadog for production), Datadog or Grafana credentials
- **LLM models** - Per-agent model selection via LiteLLM gateway
- **SRE config** - PagerDuty API key, HolmesGPT toggle
- **Support config** - Jira/Confluence URLs and tokens
- **Feature flags** - `SRE_AUTO_INVESTIGATE`, `SUPPORT_AUTO_DRAFT`
- **Approval** - `REQUIRE_APPROVAL_BELOW_CONFIDENCE` (default 0.7), `APPROVAL_TIMEOUT_SECONDS` (default 0)
- **Slack** - Bot token, app token, channel IDs

## Database

PostgreSQL with SQLModel (async via asyncpg). Two main tables:

- `investigation_records` - Persisted SRE investigations with findings, root cause, confidence
- `ticket_review_records` - Persisted support reviews with suggested responses, sources, feedback status

Migrations managed by Alembic.

## Session Management

`data/database.py` provides:

- `get_engine()` / `get_session_factory()` - Lazy-initialised singletons (async engine with `pool_pre_ping`, `pool_size=5`, `max_overflow=10`)
- `get_session()` - Async generator yielding `AsyncSession` (use in FastAPI dependencies or `async for`)
- `close_engine()` - Called during FastAPI lifespan shutdown

## Persistence Layer

- `application/sre/persist.py` - `save_investigation()`, `get_investigation()`, `get_investigations_for_service()`, `get_investigations_by_alert_id()`
- `application/support/persist.py` - `save_ticket_review()`, `get_ticket_review()`, `get_reviews_for_ticket()`, `update_review_status()`

Both use `sqlmodel.col()` for type-safe column references in WHERE and ORDER BY clauses.

Migrations managed by Alembic.

## Deployment

## Helm Chart

Located in `helm/sentinel/`. Multi-deployment chart supporting:

- **api** deployment - FastAPI server (webhook receivers + API endpoints)
- **worker** deployment - Background task processor (optional, configurable via `values.yaml`)
- **migration-job** - Pre-install/upgrade Helm hook running `alembic upgrade head`
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

295+ tests covering:

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
