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
    ↓
domain/        → Business entities, search abstractions, vendor adapters, confidence scoring
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
  ↓
InvestigateWithHolmes
  │  HolmesGPT adapter queries observability systems (Datadog, K8s, Prometheus)
  ↓
AnalyseRootCause
  │  PydanticAI agent synthesises findings into root cause + remediation
  ↓
DetermineConfidence
  │  Calculate confidence score from analysis confidence
  ↓
PublishFindings
  │  Format and return investigation results
  ↓
End(InvestigationReply)
```

### Entry Points
- `POST /api/sre/webhooks/pagerduty` - PagerDuty V3 webhook receiver
- `POST /api/sre/webhooks/datadog` - Datadog webhook receiver
- `POST /api/sre/investigate` - Manual investigation trigger

### HolmesGPT Integration (Hybrid Approach)

HolmesGPT has a dependency conflict with pydantic-ai>=1.0.7 (pinned mcp versions). Our approach:

1. **Adapter pattern** (`domain/sre/holmes_adapter.py`) defines `BaseHolmesAdapter` ABC
2. **Production adapter** wraps HolmesGPT's toolsets for data gathering
3. **Mock adapter** provides test fixtures without SDK dependency
4. **Our pipeline** handles orchestration, analysis, confidence scoring, and output formatting

When the upstream dependency conflict is resolved, the production adapter will import HolmesGPT's `ToolExecutor`, `DatadogToolset`, `KubernetesToolset`, and `PrometheusToolset` directly.

## AI Support Pipeline

The support review pipeline follows the same Pydantic Graph pattern:

```
ClassifyTicket
  │  PydanticAI agent classifies category, urgency, extracts key questions + search queries
  ↓
SearchDocumentation
  │  Parallel search across Notion, Confluence, S3, and past tickets
  ↓
DraftResponse
  │  PydanticAI agent synthesises documentation into response suggestion
  ↓
DetermineConfidence
  │  Calculate confidence score
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

## PydanticAI Agents

| Agent | Purpose | Default Model |
|-------|---------|---------------|
| `alert_classifier` | Classify alert severity, service, category | GPT-4.1-mini |
| `root_cause_analyser` | Synthesise findings into root cause + remediation | GPT-4.1 |
| `ticket_reviewer` | Classify ticket, extract questions, generate search queries | GPT-4.1-mini |
| `response_drafter` | Draft customer response from documentation | GPT-4.1 |

All agents route through a LiteLLM gateway for model management, cost tracking, and fallback.

## Configuration

All settings in `src/sentinel/_config.py` using `environs`. Key groups:

- **LLM models** - Per-agent model selection via LiteLLM gateway
- **SRE config** - PagerDuty/Datadog API keys, HolmesGPT toggle
- **Support config** - Jira/Confluence URLs and tokens
- **Feature flags** - `SRE_AUTO_INVESTIGATE`, `SUPPORT_AUTO_DRAFT`
- **Slack** - Bot token and channel IDs for posting results
- **Observability** - Datadog service name and environment

## Database

PostgreSQL with SQLModel (async via asyncpg). Two main tables:

- `investigation_records` - Persisted SRE investigations with findings, root cause, confidence
- `ticket_review_records` - Persisted support reviews with suggested responses, sources, feedback status

Migrations managed by Alembic.

## Deployment

Designed for Kubernetes deployment via Helm + ArgoCD GitOps:

- **api** deployment - FastAPI server (webhook receivers + API endpoints)
- **worker** deployment - Background task processor (for long-running investigations)
- Secrets encrypted via SOPS + KMS
- IAM roles via IRSA for AWS service access
- Datadog APM for observability
