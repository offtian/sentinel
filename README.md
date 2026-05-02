# Sentinel

AI-powered automation platform for production operations and customer support.

## What It Does

**AI SRE** — Automatically triages and investigates production alerts from PagerDuty and Datadog. Queries logs, metrics, traces, and Kubernetes state to identify root causes and suggest remediation steps. Supports multiple investigation backends — direct observability queries, a native K8s PydanticAI agent, or delegation to kagent CRDs — with config-driven A/B comparison mode. Posts findings back to Slack and PagerDuty.

**MCP Integration** — Exposes Sentinel tools (observability, documentation, investigation) to external agents via a FastMCP server, and consumes external MCP tool servers (e.g., kubectl, Datadog, Confluence) as pluggable PydanticAI toolsets.

**AI Support Agent** — Automatically reviews Jira Service Desk tickets, searches documentation across Notion, Confluence, and S3, then drafts response suggestions for support staff to review before sending.

## Architecture

![Sentinel Architecture](docs/images/architecture.svg)

*[Edit diagram in Excalidraw](https://excalidraw.com/#json=wRNI3EjHOiZB6RasBgYXH,Rllv-zTRH2clCN2e4ED2gQ)*

Clean architecture with enforced layer boundaries:

```
interfaces/    FastAPI API, Pydantic Graph pipelines, PydanticAI agents, webhooks
application/   Use cases and orchestration
domain/        Business entities, search abstractions, vendor adapters
data/          Database models and migrations (PostgreSQL + SQLModel)
vendors/       External SDK wrappers (Slack, PagerDuty, Jira)
```

The support pipeline is built as a [Pydantic Graph](https://ai.pydantic.dev/pydantic-graph/) DAG. The SRE pipeline runs on [LangGraph](https://github.com/langchain-ai/langgraph) (flag-gated via `LANGGRAPH_SRE_ENABLED`). All LLM calls route through [LiteLLM](https://github.com/BerriAI/litellm) SDK (in-process) via PydanticAI's `litellm:` model prefix — no external proxy.

### Alert Investigation Pipeline (LangGraph)

```
classify_alert → match_runbook → investigate → analyse_root_cause → determine_confidence
  → [needs_approval?] wait_for_human → [APPROVED?] publish_findings → END
                                      → [REJECTED] → END
  → publish_findings → END
```

The SRE pipeline is implemented in `interfaces/workflows/sre_investigation.py`. Approval state
is persisted via `AsyncPostgresSaver`; the `wait_for_human` node uses LangGraph `interrupt()`
so investigations survive worker restarts. Enable via `LANGGRAPH_SRE_ENABLED=true`; the
legacy Pydantic Graph pipeline remains available at `interfaces/graphs/_archive/investigation.py`
for rollback until the staging soak window closes.

### Support Review Pipeline

```
ClassifyTicket → SearchDocumentation → DraftResponse → DetermineConfidence
```

Error handling and approval gating follow the same pattern as the SRE pipeline.

## Quick Start

```bash
# Install dependencies
just install

# Copy and fill in environment variables
cp .env.default .env

# Start the full stack (Sentinel API + Postgres + local Langfuse v3)
just docker-compose-up

# Or run the API locally (requires a running Postgres)
just run-api
```

The API starts at `http://localhost:8000`. Health check at `GET /health`.

### Local stack endpoints

| Service        | URL                          | Notes                                                                 |
| -------------- | ---------------------------- | --------------------------------------------------------------------- |
| Sentinel API   | `http://localhost:8000`      | Webhooks, manual triggers, health.                                    |
| Langfuse UI    | `http://localhost:3001`      | Trace explorer. Sign in `dev@sentinel.localdev` / `devpass1`.         |
| MinIO console  | `http://localhost:9001`      | Object store backing Langfuse (`minio` / `miniosecret`).              |
| Postgres (app) | `localhost:5432`             | Sentinel application database.                                        |
| Postgres (lf)  | `localhost:5433`             | Langfuse's own database, isolated from the app DB.                    |

The Langfuse stack ships with deterministic dev keys (`pk-lf-localdev` / `sk-lf-localdev`) seeded via `LANGFUSE_INIT_*` env vars — no UI clicks required after `docker compose up`. These are **dev-only**, not secrets. See [`docs/architecture.md`](docs/architecture.md) §Observability for the full trace-pipeline diagram and the RFC §13.2 mandatory-attribute contract.

## API Endpoints

### SRE


| Method | Path                                                  | Description                   |
| ------ | ----------------------------------------------------- | ----------------------------- |
| POST   | `/api/sre/webhooks/pagerduty`                         | PagerDuty V3 webhook receiver |
| POST   | `/api/sre/webhooks/datadog`                           | Datadog webhook receiver      |
| POST   | `/api/sre/investigate`                                | Manual investigation trigger  |
| POST   | `/api/sre/investigations/{id}/approve`                | Approve a pending investigation |
| POST   | `/api/sre/investigations/{id}/reject`                 | Reject a pending investigation  |
| GET    | `/api/sre/investigations/{id}/approval-status`        | Check approval status           |


### Support


| Method | Path                         | Description                        |
| ------ | ---------------------------- | ---------------------------------- |
| POST   | `/api/support/webhooks/jira` | Jira Service Desk webhook receiver |
| POST   | `/api/support/review`        | Manual ticket review trigger       |


## Development

```bash
# Run unit tests
just test

# Run linting (ruff + mypy + import-linter)
just lint

# Auto-format
just fmt

# Database migrations
just run-db-migrations
just build-migration "add new table"
```

## Configuration

All configuration via environment variables. See [.env.default](.env.default) for the full list.

Key settings:


| Variable               | Description                           | Default                                     |
| ---------------------- | ------------------------------------- | ------------------------------------------- |
| `OLLAMA_BASE_URL`      | Ollama API URL for local development  | `http://localhost:11434`                     |
| `ALERT_CLASSIFIER_LLM` | Model for alert classification        | `openai/gpt-4.1-mini`                       |
| `ROOT_CAUSE_LLM`       | Model for root cause analysis         | `openai/gpt-4.1`                            |
| `TICKET_REVIEWER_LLM`  | Model for ticket classification       | `openai/gpt-4.1-mini`                       |
| `RESPONSE_DRAFTER_LLM` | Model for response drafting           | `openai/gpt-4.1`                            |
| `LANGGRAPH_SRE_ENABLED` | Route SRE investigations through LangGraph workflow (flag W2) | `false` |
| `SRE_AUTO_INVESTIGATE` | Auto-investigate incoming alerts      | `true`                                      |
| `SUPPORT_AUTO_DRAFT`   | Auto-draft responses for new tickets  | `true`                                      |
| `HOLMESGPT_ENABLED`    | Enable HolmesGPT investigation engine | `true`                                      |
| `REQUIRE_APPROVAL_BELOW_CONFIDENCE` | Confidence threshold requiring human approval | `0.7`                |
| `APPROVAL_TIMEOUT_SECONDS` | Timeout for pending approvals (0 = no timeout) | `0`                        |
| `K8S_INVESTIGATION_BACKEND` | K8s investigation backend: `native`, `kagent`, `both`, or empty (disabled) | `` (disabled)     |
| `K8S_INVESTIGATOR_LLM` | Model for K8s investigation agent | `ollama/qwen3-coder:30b`           |
| `K8S_CLUSTER_NAME` | Target K8s cluster name for investigation context | ``                              |
| `K8S_DEFAULT_NAMESPACE` | Default namespace for K8s investigations | ``                                       |
| `KAGENT_INVESTIGATION_TIMEOUT_SECONDS` | Timeout for kagent CRD investigations | `120`                       |
| `MCP_SERVERS` | JSON list of external MCP servers to inject as toolsets | ``                              |
| `K8S_MCP_SERVER_URL` | Optional kubectl MCP server URL for K8s agent | ``                                  |
| `MCP_SERVER_PORT` | Port for Sentinel's MCP server | `8811`                                              |
| `MCP_SERVER_API_KEY` | API key for MCP server authentication (empty = disabled) | ``                    |
| `LANGFUSE_HOST` | Langfuse OTel ingestion endpoint (empty = disabled) | `http://localhost:3001` (docker-compose default) |
| `LANGFUSE_PUBLIC_KEY` | Langfuse project public key (Basic-auth username) | `pk-lf-localdev` (dev only) |
| `LANGFUSE_SECRET_KEY` | Langfuse project secret key (Basic-auth password) | `sk-lf-localdev` (dev only) |


## Tech Stack

- **Python 3.13**, FastAPI, PydanticAI, Pydantic Graph
- **PostgreSQL** + SQLModel/SQLAlchemy (async)
- **HolmesGPT** (hybrid integration — adapter pattern)
- **LiteLLM** SDK for in-process model routing (no proxy)
- **FastMCP** for MCP server/client integration
- **kubernetes-asyncio** for native K8s investigation
- **structlog**, Datadog, Sentry for observability

## Project Structure

```
src/sentinel/
├── config.py              # Centralised configuration (Configuration class)
├── settings.py            # Environment variables (pydantic-settings)
├── interfaces/            # FastAPI API, Pydantic Graph pipelines, PydanticAI agents, MCP server
├── application/           # Use cases, supervisor orchestrator, automations
├── domain/                # Business entities, skills, prompts, tools, vendor adapters
├── plugins/               # PydanticAI toolset wrappers, MCP client builder
├── evals/                 # Evaluation framework (pydantic_evals)
├── data/                  # SQLModel tables, Alembic migrations
├── vendors/               # External SDK wrappers (Slack, PagerDuty, Jira)
└── utils/                 # Logging, metrics
```

See [Architecture](docs/architecture.md) for the full layer diagram with file-level detail.

## Documentation

Documents are organised by audience and purpose. Review in this order for onboarding:

| Priority | Document | Audience | Purpose |
|----------|----------|----------|---------|
| 1 | **This README** | Everyone | Project overview, quick start, API surface |
| 2 | [Architecture](docs/architecture.md) | Engineers | Layer diagram, pipeline flows, vendor adapters, decisions, capability plane |
| 3 | [PRD](docs/prd.md) | Product + Engineering | Requirements, acceptance criteria checkboxes (canonical status tracker) |
| 4 | [Plan Index](docs/plans/INDEX.md) | Engineering | Status of all implementation plans at a glance |
| 5 | [AGENTS.md](AGENTS.md) | AI agents | Coding conventions, testing rules, naming patterns |
| 6 | [CLAUDE.md](CLAUDE.md) | AI agents | Essential commands, gotchas, documentation workflow |

**Ownership rules:**
- **Status tracking** lives only in `docs/prd.md` (acceptance criteria) and `docs/plans/INDEX.md` (plan progress)
- **Architecture, decisions, and roadmap** live in `docs/architecture.md` — other docs link here, not duplicate
- **`docs/reviews/*`** are frozen historical snapshots — never update

## License

Private project. All rights reserved.