# CLAUDE.md

## Project Overview

Sentinel is an AI-powered automation platform with two core capabilities:

1. **AI SRE** - Automatically triages and investigates production alerts from PagerDuty and Datadog, providing root cause analysis and remediation suggestions
2. **AI Support Agent** - Automatically reviews Jira Service Desk tickets, searches documentation (Notion, Confluence, S3), and drafts response suggestions

## Architecture

Clean architecture with enforced layer boundaries (import-linter):

```
interfaces/    → API routers, Pydantic Graph pipelines, webhook handlers
application/   → Use cases and orchestration
domain/        → Business entities, search abstractions, vendor adapters
data/          → SQLModel database models, Alembic migrations
vendors/       → External SDK wrappers (Slack, PagerDuty, Jira)
```

Lower layers cannot import from higher layers.

## Tech Stack

- Python 3.13, FastAPI, PydanticAI, Pydantic Graph
- PostgreSQL + SQLModel/SQLAlchemy (async)
- HolmesGPT (hybrid integration for SRE investigations)
- LiteLLM gateway for LLM model routing
- structlog, Datadog, Sentry for observability

## Essential Commands

```bash
make install          # Install dependencies with UV
make run-api          # Start FastAPI on localhost:8000
make test             # Run unit tests
make lint             # Ruff + MyPy + import-linter
make lint-fix         # Auto-format

make run-db-migrations              # Apply Alembic migrations
make build-migration MESSAGE="..."  # Create new migration
make docker-compose-up              # Full stack (db + api)
```

## Key Pipelines

### SRE Investigation (Pydantic Graph)
```
ClassifyAlert → InvestigateWithHolmes → AnalyseRootCause → DetermineConfidence → PublishFindings
```
Entry: `POST /api/sre/webhooks/pagerduty` or `POST /api/sre/investigate`

### Support Review (Pydantic Graph)
```
ClassifyTicket → SearchDocumentation → DraftResponse → DetermineConfidence
```
Entry: `POST /api/support/webhooks/jira` or `POST /api/support/review`

## Testing

```bash
make test                                    # All unit tests
uv run pytest tests/unit/path/test_file.py   # Single test file
```

- `tests/unit/` - Fast isolated tests
- `tests/integration/` - Tests with database
- `tests/functional/` - E2E tests

## Code Quality

- Line length: 99 characters
- Formatting: Ruff
- Type checking: MyPy strict mode
- Import enforcement: import-linter
- Never skip pre-commit hooks with `--no-verify`
