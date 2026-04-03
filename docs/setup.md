# Setup Guide

## Prerequisites

- **Python 3.13+** — install via [pyenv](https://github.com/pyenv/pyenv) or [uv](https://docs.astral.sh/uv/)
- **UV** — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Docker** and **Docker Compose** — for local PostgreSQL
- **A LiteLLM gateway** (optional for local dev — can point to OpenAI directly)

## 1. Clone and Install

```bash
git clone <repo-url> sentinel
cd sentinel

# Install all dependencies (Python + dev tools)
just install
```

This creates a `.venv/` with all dependencies including test and linting tools.

## 2. Environment Variables

```bash
cp .env.default .env
```

Edit `.env` and fill in the values you need. At minimum for local development:

```bash
# Required
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/sentinel

# LLM — point to your LiteLLM instance or use OpenAI directly
AI_GATEWAY_URL=http://localhost:4000
# If no LiteLLM, set OPENAI_API_KEY and use models like "openai/gpt-4.1-mini"

# Optional — leave blank to disable these integrations locally
PAGERDUTY_API_KEY=
DATADOG_API_KEY=
DATADOG_APP_KEY=
JIRA_BASE_URL=
JIRA_API_TOKEN=
JIRA_USER_EMAIL=
CONFLUENCE_BASE_URL=
CONFLUENCE_USERNAME=
CONFLUENCE_API_TOKEN=
SLACK_BOT_TOKEN=
```

## 3. Database

### Option A: Docker Compose (recommended)

```bash
# Starts PostgreSQL on port 5432
just docker-compose-up
```

### Option B: Local PostgreSQL

```bash
createdb sentinel
```

Then run migrations:

```bash
just run-db-migrations
```

## 4. Run the API

```bash
just run-api
```

The API starts at [http://localhost:8000](http://localhost:8000).

Verify it works:

```bash
curl http://localhost:8000/health
# {"status":"ok","service":"sentinel"}
```

FastAPI docs available at [http://localhost:8000/docs](http://localhost:8000/docs).

## 5. Test an Investigation (Manual Trigger)

```bash
curl -X POST http://localhost:8000/api/sre/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "id": "test-001",
    "title": "High CPU usage on web-01",
    "description": "CPU usage exceeded 90% for the last 10 minutes. Pod restarts detected.",
    "severity": "high",
    "service": "api-gateway"
  }'
```

## 6. Test a Ticket Review (Manual Trigger)

```bash
curl -X POST http://localhost:8000/api/support/review \
  -H "Content-Type: application/json" \
  -d '{
    "id": "12345",
    "key": "SUPPORT-42",
    "summary": "Cannot log in to the dashboard",
    "description": "I have been unable to log in since yesterday. Password reset emails are not arriving.",
    "reporter": "Jane Doe",
    "priority": "High"
  }'
```

## Running Tests

```bash
# Unit tests (fast, no external deps)
just test

# Single test file
uv run pytest tests/unit/domain/sre/test_entities.py -vv

# Single test
uv run pytest tests/unit/domain/sre/test_entities.py::TestAlert::test_create_alert -vv
```

## Code Quality

```bash
# Run all checks (ruff lint + format check + mypy + import-linter)
just lint

# Auto-fix formatting
just lint-fix
```

## Setting Up External Integrations

### PagerDuty

1. Create a PagerDuty V3 webhook subscription pointing to `https://<your-domain>/api/sre/webhooks/pagerduty`
2. Subscribe to event types: `incident.triggered`, `incident.escalated`
3. Set `PAGERDUTY_API_KEY` to a PagerDuty REST API key (for write-back)

### Datadog

1. Create a Datadog webhook integration pointing to `https://<your-domain>/api/sre/webhooks/datadog`
2. Add the webhook as a notification channel on your monitors
3. Set `DATADOG_API_KEY` and `DATADOG_APP_KEY` for log/metric/trace queries

### Jira Service Desk

1. Create a Jira webhook at **Settings > System > Webhooks**
2. URL: `https://<your-domain>/api/support/webhooks/jira`
3. Events: `jira:issue_created`, `jira:issue_updated`
4. Filter by project (e.g., `project = SUPPORT`)
5. Set `JIRA_BASE_URL`, `JIRA_API_TOKEN`, and `JIRA_USER_EMAIL`

### Confluence

1. Set `CONFLUENCE_BASE_URL` to your Confluence instance (e.g., `https://your-org.atlassian.net/wiki`)
2. Set `CONFLUENCE_USERNAME` (email) and `CONFLUENCE_API_TOKEN`
3. The ConfluenceClient adapter searches via CQL and retrieves page content as plain text

### Slack

1. Create a Slack app with `chat:write` scope
2. Install to your workspace
3. Set `SLACK_BOT_TOKEN` to the bot token (`xoxb-...`)
4. Set `SRE_SLACK_CHANNEL` and `SUPPORT_SLACK_CHANNEL` to the channel IDs

### LiteLLM Gateway

If using a shared LiteLLM instance:

```bash
AI_GATEWAY_URL=http://litellm.litellm.svc.cluster.local/
```

For local development without LiteLLM, set your API key directly:

```bash
OPENAI_API_KEY=sk-...
# Models will be routed through PydanticAI's native OpenAI support
```

## Database Migrations

```bash
# Apply all pending migrations
just run-db-migrations

# Create a new migration after changing models.py
just build-migration "add feedback column"

# Roll back the last migration
just downgrade-db-migration
```

## Docker Build

```bash
# Build the image
just docker-build

# Run everything (db + api + Grafana stack)
just docker-compose-up
```

This starts PostgreSQL, the Sentinel API, and the full Grafana observability stack:

| Service | Port | Purpose |
|---------|------|---------|
| Grafana | [localhost:3000](http://localhost:3000) | Dashboards (admin/admin) |
| Prometheus | [localhost:9090](http://localhost:9090) | Metrics |
| Loki | localhost:3100 | Logs |
| Tempo | localhost:3200 | Traces (OTLP on 4317/4318) |

The observability backend auto-selects Grafana when `ENVIRONMENT=localdev`. Datasource UIDs (`prometheus`, `loki`, `tempo`) are pre-provisioned.

## Local Kubernetes

```bash
just k8s-up    # Deploys PostgreSQL + Grafana stack + Sentinel (api + worker)
just k8s-down  # Tears down everything
just k8s-logs  # Tail Sentinel pod logs
```

The Grafana stack is deployed from `helm/grafana-stack-local.yaml` (Prometheus, Loki, Tempo, Grafana with pre-configured datasources). Grafana is exposed via NodePort on port 3000.

## Helm Chart

The Helm chart is at `helm/sentinel/`. To render templates locally:

```bash
helm template sentinel helm/sentinel/ -f helm/sentinel/values.yaml
```

The chart supports two deployments (`api` and `worker`) from the same image. Configuration in `values.yaml` controls:

- Replica counts, resource requests/limits
- HPA settings (min/max replicas, CPU target)
- Ingress (AWS ALB with optional Zscaler SG)
- Pre-install migration job (alembic)
- ServiceAccount with IRSA annotation

## CI/CD

CircleCI pipeline (`.circleci/config.yml`) runs:

1. **mypy** - Strict type checking
2. **test-and-lint** - Unit tests + ruff + import-linter (with PostgreSQL sidecar)
3. **publish-image** - Docker build + push to ECR
4. **package-chart** - Helm chart packaging via ktl-services-deployment-orb

## Troubleshooting

**`uv sync` fails with HolmesGPT conflict** — HolmesGPT has an incompatible dependency with pydantic-ai>=1.0.7. It's excluded from the default install. The adapter works without it via the placeholder implementation.

**Tests pick up the wrong venv** — If `VIRTUAL_ENV` is set from another project, prefix commands with `VIRTUAL_ENV= uv run pytest ...` or deactivate the other environment first.

**`Unknown config option: env`** — Make sure dev dependencies are installed: `uv sync --extra dev`.
