# Setup Guide

## Prerequisites

- **Python 3.13+** — install via [pyenv](https://github.com/pyenv/pyenv) or [uv](https://docs.astral.sh/uv/)
- **UV** — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Docker** and **Docker Compose** — for local PostgreSQL
- **An OpenAI API key** or **Ollama** for local LLM inference

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

# LLM — set your provider API key (LiteLLM SDK routes to providers in-process)
OPENAI_API_KEY=sk-...
# For local dev with Ollama, set OLLAMA_BASE_URL (defaults to http://localhost:11434)

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
uv run pytest tests/unit/domain/alerts/test_entities.py -vv

# Single test
uv run pytest tests/unit/domain/alerts/test_entities.py::TestAlert::test_create_alert -vv
```

## Code Quality

```bash
# Run all checks (ruff lint + format check + mypy + import-linter)
just lint

# Auto-fix formatting
just lint-fix
```

## K8s Investigation Agent

Sentinel supports K8s-native investigation via a PydanticAI agent with kubernetes-asyncio tools, or delegation to kagent CRDs.

### Environment Variables

```bash
# Backend: "native" (PydanticAI agent), "kagent" (CRD delegation), "both" (A/B comparison), or "" (disabled)
K8S_INVESTIGATION_BACKEND=native

# LLM model for the K8s investigator agent
K8S_INVESTIGATOR_LLM=openai/gpt-4.1

# Cluster context (used in investigation reports and audit trail)
K8S_CLUSTER_NAME=my-cluster
K8S_DEFAULT_NAMESPACE=default

# Kagent (only when backend includes kagent)
KAGENT_INVESTIGATION_TIMEOUT_SECONDS=120
KAGENT_NAMESPACE=kagent-system

# Optional kubectl MCP server for extended K8s operations
K8S_MCP_SERVER_URL=http://localhost:9090/sse
```

### In-Cluster vs Kubeconfig

The K8s client auto-detects its environment:
- **In-cluster**: Uses the service account token mounted at `/var/run/secrets/kubernetes.io/serviceaccount/`
- **Local dev**: Falls back to `~/.kube/config` (current context)

### Local Kind Cluster

For local development with kagent:

```bash
# Start a Kind cluster with kagent CRDs installed
just kagent-dev-up

# Tear down
just kagent-dev-down
```

### RBAC

The Helm chart creates a read-only ClusterRole when `k8sAgent.enabled=true`:
- `get`, `list`, `watch` on pods, deployments, replicasets, events, services, nodes
- `get` on `pods/log`
- `create`, `get`, `list`, `watch` on kagent `investigations` CRDs (when `kagent.enabled=true`)

No write access to core K8s resources — hedge fund compliance requires minimal blast radius.

## MCP Server

Sentinel exposes an MCP (Model Context Protocol) server for external agents to discover and call Sentinel tools.

```bash
# Port for the MCP server (also configurable via Helm)
MCP_SERVER_PORT=8811

# API key authentication (empty = auth disabled)
MCP_SERVER_API_KEY=your-secret-key
```

The MCP server is included in `docker-compose.yaml` and available as a separate Helm deployment (`mcpServer.enabled=true`). It exposes observability, documentation, and investigation tools via streamable HTTP transport.

### MCP Client (Consuming External Servers)

Configure external MCP servers for all pipeline agents via `MCP_SERVERS`:

```bash
# Single HTTP server
MCP_SERVERS=[{"name": "datadog", "url": "http://localhost:9090/sse"}]

# Stdio server
MCP_SERVERS=[{"name": "confluence", "command": "npx", "args": ["-y", "@confluence/mcp"]}]

# Multiple servers
MCP_SERVERS=[{"name": "datadog", "url": "http://localhost:9090/sse"}, {"name": "github", "url": "http://localhost:9091/sse"}]
```

See `.env.default` for more examples.

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

### LLM Providers

LiteLLM SDK handles provider routing in-process via PydanticAI's `litellm:` model prefix. No external proxy service is needed -- `litellm` is a Python dependency that routes model calls directly to provider APIs, supporting 100+ providers with `drop_params` for cross-provider compatibility. Settings are configured programmatically in `bootstrap.py`.

**For OpenAI:**

```bash
OPENAI_API_KEY=sk-...
# Models use LiteLLM format: "openai/gpt-4.1-mini"
```

**For local development with Ollama:**

```bash
OLLAMA_BASE_URL=http://localhost:11434  # default, can be omitted
# Use models like "ollama/llama3" for local inference
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
