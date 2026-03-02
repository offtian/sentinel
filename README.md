# Sentinel

AI-powered automation platform for production operations and customer support.

## What It Does

**AI SRE** — Automatically triages and investigates production alerts from PagerDuty and Datadog. Queries logs, metrics, traces, and Kubernetes state to identify root causes and suggest remediation steps. Posts findings back to Slack and PagerDuty.

**AI Support Agent** — Automatically reviews Jira Service Desk tickets, searches documentation across Notion, Confluence, and S3, then drafts response suggestions for support staff to review before sending.

## Architecture

Clean architecture with enforced layer boundaries:

```
interfaces/    FastAPI API, Pydantic Graph pipelines, PydanticAI agents, webhooks
application/   Use cases and orchestration
domain/        Business entities, search abstractions, vendor adapters
data/          Database models and migrations (PostgreSQL + SQLModel)
vendors/       External SDK wrappers (Slack, PagerDuty, Jira)
```

Both pipelines are built as [Pydantic Graph](https://ai.pydantic.dev/pydantic-graph/) DAGs with [PydanticAI](https://ai.pydantic.dev/) agents at key decision nodes. LLM calls route through a [LiteLLM](https://github.com/BerriAI/litellm) gateway.

### SRE Investigation Pipeline

```
ClassifyAlert → InvestigateWithHolmes → AnalyseRootCause → DetermineConfidence → PublishFindings
```

### Support Review Pipeline

```
ClassifyTicket → SearchDocumentation → DraftResponse → DetermineConfidence
```

## Quick Start

```bash
# Install dependencies
make install

# Copy and fill in environment variables
cp .env.default .env

# Start PostgreSQL + API with Docker Compose
make docker-compose-up

# Or run the API locally (requires a running Postgres)
make run-api
```

The API starts at `http://localhost:8000`. Health check at `GET /health`.

## API Endpoints

### SRE

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/sre/webhooks/pagerduty` | PagerDuty V3 webhook receiver |
| POST | `/api/sre/webhooks/datadog` | Datadog webhook receiver |
| POST | `/api/sre/investigate` | Manual investigation trigger |

### Support

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/support/webhooks/jira` | Jira Service Desk webhook receiver |
| POST | `/api/support/review` | Manual ticket review trigger |

## Development

```bash
# Run unit tests
make test

# Run linting (ruff + mypy + import-linter)
make lint

# Auto-format
make lint-fix

# Database migrations
make run-db-migrations
make build-migration MESSAGE="add new table"
```

## Configuration

All configuration via environment variables. See [.env.default](.env.default) for the full list.

Key settings:

| Variable | Description | Default |
|----------|-------------|---------|
| `AI_GATEWAY_URL` | LiteLLM gateway URL | `http://litellm.litellm.svc.cluster.local/` |
| `ALERT_CLASSIFIER_LLM` | Model for alert classification | `openai/gpt-4.1-mini` |
| `ROOT_CAUSE_LLM` | Model for root cause analysis | `openai/gpt-4.1` |
| `TICKET_REVIEWER_LLM` | Model for ticket classification | `openai/gpt-4.1-mini` |
| `RESPONSE_DRAFTER_LLM` | Model for response drafting | `openai/gpt-4.1` |
| `SRE_AUTO_INVESTIGATE` | Auto-investigate incoming alerts | `true` |
| `SUPPORT_AUTO_DRAFT` | Auto-draft responses for new tickets | `true` |
| `HOLMESGPT_ENABLED` | Enable HolmesGPT investigation engine | `true` |

## Tech Stack

- **Python 3.13**, FastAPI, PydanticAI, Pydantic Graph
- **PostgreSQL** + SQLModel/SQLAlchemy (async)
- **HolmesGPT** (hybrid integration — adapter pattern)
- **LiteLLM** gateway for LLM routing
- **structlog**, Datadog, Sentry for observability

## Project Structure

```
src/sentinel/
├── _config.py                        # Centralised configuration
├── interfaces/
│   ├── api/                          # FastAPI app and routers
│   ├── graphs/                       # Pydantic Graph pipelines
│   │   ├── sre_investigation.py      # SRE pipeline (5 nodes)
│   │   ├── support_review.py         # Support pipeline (4 nodes)
│   │   └── agents/                   # PydanticAI agent definitions
│   └── webhooks/                     # Webhook payload parsers
├── domain/
│   ├── sre/                          # Alert, Investigation, HolmesAdapter
│   ├── support/                      # Ticket, ResponseSuggestion
│   ├── confidence/                   # Confidence scoring
│   └── search/                       # Search abstractions (ABC)
├── data/                             # SQLModel tables, Alembic migrations
└── vendors/                          # Slack SDK wrapper
```

## Documentation

- [Architecture](docs/architecture.md) — design principles, layer diagram, pipeline flows
- [Roadmap](docs/roadmap.md) — phased delivery plan with implementation details

## License

Private project. All rights reserved.
