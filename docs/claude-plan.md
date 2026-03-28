# Sentinel: AI SRE + AI Support Agent

## Context

The team needs two AI-powered automation capabilities:

1. **AI SRE** - Automatically triage and investigate production alerts from PagerDuty + Datadog, provide root cause analysis and remediation suggestions
2. **AI Support Agent** - Automatically review Jira Service Desk tickets, search mixed documentation (Notion, Confluence, S3), and suggest responses

These live in the `sentinel` repository following clean architecture patterns. The AI SRE uses a **hybrid approach** with HolmesGPT (their investigation engine wrapped in our Pydantic Graph pipeline).

---

## Current Status (as of 2026-03-28)

Sentinel is deployed and running in Kubernetes:

```
sentinel-api        1/1  Running
sentinel-worker     1/1  Running
sentinel-postgres   1/1  Running
```

### What's Built

| Area | Status | Notes |
|------|--------|-------|
| SRE investigation pipeline (5-node graph) | Done | ClassifyAlert -> InvestigateWithHolmes -> AnalyseRootCause -> DetermineConfidence -> PublishFindings |
| Support review pipeline (4-node graph) | Done | ClassifyTicket -> SearchDocumentation -> DraftResponse -> DetermineConfidence |
| All PydanticAI agents | Done | alert_classifier, root_cause_analyser, ticket_reviewer, response_drafter |
| Async worker with PostgreSQL job queue | Done | `SELECT ... FOR UPDATE SKIP LOCKED`, retry, timeout, recovery |
| API routers (webhooks + manual triggers) | Done | All return 202 Accepted with job_id |
| Webhook parsers (PagerDuty, Datadog, Jira) | Done | |
| Vendor adapters (PagerDuty, Datadog, Jira, Confluence) | Done | |
| Search implementations | Done | Confluence, Jira past tickets, Datadog metrics, mocks + factory |
| Slack integration | Done | Investigation summaries + support suggestions + Socket Mode chatbot |
| Confidence scoring | Done | High/Medium/Low thresholds (naive -- needs multi-factor improvement) |
| Investigation persistence | Done | save/get/query in PostgreSQL |
| Support persistence | Done | save/get/query wired into worker via `persist_fn` |
| Feedback API | Done | `POST /api/support/reviews/{id}/feedback` with accepted/rejected/modified statuses |
| Retrieval API | Done | `GET /api/sre/investigations/{id}`, `GET /api/support/reviews/{id}` |
| DirectToolsetAdapter | Done | Queries Datadog logs, metrics, traces concurrently; replaces HolmesGPT stub |
| Audit logging | Done | Append-only for compliance |
| Circuit breaker | Done | Thread-safe with configurable thresholds |
| Helm chart | Done | API + worker deployments, CronJob template, migration hook, HPA, PDB, NetworkPolicy |
| Database migrations | Done | job_requests, job_results, audit_log tables |
| LiteLLM config | Done | Routes to local Ollama for dev |
| Prompt templates | Done | Jinja2 `.j2` files in `plugins/prompts/`, loaded at agent init |
| Config via Pydantic Settings | Done | Replaced environs with `pydantic-settings`; backward-compatible `_config.DATABASE_URL` access |
| Test suite | Done | 116 unit tests, integration, functional with factories |

### Key Gaps

| Gap | Impact |
|-----|--------|
| No CI/CD pipeline | No `.circleci/` or `.github/workflows/` in repo |
| No evaluation framework | No way to measure or regress LLM output quality |
| No distributed tracing | structlog events exist but no ddtrace/OTEL spans |
| No scheduled automations | CronJob template ready but empty, no `--run-once` worker mode |
| Bedrock KB / Notion searcher not implemented | Config option exists but no code |
| mypy errors from `_config.__getattr__` | Returns `object` type; vendor adapter call sites need `cast()` or typed accessors |

---

## Repository Structure

```
sentinel/
├── src/sentinel/
│   ├── _config.py                    # Centralised config (pydantic-settings)
│   ├── main.py                       # Entrypoint
│   ├── version/
│   │   └── __init__.py
│   │
│   ├── interfaces/                   # Layer 1: Entry points
│   │   ├── api/                      # FastAPI app
│   │   │   ├── app.py
│   │   │   └── routers/
│   │   │       ├── sre/              # SRE endpoints (webhook receivers, investigation triggers)
│   │   │       └── support/          # Support endpoints (ticket processing, suggestion retrieval)
│   │   ├── graphs/                   # Pydantic Graph pipelines
│   │   │   ├── sre_investigation.py  # Alert triage → investigate → analyse → respond
│   │   │   ├── support_review.py     # Ticket intake → search docs → draft response
│   │   │   └── agents/              # PydanticAI agent definitions
│   │   │       ├── alert_classifier.py
│   │   │       ├── root_cause_analyser.py
│   │   │       ├── ticket_reviewer.py
│   │   │       ├── response_drafter.py
│   │   │       └── utils.py          # LiteLLM gateway helper
│   │   └── webhooks/                 # PagerDuty/Datadog webhook handlers
│   │       ├── pagerduty.py
│   │       └── datadog.py
│   │
│   ├── application/                  # Layer 2: Use cases
│   │   ├── sre/
│   │   │   ├── investigate.py        # Orchestrates HolmesGPT + custom pipeline
│   │   │   └── triage.py             # Alert dedup, severity classification
│   │   └── support/
│   │       ├── review_ticket.py      # Ticket analysis use case
│   │       └── search_docs.py        # Multi-source document retrieval
│   │
│   ├── domain/                       # Layer 3: Business logic
│   │   ├── sre/
│   │   │   ├── entities.py           # Alert, Investigation, RootCause, Remediation
│   │   │   ├── holmes_adapter.py     # HolmesGPT SDK wrapper
│   │   │   └── operations.py         # Investigation lifecycle management
│   │   ├── support/
│   │   │   ├── entities.py           # Ticket, ResponseSuggestion, DocSource
│   │   │   └── operations.py         # Ticket review lifecycle
│   │   ├── confidence/               # Shared confidence scoring
│   │   │   └── entities.py
│   │   ├── search/                   # Shared search abstractions
│   │   │   └── searcher.py           # BaseDocumentSearcher, BaseMetricsSearcher
│   │   └── vendor_adapters/          # Vendor-specific implementations
│   │       ├── pagerduty.py          # PagerDuty API client
│   │       ├── datadog_client.py     # Datadog API (logs, metrics, traces)
│   │       ├── jira.py               # Jira Service Desk client
│   │       ├── notion.py             # Notion search (S3-backed approach)
│   │       ├── confluence.py         # Confluence REST API client
│   │       └── s3.py                 # S3 document retrieval
│   │
│   ├── data/                         # Layer 4: Persistence
│   │   ├── models.py                 # SQLModel table definitions
│   │   └── migrations/               # Alembic migrations
│   │
│   ├── vendors/                      # Layer 5: External SDK wrappers
│   │   ├── pagerduty.py              # pdpyras or REST client
│   │   ├── datadog.py                # datadog-api-client
│   │   ├── jira.py                   # jira or atlassian-python-api
│   │   ├── slack.py                  # slack_bolt (for posting findings)
│   │   └── holmes.py                 # holmesgpt SDK init
│   │
│   ├── utils/
│   │   └── logs.py                   # structlog + Datadog injection
│   └── bootstrap.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── functional/
│   └── factories/
│
├── helm/sentinel/                    # Helm chart
├── .circleci/config.yml
├── pyproject.toml
├── Makefile
├── Dockerfile
├── CLAUDE.md
└── README.md
```

### Import-Linter Contracts (in pyproject.toml)

```toml
[tool.importlinter]
root_package = "sentinel"

[[tool.importlinter.contracts]]
name = "Top level layers"
type = "layers"
exhaustive = true
containers = ["sentinel"]
layers = [
    "main",
    "interfaces",
    "application",
    "domain",
    "data",
    "vendors",
    "utils",
    "bootstrap",
    "_config",
    "version",
]
```

---

## Project 1: AI SRE - Investigation Pipeline <!-- STATUS: DONE (except HolmesGPT SDK) -->

### Graph Pipeline: `sre_investigation.py` <!-- DONE -->

Follows the Pydantic Graph pattern (State, Dependencies, BaseNode, GraphRunContext):

```
ReceiveAlert
  → ClassifyAlert (PydanticAI agent: severity, service, category)
  → InvestigateWithHolmes (HolmesGPT SDK: query logs, metrics, traces, K8s)
  → AnalyseRootCause (PydanticAI agent: synthesise findings into root cause hypothesis)
  → DetermineConfidence (shared confidence scoring)
  → PublishFindings (write back to PagerDuty + Slack)
```

### HolmesGPT Hybrid Integration <!-- STUB — dependency conflict with pydantic-ai -->

**`domain/sre/holmes_adapter.py`** wraps HolmesGPT's investigation engine:

```python
# Use HolmesGPT's toolsets for data gathering
from holmes.core.tools import ToolExecutor
from holmes.plugins.toolsets import (
    DatadogToolset,
    KubernetesToolset,
    PrometheusToolset,
)

# But orchestrate via our Pydantic Graph pipeline
# and use our PydanticAI agents for analysis/synthesis
```

Key HolmesGPT components to leverage:
- **Toolsets** - Pre-built integrations for Datadog, K8s, Prometheus (avoid reimplementing)
- **Investigation engine** - Their agentic loop for autonomous data gathering
- **Server-side filtering** - Their approach to managing large log/metric volumes

Custom wrapper responsibilities:
- Convert HolmesGPT output into our domain entities (`Investigation`, `RootCause`)
- Feed gathered context into our PydanticAI `root_cause_analyser` agent for synthesis
- Apply our confidence scoring framework
- Format output for our Slack/PagerDuty integrations

> **Update:** HolmesGPT SDK has a dependency conflict with pydantic-ai>=1.0.7.
> The adapter currently returns placeholder text when enabled. Phase A replaces
> this with a `DirectToolsetAdapter` that calls the existing DatadogClient and
> PagerDutyClient vendor adapters directly, bypassing the HolmesGPT SDK.

### Webhook Receivers <!-- DONE -->

**PagerDuty webhook** (`interfaces/webhooks/pagerduty.py`):
- Receives V3 webhook events (incident.triggered, incident.acknowledged)
- Normalises into internal `Alert` entity
- Triggers investigation graph via async task or direct invocation

**Datadog webhook** (`interfaces/webhooks/datadog.py`):
- Receives Datadog webhook payloads (alert transitions)
- Normalises into internal `Alert` entity

### Domain Entities <!-- DONE -->

```python
# domain/sre/entities.py

class AlertSeverity(enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class Alert(BaseModel):
    id: str
    source: Literal["pagerduty", "datadog"]
    title: str
    description: str
    severity: AlertSeverity
    service: str
    triggered_at: datetime
    raw_payload: dict[str, Any]

class Investigation(BaseModel):
    id: uuid.UUID
    alert: Alert
    status: Literal["pending", "investigating", "completed", "failed"]
    findings: list[Finding]
    root_cause: str | None
    remediation: str | None
    confidence: ConfidenceScore | None
    started_at: datetime
    completed_at: datetime | None

class Finding(BaseModel):
    source: str          # "datadog_logs", "kubernetes", "prometheus"
    summary: str
    raw_data: str | None
    relevance: float     # 0-1
```

### PydanticAI Agents <!-- DONE -->

1. **`alert_classifier`** - Classifies alert severity, affected service, alert category. Uses GPT-4.1-mini via LiteLLM.
2. **`root_cause_analyser`** - Synthesises HolmesGPT findings + alert context into root cause hypothesis and remediation steps. Uses GPT-4.1 or Claude Opus via LiteLLM.

### Output Channels <!-- DONE -->

- **Slack**: Post investigation summary to incident channel (using slack_bolt)
- **PagerDuty**: Add investigation notes to incident via API
- **Persisted**: Store investigation results in PostgreSQL for historical analysis

---

## Project 2: AI Support Agent - Ticket Review Pipeline <!-- STATUS: DONE (persistence wiring gap) -->

### Graph Pipeline: `support_review.py` <!-- DONE -->

```
ReceiveTicket
  → ClassifyTicket (PydanticAI agent: category, urgency, required expertise)
  → SearchDocumentation (parallel: Notion, Confluence, S3 docs, past tickets)
  → DraftResponse (PydanticAI agent: synthesise docs into response suggestion)
  → DetermineConfidence (shared confidence scoring)
  → PublishSuggestion (write draft to Jira comment or Slack)
```

### Vendor Adapters <!-- DONE -->

**`vendor_adapters/jira.py`** - Jira Service Desk client:
- Fetch new/updated tickets via JQL or webhook
- Read ticket fields (summary, description, comments, attachments, custom fields)
- Post internal comments with response suggestions
- Transition ticket status

**`vendor_adapters/notion.py`** - S3-backed Notion search: <!-- GAP: not implemented -->
- Notion content indexed into S3 buckets
- Shared search backend via Bedrock KB / Kendra

**`vendor_adapters/confluence.py`** - Confluence REST API: <!-- DONE -->
- Search via CQL (Confluence Query Language)
- Retrieve page content as structured text
- Handle space-scoped searches

### Search Abstraction <!-- DONE (except Notion/S3/Bedrock KB) -->

Multi-source search via `BaseDocumentSearcher` pattern:

```python
# domain/search/searcher.py

class BaseDocumentSearcher(abc.ABC):
    @abc.abstractmethod
    async def search(self, *, query: str, limit: int) -> list[DocumentSearchResult]: ...

class BasePastTicketSearcher(abc.ABC):
    @abc.abstractmethod
    async def search(self, *, query: str, limit: int) -> list[TicketSearchResult]: ...
```

Implementations:
- `NotionSearcher` - Queries Bedrock KB / Kendra <!-- GAP: not implemented -->
- `ConfluenceSearcher` - Queries Confluence REST API <!-- DONE -->
- `S3DocumentSearcher` - Direct S3 object retrieval for indexed docs <!-- GAP: not implemented -->
- `JiraPastTicketSearcher` - Searches resolved tickets for similar issues <!-- DONE -->
- `DatadogMetricsSearcher` - Queries logs and metrics concurrently <!-- DONE (not in original plan) -->
- `MockSearchers` - Canned results for offline development <!-- DONE (not in original plan) -->

### Domain Entities <!-- DONE -->

```python
# domain/support/entities.py

class Ticket(BaseModel):
    id: str
    key: str               # e.g. "SUPPORT-1234"
    summary: str
    description: str
    reporter: str
    priority: str
    created_at: datetime
    labels: list[str]
    comments: list[TicketComment]
    raw_payload: dict[str, Any]

class ResponseSuggestion(BaseModel):
    ticket_id: str
    suggested_response: str
    sources: list[DocSource]
    confidence: ConfidenceScore
    category: str
    created_at: datetime

class DocSource(BaseModel):
    title: str
    url: str
    source_type: Literal["notion", "confluence", "s3", "jira"]
    excerpt: str
    relevance: float
```

### PydanticAI Agents <!-- DONE -->

1. **`ticket_reviewer`** - Classifies ticket category, urgency, required domain expertise. Extracts key questions from the ticket. Uses GPT-4.1-mini.
2. **`response_drafter`** - Synthesises search results into a professional response suggestion, citing sources. Uses GPT-4.1 or Claude Opus.

### Trigger Modes <!-- DONE -->

- **Webhook**: Jira webhook on ticket creation/update → triggers review pipeline
- **Polling**: Periodic JQL query for unreviewed tickets (fallback)
- **API**: Manual trigger via FastAPI endpoint (for testing/backfill)

---

## Shared Components <!-- STATUS: DONE -->

### LiteLLM Gateway <!-- DONE -->

Reuse existing LiteLLM deployment at `http://litellm.litellm.svc.cluster.local/`:

```python
# interfaces/graphs/agents/utils.py
def get_model_with_gateway(model_name: str) -> str:
    return f"litellm_proxy/{model_name}"
```

### Configuration (`_config.py`) <!-- DONE -->

Pydantic Settings configuration:

```python
class Config:
    # Environment
    ENVIRONMENT: str = env.str("ENVIRONMENT", default="production")
    DATABASE_URL: str = env.str("DATABASE_URL")

    # LLM models (via LiteLLM gateway)
    AI_GATEWAY_URL: str = env.str("AI_GATEWAY_URL")
    ALERT_CLASSIFIER_LLM: str = env.str("ALERT_CLASSIFIER_LLM", default="openai/gpt-4.1-mini")
    ROOT_CAUSE_LLM: str = env.str("ROOT_CAUSE_LLM", default="openai/gpt-4.1")
    TICKET_REVIEWER_LLM: str = env.str("TICKET_REVIEWER_LLM", default="openai/gpt-4.1-mini")
    RESPONSE_DRAFTER_LLM: str = env.str("RESPONSE_DRAFTER_LLM", default="openai/gpt-4.1")

    # SRE config
    PAGERDUTY_API_KEY: str = env.str("PAGERDUTY_API_KEY", default="")
    DATADOG_API_KEY: str = env.str("DATADOG_API_KEY", default="")
    DATADOG_APP_KEY: str = env.str("DATADOG_APP_KEY", default="")
    HOLMESGPT_ENABLED: bool = env.bool("HOLMESGPT_ENABLED", default=True)

    # Support config
    JIRA_BASE_URL: str = env.str("JIRA_BASE_URL", default="")
    JIRA_API_TOKEN: str = env.str("JIRA_API_TOKEN", default="")
    CONFLUENCE_BASE_URL: str = env.str("CONFLUENCE_BASE_URL", default="")

    # Shared search
    DOCUMENT_SEARCHER: str = env.str("DOCUMENT_SEARCHER", default="bedrock_knowledge_base")

    # Feature flags
    SRE_AUTO_INVESTIGATE: bool = env.bool("SRE_AUTO_INVESTIGATE", default=True)
    SUPPORT_AUTO_DRAFT: bool = env.bool("SUPPORT_AUTO_DRAFT", default=True)

    # Slack (for posting findings)
    SLACK_BOT_TOKEN: str = env.str("SLACK_BOT_TOKEN", default="")
    SRE_SLACK_CHANNEL: str = env.str("SRE_SLACK_CHANNEL", default="")
    SUPPORT_SLACK_CHANNEL: str = env.str("SUPPORT_SLACK_CHANNEL", default="")

    # Observability
    DD_SERVICE: str = "sentinel"
    DD_ENV: str = env.str("DD_ENV", default="production")
```

### Database Schema (SQLModel) <!-- DONE -->

```python
# data/models.py

class InvestigationRecord(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    alert_source: str
    alert_id: str
    alert_title: str
    severity: str
    service: str
    status: str
    root_cause: str | None
    remediation: str | None
    confidence_score: float | None
    findings_json: dict | None = Field(sa_column=Column(JSON))
    started_at: datetime
    completed_at: datetime | None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class TicketReviewRecord(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    ticket_id: str
    ticket_key: str
    suggested_response: str
    sources_json: dict | None = Field(sa_column=Column(JSON))
    confidence_score: float | None
    category: str | None
    status: str           # "drafted", "accepted", "rejected", "modified"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    reviewed_at: datetime | None
```

---

## Deployment <!-- STATUS: PARTIAL — local K8s done, production not deployed -->

### Infrastructure

Infrastructure Terraform configuration (separate repo):
- ECR repos: `sentinel`, `sentinel-helm`
- IAM role with S3 access (shared doc buckets), Secrets Manager
- ACM certificate for sentinel subdomain
- KMS key for SOPS

### Kubernetes (ktl-services-deployment) <!-- GAP: not in this repo -->

```
services-eks-test/applications/sentinel/
├── config.yaml          # chartName: sentinel-helm
├── image-tag-value.yaml # ECR image tag
├── values.yaml          # Environment config, deployments
├── values.encrypted.yaml # PagerDuty/Jira/Datadog API keys
└── .sops.yaml
```

Deployments within the Helm chart: <!-- DONE -->
1. **api** - FastAPI server (webhook receivers + API)
2. **worker** - Background task processor (async PostgreSQL job queue, not Celery)

### CI/CD (CircleCI) <!-- GAP: no config in repo -->

CI/CD pipeline:
1. `mypy` → `test-and-lint` → `publish-image` → `package_chart_and_deploy`
2. Auto-deploy to test, approval-gated for prod
3. Add to `.github/approveman.yml` for CD

---

## Architecture Decisions

### Execution Model

- **PostgreSQL job queue** with `SELECT ... FOR UPDATE SKIP LOCKED` instead of Redis/RabbitMQ/Celery. Supports horizontal scaling by adding worker replicas. No additional infrastructure dependency.
- **Async/await throughout** -- no blocking I/O, no Celery task serialisation overhead.
- **Worker replicas** scale independently from API replicas. Helm HPA configured (disabled by default).

### Scheduled Automations

Scheduled automations live in Sentinel, triggered by Kubernetes CronJobs. The worker gets a `--run-once` mode for CronJob execution. This avoids introducing a new runtime (kagent, Temporal, Argo Workflows) until the need is proven. For Toby's "every Thursday at 5pm" use case, a CronJob creates a K8s Job that hits the Sentinel API, which enqueues a `SCHEDULED_AUTOMATION` job for the worker.

### HolmesGPT Resolution

HolmesGPT SDK has a dependency conflict with pydantic-ai>=1.0.7. Rather than waiting for an upstream fix, Phase A introduces a `DirectToolsetAdapter` that calls the existing DatadogClient and PagerDutyClient vendor adapters directly. This gives real observability data without the SDK. HolmesGPT can be reconsidered later.

### OSS Framework Positioning

| Framework | Position |
|-----------|----------|
| PydanticAI + Pydantic Graph | Keep -- current orchestration layer, well-integrated |
| LiteLLM | Keep -- model routing gateway, already deployed |
| LangFuse | Adopt via LiteLLM callbacks (Phase B) -- no direct dependency |
| kagent | Narrow pilot for K8s troubleshooting (Phase D) -- not a replacement |
| agentgateway | Defer until multiple MCP backends or agent runtimes need centralised routing |
| Claude Agent SDK / OpenAI Agent SDK | Evaluate in Phase D -- may complement PydanticAI for specific workflows |
| LangGraph / LangChain | Evaluate in Phase D -- compare with Pydantic Graph for complex branching |
| FastMCP | Adopt in Phase C for tool integration |

### Quality Over Time

Quality improvement relies on three feedback loops:
1. **LangFuse** -- per-call cost, latency, and prompt versioning (Phase B)
2. **Eval framework** -- golden test cases with automated scoring, run on prompt changes (Phase B)
3. **Feedback API** -- track accept/reject/modify rates on support suggestions (Phase A/B)

---

## Dependencies <!-- DONE -->

```toml
[project]
dependencies = [
    # Core
    "environs",
    "fastapi[standard]",
    "sqlalchemy[asyncio]",
    "sqlmodel",
    "asyncpg",
    "alembic",
    "pydantic-ai>=1.0.7",
    "structlog",

    # HolmesGPT
    "holmesgpt",

    # Vendors
    "pdpyras",                    # PagerDuty
    "datadog-api-client",         # Datadog
    "jira",                       # Jira (or atlassian-python-api)
    "atlassian-python-api",       # Confluence
    "boto3",                      # AWS S3/Bedrock
    "aioboto3",
    "slack_bolt",                 # Slack

    # Observability
    "ddtrace",
    "sentry-sdk",
]
```

---

## Phased Delivery

### Completed Phases (Original Plan)

**Phase 1: Bootstrap + AI SRE Core** -- COMPLETE
- All deliverables shipped. HolmesGPT adapter is a stub (dependency conflict).

**Phase 2: AI SRE Polish + Deployment** -- COMPLETE (gaps: CI/CD, prod infra)
- All core deliverables shipped. CI/CD pipeline and OctoCloud/Terraform infra not in this repo.

**Phase 3: AI Support Agent Core** -- COMPLETE
- All deliverables shipped. Notion/S3/Bedrock KB searchers not implemented.

**Phase 4: AI Support Agent Polish + E2E** -- PARTIAL
- Past ticket search, confidence scoring, functional tests done.
- Support persistence, feedback API, evals, production deployment not done.

### Additional Work Completed (beyond original plan)

- Async worker with PostgreSQL job queue (`SELECT ... FOR UPDATE SKIP LOCKED`)
- Job entities, enqueue/dequeue/retry/recovery logic
- Audit logging (append-only for financial services compliance)
- Circuit breaker resilience pattern
- Slack Socket Mode chatbot with intent routing
- CronJob Helm template (parameterised but empty)
- Local K8s deployment (`make k8s-up`)
- DatadogMetricsSearcher and search factory pattern

---

### Updated Roadmap

### Phase A: Complete Wiring and Ship to Production (2 weeks)

Close remaining gaps so both pipelines are fully operational end-to-end in production.

1. **Wire support persistence into worker pipeline**
   - `worker.py` `_run_support_review()` -- add `persist_fn` injection, mirroring the SRE path
   - `support_review.py` -- accept optional `persist_fn` in Dependencies

2. **Feedback and retrieval API endpoints**
   - `POST /api/support/reviews/{id}/feedback` calling existing `update_review_status()`
   - `GET /api/support/reviews/{id}` and `GET /api/sre/investigations/{id}`

3. **Replace HolmesGPT stub with DirectToolsetAdapter**
   - New `DirectToolsetAdapter(BaseHolmesAdapter)` in `domain/sre/holmes_adapter.py`
   - Calls `DatadogClient.query_logs()`, `query_metrics()`, `query_traces()` concurrently
   - Returns structured `HolmesInvestigationResult` from real observability data
   - Uses existing circuit breaker for resilience
   - HolmesGPT SDK can be reconsidered later when dependency conflict resolves

4. **CI/CD pipeline**
   - `.circleci/config.yml` or `.github/workflows/ci.yml` -- lint, typecheck, test, build, deploy

5. **Production deployment**
   - ktl-services-deployment config, ECR repos, IAM role, ACM certificate

### Phase B: Observability and Quality Loop (2-3 weeks)

Answer "how do I make sure it keeps getting better over time."

1. **Distributed tracing**
   - ddtrace spans around graph node `run()` methods and vendor adapter HTTP calls
   - Enable `instrument=True` on PydanticAI agents
   - Tag traces with `run_id` linking webhook receipt to final output

2. **LangFuse integration via LiteLLM**
   - Add `success_callback: ["langfuse"]` to `litellm_config.yaml`
   - No direct Python dependency -- LiteLLM handles the callback
   - Per-call cost tracking, latency, prompt versioning

3. **Evaluation framework**
   - `tests/evals/datasets/sre_golden.json` -- 10-15 golden cases per pipeline
   - `tests/evals/test_sre_evals.py`, `test_support_evals.py` -- run with real LLM, score with automated rubrics
   - Wire into `make test-evals`

4. **Improved confidence scoring**
   - Replace naive `from_total()` with multi-factor scorer
   - SRE: source count, data freshness, tool call count, evidence correlation
   - Support: doc source count, source authority, recency, citation grounding

5. **Feedback metrics**
   - `GET /api/support/stats` -- acceptance rates over time
   - Emit as Datadog custom metric for dashboarding

### Phase C: Scheduled Automations and Extensibility (2-3 weeks)

Answer "where do scheduled automations live" and "custom agentic automations on a schedule."

**Decision:** Scheduled automations live in Sentinel. The existing worker handles execution. Kubernetes CronJobs trigger it. No new runtime needed.

1. **Worker `--run-once` mode**
   - CLI arg parsing so CronJobs run a single job and exit instead of polling

2. **Scheduled automation job type**
   - Add `SCHEDULED_AUTOMATION` to `JobType` enum
   - Add `_run_scheduled_automation()` dispatch branch in worker
   - New `POST /api/automations/trigger` and `GET /api/automations/runs` endpoints

3. **First automation: Repository Health Check**
   - Weekly CronJob that inspects repos via GitHub API, checks stale PRs, posts summary to Slack
   - Helm values entry:
     ```yaml
     cronJobs:
       repo-health:
         enabled: true
         schedule: "0 17 * * 4"  # Thursday 5pm
     ```

4. **MCP tool integration (FastMCP)**
   - MCP client for calling external MCP servers as tools
   - PydanticAI agents can register MCP tools as callable functions
   - Enables automations to use MCP-published tools without vendor-specific code

### Phase D: kagent Pilot and Framework Evaluation (3-4 weeks)

Evaluate OSS frameworks. Run a narrow kagent pilot for Kubernetes troubleshooting.

1. **kagent pilot**
   - Install kagent operator in cluster
   - Define `KubernetesTroubleshooter` Agent CRD with kubectl, Prometheus, Helm tools
   - New `KagentAdapter(BaseHolmesAdapter)` that delegates K8s investigation to kagent
   - Compare results vs. `DirectToolsetAdapter` on same eval cases

2. **Framework evaluation report** (`docs/framework-evaluation.md`)
   - **Claude Agent SDK** -- multi-step agents with tool use; compare with PydanticAI
   - **OpenAI Agent SDK** -- same evaluation; check LiteLLM compatibility
   - **LangGraph / LangChain** -- workflow orchestration; compare with Pydantic Graph
   - **agentgateway** -- document adoption triggers: multiple MCP backends, multiple runtimes, A2A communication
   - Recommendation per framework: adopt / pilot / defer / skip

---

## Verification

### Local Development
```bash
make install                    # UV install
make run-api                    # FastAPI on localhost:8000
make run-worker                 # Background worker
make test                       # Unit tests
make test-integration           # Integration tests
make test-evals                 # Functional/eval tests
make lint                       # Ruff + MyPy + import-linter
make k8s-up                     # Deploy to local K8s
```

### SRE Testing (current)
1. Send a mock PagerDuty webhook to `POST /api/sre/webhooks/pagerduty` -- returns 202 with `job_id`
2. Poll `GET /api/jobs/{job_id}` until status is `completed`
3. Verify investigation graph executes (check logs or job result)
4. Verify Slack message posted to test channel
5. Verify investigation persisted in `investigation_records` table

### Support Testing (current)
1. Send a mock Jira webhook to `POST /api/support/webhooks/jira` -- returns 202 with `job_id`
2. Poll `GET /api/jobs/{job_id}` until status is `completed`
3. Verify review graph executes with doc search
4. Verify response suggestion in job result JSON

### Phase A Verification
- SRE investigation returns real Datadog data (not placeholder) via `DirectToolsetAdapter`
- Support review persists to `ticket_review_records` table
- `POST /api/support/reviews/{id}/feedback` updates review status in DB
- CI pipeline passes on PR

### Phase B Verification
- `make test-evals` runs golden cases and produces scored results
- LangFuse dashboard shows LLM calls with cost/latency
- Datadog traces show full request path from webhook to output
- `GET /api/support/stats` returns acceptance rates

### Phase C Verification
- CronJob triggers on schedule, worker picks up job, automation runs, Slack post appears
- `POST /api/automations/trigger` manually runs same automation
- MCP tools callable from within a PydanticAI agent

### Phase D Verification
- kagent agent responds to K8s alert with kubectl/Prometheus findings
- Framework evaluation report completed with recommendation per framework

### E2E
```bash
make smoke-test                 # Health check
make test-evals                 # Quality evaluation suite
```

---

## Key Reference Files

| Pattern | Source File |
|---------|-----------|
| Pydantic Graph pipeline | `src/sentinel/interfaces/graphs/sre_investigation.py` |
| Search abstraction | `src/sentinel/domain/search/searcher.py` |
| PydanticAI agents | `src/sentinel/interfaces/graphs/agents/alert_classifier.py` |
| LiteLLM gateway helper | `src/sentinel/interfaces/graphs/agents/utils.py` |
| Configuration pattern | `src/sentinel/config.py`, `src/sentinel/settings.py` |
| Import-linter contracts | `pyproject.toml` |
| Confidence scoring | `src/sentinel/domain/confidence/entities.py` |
| Automation runner | `src/sentinel/application/automations/runner.py` |
| Helm chart | `helm/sentinel/values.yaml` |
