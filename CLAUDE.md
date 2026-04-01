# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Sentinel is an AI-powered automation platform with two core capabilities:

1. **AI SRE** - Triages and investigates production alerts from PagerDuty and Datadog, providing root cause analysis and remediation suggestions
2. **AI Support Agent** - Reviews Jira Service Desk tickets, searches documentation (Notion, Confluence, S3), and drafts response suggestions

## Architecture

Clean architecture with enforced layer boundaries (import-linter):

```
interfaces/    → API routers, Pydantic Graph pipelines, webhook handlers, Slack handlers
application/   → Use cases, persistence orchestration, async job enqueue/dequeue
domain/        → Business entities, search abstractions (ABCs), vendor adapter interfaces
data/          → SQLModel database models, Alembic migrations
vendors/       → External SDK wrappers (Slack, PagerDuty, Jira)
plugins/       → Jinja2 prompt templates for PydanticAI agents
```

Lower layers cannot import from higher layers. Enforced by import-linter contracts in `pyproject.toml`.

### Entry Points

- `main.py` — FastAPI server + Slack Socket Mode listener (production)
- `worker.py` — Background job processor polling a PostgreSQL-backed queue (`SELECT ... FOR UPDATE SKIP LOCKED`, no external broker)
- `interfaces/chat/app.py` — Streamlit chat UI for local testing

### Configuration

Two-layer pattern:
- `settings.py` — Pydantic `BaseSettings` reading environment variables, singleton via `get_settings()`
- `_config.py` — Wires vendor adapters and builders from settings, singleton via `get_config()`

Environment variables defined in `.env.default`. Copy to `.env` for local overrides.

### Pydantic Graph Pipelines

Each pipeline is a `pydantic_graph.Graph` with typed dataclass nodes inheriting `BaseNode[State, Dependencies, Reply]`. Dependencies (vendor adapters, searchers) are injected at graph instantiation. Agent definitions live in `interfaces/graphs/agents/` with system prompts as Jinja2 templates in `plugins/prompts/`.

**SRE Investigation:** `ClassifyAlert → InvestigateWithHolmes → AnalyseRootCause → DetermineConfidence → PublishFindings`
Entry: `POST /api/sre/webhooks/pagerduty` or `POST /api/sre/investigate`

**Support Review:** `ClassifyTicket → SearchDocumentation → DraftResponse → DetermineConfidence`
Entry: `POST /api/support/webhooks/jira` or `POST /api/support/review`

### Vendor Adapters

Abstract base classes in `domain/vendor_adapters/`. Each adapter has an `is_configured` property — operations no-op when the adapter is unconfigured (missing API keys). Observability backend (`DatadogClient` | `GrafanaClient`) selected by `OBSERVABILITY_BACKEND` env var.

### LLM Integration

All agents route through a LiteLLM gateway (`AI_GATEWAY_URL`). Model names are per-agent env vars (e.g., `ALERT_CLASSIFIER_LLM`, `ROOT_CAUSE_LLM`). Agents use `Agent("test", ...)` placeholder model, overridden at runtime via `.run(model=...)`.

## Tech Stack

- Python 3.13, FastAPI, PydanticAI, Pydantic Graph
- PostgreSQL + SQLModel/SQLAlchemy (async, asyncpg)
- HolmesGPT (hybrid integration for SRE investigations)
- LiteLLM gateway for LLM model routing
- structlog, Datadog/Grafana, Sentry for observability

## Essential Commands

```bash
make install          # Install dependencies with UV
make run-api          # Start FastAPI on localhost:8000
make run-worker       # Start background job processor
make run-chat         # Start Streamlit chat UI on localhost:8501
make test             # Run unit tests
make test-integration # Run integration tests (requires DB)
make test-evals       # Run functional/E2E tests
make lint             # Ruff + MyPy + import-linter
make lint-fix         # Auto-format with Ruff

make run-db-migrations              # Apply Alembic migrations
make build-migration MESSAGE="..."  # Create new migration
make downgrade-db-migration         # Rollback last migration
make docker-compose-up              # Full stack (db + api + observability)
make k8s-up                         # Deploy to local K8s
```

```bash
uv run pytest tests/unit/path/test_file.py           # Single test file
uv run pytest tests/unit/path/test_file.py::TestClass # Single test class
```

## Testing

- `tests/unit/` — Fast isolated tests, no DB or network. Mirrors `src/` structure.
- `tests/integration/` — Tests with database and vendor mocking
- `tests/functional/` — E2E pipeline tests with mocked LLM agents

Test data factories in `tests/factories/__init__.py` (`make_alert()`, `make_ticket()`, `make_investigation()`, etc.). Functional tests use fixtures that monkeypatch PydanticAI agents (`patch_alert_classifier`, `patch_root_cause_analyser`, etc.) — see `tests/functional/conftest.py`.

## Conventions

See [AGENT.md](AGENT.md) for coding conventions, patterns, and testing rules.
