# Sentinel: AI SRE + AI Support Agent

## Context

The team needs two AI-powered automation capabilities:

1. **AI SRE** - Automatically triage and investigate production alerts from PagerDuty + Datadog, provide root cause analysis and remediation suggestions
2. **AI Support Agent** - Automatically review Jira Service Desk tickets, search mixed documentation (Notion, Confluence, S3), and suggest responses

These live in the `sentinel` repository following clean architecture patterns. The AI SRE uses a **hybrid approach** with a DirectToolsetAdapter that queries observability backends directly (replacing the HolmesGPT SDK due to a pydantic-ai dependency conflict).

> **Status tracking lives in `docs/prd.md`** (acceptance criteria checkboxes).
> This file contains operational context for AI sessions — architecture decisions, gotchas, and reference material.

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
│   │   ├── support/
│   │   │   ├── review_ticket.py      # Ticket analysis use case
│   │   │   └── search_docs.py        # Multi-source document retrieval
│   │   ├── supervisor/               # Quality-gate orchestration
│   │   │   └── orchestrator.py       # supervise_sre_investigation(), supervise_support_review()
│   │   └── automations/
│   │       └── runner.py             # Automation registry and execution
│   │
│   ├── domain/                       # Layer 3: Business logic
│   │   ├── sre/
│   │   │   ├── entities.py           # Alert, Investigation, RootCause, Remediation
│   │   │   ├── holmes_adapter.py     # BaseHolmesAdapter ABC + DirectToolsetAdapter
│   │   │   └── operations.py         # Investigation lifecycle management
│   │   ├── support/
│   │   │   ├── entities.py           # Ticket, ResponseSuggestion, DocSource
│   │   │   └── operations.py         # Ticket review lifecycle
│   │   ├── confidence/               # Shared confidence scoring
│   │   │   └── entities.py           # ConfidenceScore.from_factors(), from_total()
│   │   ├── search/                   # Shared search abstractions
│   │   │   └── searcher.py           # BaseDocumentSearcher, BaseMetricsSearcher
│   │   ├── pipeline/                 # Pipeline error and state types
│   │   │   ├── errors.py             # NodeError, PipelineNodeFailed
│   │   │   └── types.py              # PipelineState, GraphRunResult, shared graph types
│   │   ├── approval/                 # Human approval gate
│   │   │   └── entities.py           # ApprovalRequest, ApprovalDecision
│   │   ├── supervisor/               # Quality gate evaluation
│   │   │   └── quality_gate.py       # evaluate_sre_quality(), evaluate_support_quality()
│   │   ├── tools/                    # Domain tool definitions
│   │   │   ├── documentation.py      # Documentation search tool functions
│   │   │   └── observability.py      # Observability query tool functions
│   │   └── vendor_adapters/          # Vendor-specific implementations
│   │       ├── pagerduty.py          # PagerDuty API client
│   │       ├── datadog_client.py     # Datadog API (logs, metrics, traces)
│   │       ├── jira.py               # Jira Service Desk client
│   │       ├── notion.py             # Notion search (S3-backed approach)
│   │       ├── confluence.py         # Confluence REST API client
│   │       └── s3.py                 # S3 document retrieval
│   │
│   ├── evals/                        # Evaluation framework (pydantic_evals)
│   │   ├── cases/                    # Test case definitions and golden datasets
│   │   ├── evaluators/               # Keyword coverage, structural evaluators
│   │   ├── runner.py                 # Eval runner entry point
│   │   ├── reporting.py              # EvaluationReport with assertion/score averages
│   │   └── rendering.py              # Rich console table output
│   │
│   ├── plugins/                      # Plugin adapters (toolsets, prompts)
│   │   ├── toolsets/                 # PydanticAI toolset wrappers
│   │   │   ├── documentation.py      # Documentation toolset for agents
│   │   │   └── observability.py      # Observability toolset for agents
│   │   └── prompts/                  # Jinja2 agent system prompt templates
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
├── .github/workflows/ci.yml
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
include_external_packages = true

[[tool.importlinter.contracts]]
name = "Top level layers"
type = "layers"
exhaustive = true
containers = ["sentinel"]
layers = [
    "main",
    "worker",
    "config",
    "interfaces",
    "application",
    "evals",
    "plugins",
    "domain",
    "data",
    "vendors",
    "bootstrap",
    "utils",
    "settings",
    "version",
]
```

---

## Architecture Decisions

### Execution Model

- **PostgreSQL job queue** with `SELECT ... FOR UPDATE SKIP LOCKED` instead of Redis/RabbitMQ/Celery. Supports horizontal scaling by adding worker replicas. No additional infrastructure dependency.
- **Async/await throughout** -- no blocking I/O, no Celery task serialisation overhead.
- **Worker replicas** scale independently from API replicas. Helm HPA configured (disabled by default).

### Scheduled Automations

Scheduled automations live in Sentinel, triggered by Kubernetes CronJobs. The worker gets a `--run-once` mode for CronJob execution. This avoids introducing a new runtime (kagent, Temporal, Argo Workflows) until the need is proven. For Toby's "every Thursday at 5pm" use case, a CronJob creates a K8s Job that hits the Sentinel API, which enqueues a `SCHEDULED_AUTOMATION` job for the worker.

### HolmesGPT Resolution

HolmesGPT SDK has a dependency conflict with pydantic-ai>=1.0.7. Rather than waiting for an upstream fix, `DirectToolsetAdapter` calls the existing DatadogClient and PagerDutyClient vendor adapters directly. This gives real observability data without the SDK. HolmesGPT can be reconsidered later.

### OSS Framework Positioning

| Framework | Position |
|-----------|----------|
| PydanticAI + Pydantic Graph | Keep -- current orchestration layer, well-integrated |
| LiteLLM | Keep -- model routing gateway, already deployed |
| LangFuse | Adopt via LiteLLM callbacks -- no direct dependency |
| kagent | Narrow pilot for K8s troubleshooting -- not a replacement |
| agentgateway | Defer until multiple MCP backends or agent runtimes need centralised routing |
| Claude Agent SDK / OpenAI Agent SDK | Evaluate -- may complement PydanticAI for specific workflows |
| LangGraph / LangChain | Evaluate -- compare with Pydantic Graph for complex branching |
| FastMCP | Adopt for tool integration |

### Quality Over Time

Quality improvement relies on three feedback loops:
1. **LangFuse** -- per-call cost, latency, and prompt versioning
2. **Eval framework** -- golden test cases with automated scoring, run on prompt changes
3. **Feedback API** -- track accept/reject/modify rates on support suggestions

---

## Pipeline Details

### SRE Investigation Pipeline

```
ClassifyAlert → InvestigateWithHolmes → AnalyseRootCause → DetermineConfidence → PublishFindings
```

- Each node has structured error handling via `NodeError` / `PipelineNodeFailed`
- Critical nodes fail pipeline cleanly; degradable nodes continue with partial results
- `DetermineConfidence` enforces approval gate below configurable confidence threshold
- `PublishFindings` uses `gather(return_exceptions=True)` so one channel failure doesn't block others
- Supervisor orchestrator wraps the pipeline with quality gating, retry, and escalation

### Support Review Pipeline

```
ClassifyTicket → SearchDocumentation → DraftResponse → DetermineConfidence
```

- `SearchDocumentation` uses `asyncio.TaskGroup()` for parallel doc + ticket search
- Same error handling and supervisor wrapping pattern as SRE pipeline

### Domain Entities

```python
# domain/sre/entities.py
class Alert(BaseModel):       # id, source, title, description, severity, service, triggered_at, raw_payload
class Investigation(BaseModel): # id, alert, status, findings, root_cause, remediation, confidence
class Finding(BaseModel):      # source, summary, raw_data, relevance

# domain/support/entities.py
class Ticket(BaseModel):             # id, key, summary, description, reporter, priority, labels, comments
class ResponseSuggestion(BaseModel): # ticket_id, suggested_response, sources, confidence, category
class DocSource(BaseModel):          # title, url, source_type, excerpt, relevance

# domain/confidence/entities.py
class ConfidenceScore:  # from_factors(source_count, relevance, recency) or from_total(total)
```

---

## Deployment

### Infrastructure (separate repo)

- Terraform: ECR repos, IAM role with S3 access, Secrets Manager, ACM cert, KMS key for SOPS
- K8s config: `services-eks-test/applications/sentinel/` with config.yaml, values.yaml, values.encrypted.yaml

### Helm Chart Deployments

1. **api** - FastAPI server (webhook receivers + API)
2. **worker** - Background task processor (async PostgreSQL job queue)
3. **CronJob template** - Parameterised for scheduled automations

---

## Dependencies

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

### SRE Testing
1. Send a mock PagerDuty webhook to `POST /api/sre/webhooks/pagerduty` -- returns 202 with `job_id`
2. Poll `GET /api/jobs/{job_id}` until status is `completed`
3. Verify investigation graph executes (check logs or job result)
4. Verify Slack message posted to test channel
5. Verify investigation persisted in `investigation_records` table

### Support Testing
1. Send a mock Jira webhook to `POST /api/support/webhooks/jira` -- returns 202 with `job_id`
2. Poll `GET /api/jobs/{job_id}` until status is `completed`
3. Verify review graph executes with doc search
4. Verify response suggestion in job result JSON

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
| Quality gate | `src/sentinel/domain/supervisor/quality_gate.py` |
| Supervisor orchestrator | `src/sentinel/application/supervisor/orchestrator.py` |
| Approval entities | `src/sentinel/domain/approval/entities.py` |
| Helm chart | `helm/sentinel/values.yaml` |
