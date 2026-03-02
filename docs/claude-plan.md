# Sentinel: AI SRE + AI Support Agent

## Context

The team needs two AI-powered automation capabilities:

1. **AI SRE** - Automatically triage and investigate production alerts from PagerDuty + Datadog, provide root cause analysis and remediation suggestions
2. **AI Support Agent** - Automatically review Jira Service Desk tickets, search mixed documentation (Notion, Confluence, S3), and suggest responses

These will live in a **single new repository** (`sentinel`) following alfredo's clean architecture patterns. The AI SRE will use a **hybrid approach** with HolmesGPT (their investigation engine wrapped in our Pydantic Graph pipeline).

---

## Repository Structure

```
sentinel/
├── src/sentinel/
│   ├── _config.py                    # Centralised config (environs + attrs)
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
│   │   │       └── utils.py          # LiteLLM gateway helper (reuse alfredo pattern)
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
│   │   ├── confidence/               # Shared confidence scoring (port from alfredo)
│   │   │   └── entities.py
│   │   ├── search/                   # Shared search abstractions
│   │   │   └── searcher.py           # BaseDocumentSearcher, BaseMetricsSearcher
│   │   └── vendor_adapters/          # Vendor-specific implementations
│   │       ├── pagerduty.py          # PagerDuty API client
│   │       ├── datadog_client.py     # Datadog API (logs, metrics, traces)
│   │       ├── jira.py               # Jira Service Desk client
│   │       ├── notion.py             # Notion search (reuse alfredo's S3-backed approach)
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

## Project 1: AI SRE - Investigation Pipeline

### Graph Pipeline: `sre_investigation.py`

Follows alfredo's `search.py` pattern (State, Dependencies, BaseNode, GraphRunContext):

```
ReceiveAlert
  → ClassifyAlert (PydanticAI agent: severity, service, category)
  → InvestigateWithHolmes (HolmesGPT SDK: query logs, metrics, traces, K8s)
  → AnalyseRootCause (PydanticAI agent: synthesise findings into root cause hypothesis)
  → DetermineConfidence (shared confidence scoring)
  → PublishFindings (write back to PagerDuty + Slack)
```

### HolmesGPT Hybrid Integration

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

### Webhook Receivers

**PagerDuty webhook** (`interfaces/webhooks/pagerduty.py`):
- Receives V3 webhook events (incident.triggered, incident.acknowledged)
- Normalises into internal `Alert` entity
- Triggers investigation graph via async task or direct invocation

**Datadog webhook** (`interfaces/webhooks/datadog.py`):
- Receives Datadog webhook payloads (alert transitions)
- Normalises into internal `Alert` entity

### Domain Entities

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

### PydanticAI Agents

1. **`alert_classifier`** - Classifies alert severity, affected service, alert category. Uses GPT-4.1-mini via LiteLLM.
2. **`root_cause_analyser`** - Synthesises HolmesGPT findings + alert context into root cause hypothesis and remediation steps. Uses GPT-4.1 or Claude Opus via LiteLLM.

### Output Channels

- **Slack**: Post investigation summary to incident channel (using slack_bolt, same pattern as alfredo)
- **PagerDuty**: Add investigation notes to incident via API
- **Persisted**: Store investigation results in PostgreSQL for historical analysis

---

## Project 2: AI Support Agent - Ticket Review Pipeline

### Graph Pipeline: `support_review.py`

```
ReceiveTicket
  → ClassifyTicket (PydanticAI agent: category, urgency, required expertise)
  → SearchDocumentation (parallel: Notion, Confluence, S3 docs, past tickets)
  → DraftResponse (PydanticAI agent: synthesise docs into response suggestion)
  → DetermineConfidence (shared confidence scoring)
  → PublishSuggestion (write draft to Jira comment or Slack)
```

### Vendor Adapters

**`vendor_adapters/jira.py`** - Jira Service Desk client:
- Fetch new/updated tickets via JQL or webhook
- Read ticket fields (summary, description, comments, attachments, custom fields)
- Post internal comments with response suggestions
- Transition ticket status

**`vendor_adapters/notion.py`** - Reuse alfredo's S3-backed Notion search:
- Alfredo already indexes Notion into S3 buckets
- Reference: `alfredo/application/search/documents.py` and `alfredo/vendors/notion/`
- Can point to same S3 buckets or use a shared search backend (Bedrock KB / Kendra)

**`vendor_adapters/confluence.py`** - Confluence REST API:
- Search via CQL (Confluence Query Language)
- Retrieve page content as structured text
- Handle space-scoped searches

### Search Abstraction

Extend alfredo's `BaseDocumentSearcher` pattern for multi-source search:

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
- `NotionSearcher` - Queries Bedrock KB / Kendra (same backend as alfredo)
- `ConfluenceSearcher` - Queries Confluence REST API
- `S3DocumentSearcher` - Direct S3 object retrieval for indexed docs
- `JiraPastTicketSearcher` - Searches resolved tickets for similar issues

### Domain Entities

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

### PydanticAI Agents

1. **`ticket_reviewer`** - Classifies ticket category, urgency, required domain expertise. Extracts key questions from the ticket. Uses GPT-4.1-mini.
2. **`response_drafter`** - Synthesises search results into a professional response suggestion, citing sources. Uses GPT-4.1 or Claude Opus.

### Trigger Modes

- **Webhook**: Jira webhook on ticket creation/update → triggers review pipeline
- **Polling**: Periodic JQL query for unreviewed tickets (fallback)
- **API**: Manual trigger via FastAPI endpoint (for testing/backfill)

---

## Shared Components

### LiteLLM Gateway

Reuse existing LiteLLM deployment at `http://litellm.litellm.svc.cluster.local/`:

```python
# interfaces/graphs/agents/utils.py (same pattern as alfredo)
def get_model_with_gateway(model_name: str) -> str:
    return f"litellm_proxy/{model_name}"
```

### Configuration (`_config.py`)

Follow alfredo's pattern with environs:

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

### Database Schema (SQLModel)

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

## Deployment

### Infrastructure (OctoCloud)

Add to `octocloud/terraform/workspaces/ktl-services-test/eks_applications.tf`:
- ECR repos: `sentinel`, `sentinel-helm`
- IAM role with S3 access (shared doc buckets), Secrets Manager
- ACM certificate for `sentinel.test.ktl.net`
- KMS key for SOPS

### Kubernetes (ktl-services-deployment)

```
services-eks-test/applications/sentinel/
├── config.yaml          # chartName: sentinel-helm
├── image-tag-value.yaml # ECR image tag
├── values.yaml          # Environment config, deployments
├── values.encrypted.yaml # PagerDuty/Jira/Datadog API keys
└── .sops.yaml
```

Deployments within the Helm chart:
1. **api** - FastAPI server (webhook receivers + API)
2. **worker** - Background task processor (Celery or async task queue for investigations)

### CI/CD (CircleCI)

Follow alfredo's pattern:
1. `mypy` → `test-and-lint` → `publish-image` → `package_chart_and_deploy`
2. Auto-deploy to test, approval-gated for prod
3. Add to `.github/approveman.yml` for CD

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

## Phased Delivery

### Phase 1: Bootstrap + AI SRE Core (Weeks 1-2)
- Repository scaffolding (pyproject.toml, Makefile, Dockerfile, CLAUDE.md)
- Clean architecture skeleton with import-linter
- `_config.py` with SRE-focused settings
- PagerDuty webhook receiver
- HolmesGPT adapter (`domain/sre/holmes_adapter.py`)
- SRE investigation graph (ReceiveAlert → Investigate → Analyse → Publish)
- Slack output (post investigation summary)
- Unit tests for entities and adapter
- Local Docker Compose for development

### Phase 2: AI SRE Polish + Deployment (Weeks 3-4)
- Datadog webhook receiver
- Confidence scoring for investigations
- PostgreSQL persistence (investigation records)
- PagerDuty write-back (add notes to incidents)
- OctoCloud infrastructure setup
- Helm chart + ktl-services-deployment config
- CircleCI pipeline
- Integration tests

### Phase 3: AI Support Agent Core (Weeks 5-6)
- Jira Service Desk client (read tickets, post comments)
- Document search adapters (Notion/S3 via Bedrock KB, Confluence API)
- Support review graph (ReceiveTicket → SearchDocs → DraftResponse → Publish)
- `ticket_reviewer` and `response_drafter` PydanticAI agents
- Jira webhook receiver
- Unit tests

### Phase 4: AI Support Agent Polish + E2E (Weeks 7-8)
- Past ticket search (JQL-based similar ticket lookup)
- Confidence scoring for response suggestions
- PostgreSQL persistence (ticket review records)
- Feedback loop (track accepted/rejected/modified suggestions)
- Functional tests
- Production deployment
- Evals framework (adapting neuralink's evaluator pattern)

---

## Verification

### Local Development
```bash
make install                    # UV install
make run-api                    # FastAPI on localhost:8000
make test                       # Unit tests
make lint                       # Ruff + MyPy + import-linter
```

### SRE Testing
1. Send a mock PagerDuty webhook to `POST /api/sre/webhooks/pagerduty`
2. Verify investigation graph executes (check logs)
3. Verify Slack message posted to test channel
4. Verify investigation persisted in database

### Support Testing
1. Send a mock Jira webhook to `POST /api/support/webhooks/jira`
2. Verify review graph executes with doc search
3. Verify response suggestion posted as Jira comment
4. Verify review record persisted in database

### E2E
```bash
make smoke-test                 # Health check
make test-evals                 # Quality evaluation suite
```

---

## Key Reference Files

| Pattern | Source File |
|---------|-----------|
| Pydantic Graph pipeline | `alfredo/interfaces/graphs/search.py` |
| Search abstraction | `alfredo/domain/search/searcher.py` |
| PydanticAI agents | `alfredo/interfaces/graphs/agents/analyser.py` |
| LiteLLM gateway helper | `alfredo/interfaces/graphs/agents/utils.py` |
| Configuration pattern | `alfredo/_config.py` |
| Import-linter contracts | `alfredo/pyproject.toml` |
| Deployment config example | `ktl-services-deployment/services-eks-test/applications/alfredo/values.yaml` |
| Infra module example | `octocloud/terraform/workspaces/ktl-services-test/eks_applications.tf` |
| Confidence scoring | `alfredo/domain/confidence/entities.py` |
| Helm chart example | `neuralink/helm/neuralink/values.yaml` |
