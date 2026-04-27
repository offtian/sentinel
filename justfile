# Sentinel — task runner
# Run `just` to see all available recipes.
# Works from any subdirectory.

set fallback  # search parent directories for this justfile
set dotenv-load  # load .env automatically

# Setup
# -----

# Install dependencies
install:
    uv sync --locked --all-extras
    just verify-install

# Verify the virtualenv can import sentinel
verify-install:
    .venv/bin/python -c "import sentinel; print(sentinel.__file__)"

# Update lockfile
lock:
    uv lock

# Create a plan file from the template
create-plan NAME:
    test ! -e "docs/plans/{{ NAME }}.md"
    cp docs/plans/_template.md "docs/plans/{{ NAME }}.md"

# Development
# -----------

# Start HTTP API + Slack Socket Mode handler
run:
    uv run python -m sentinel.main

# API-only mode with hot-reload (no Slack bot)
run-api:
    uv run uvicorn sentinel.interfaces.api.app:app --host 127.0.0.1 --port 8000 --reload

# Background worker that polls the job queue
run-worker:
    uv run python -m sentinel.worker

# Streamlit chat UI for local e2e testing
run-chat:
    uv run streamlit run src/sentinel/interfaces/chat/app.py --server.port 8501

# Testing
# -------

# Run unit tests
test *ARGS:
    uv run pytest tests/unit/ -x -vv {{ ARGS }}

# Run integration tests (requires DB)
test-integration *ARGS:
    uv run pytest tests/integration/ -x -vv {{ ARGS }}

# Run functional / eval tests
test-evals *ARGS:
    uv run pytest tests/functional/ tests/evals/ -x -vv {{ ARGS }}

# Quick health-check against running API
smoke-test:
    curl -s http://localhost:8000/health | python -m json.tool

# Code Quality
# ------------

# Run all linters (ruff + mypy + import-linter)
lint:
    uv run ruff check src/ tests/
    uv run ruff format --check src/ tests/
    uv run mypy src/
    uv run lint-imports

# Auto-fix lint issues and format
lint-fix:
    uv run ruff check --fix src/ tests/
    uv run ruff format src/ tests/

# Ruff check only
ruff-check:
    uv run ruff check src/ tests/

# Ruff format only
ruff-format:
    uv run ruff format src/ tests/

# MyPy type-check
typecheck:
    uv run mypy src/

# Import-linter check
check-imports:
    uv run lint-imports

# Assert runbook content_sha frontmatter matches loader-computed sha (F6.E)
check-runbook-shas:
    uv run python scripts/compute_runbook_shas.py --check

# Run the F6.M weekly fingerprint-clustering + auto-PR flywheel
# (clusters last week's no-match runbook rows; opens draft PR per qualifying gap).
# Pass --dry-run to inspect cluster output without writing rows or opening PRs.
run-runbook-flywheel *ARGS:
    uv run python scripts/runbook_gap_flywheel.py {{ ARGS }}

# Run the F6.L daily drift-detection sweep (fixture replay + stale runbooks
# + tools registry). Writes runbook_drift_history rows and posts one Slack
# alert per fresh drift via the runbook-owner routing.
check-runbook-drift:
    uv run python scripts/runbook_drift_check.py

# Database
# --------

# Run pending migrations
run-db-migrations:
    uv run python -m alembic -c src/sentinel/data/alembic.ini upgrade head

# Generate a new migration
build-migration MESSAGE:
    uv run python -m alembic -c src/sentinel/data/alembic.ini revision --autogenerate -m "{{ MESSAGE }}"

# Roll back one migration
downgrade-db-migration:
    uv run python -m alembic -c src/sentinel/data/alembic.ini downgrade -1

# Docker
# ------

# Build Docker image
docker-build:
    docker build -t sentinel:latest .

# Prune Docker to free disk space (volumes, images, containers, build cache)
docker-prune:
    docker system prune --all --volumes --force

# Start Docker Compose stack
docker-compose-up:
    docker compose up -d

# Kubernetes (local — Docker Desktop)
# ------------------------------------

# Build image + deploy to local K8s
k8s-up:
    docker build -t sentinel-api:local .
    @echo "Loading image into K8s nodes..."
    @for node in $(kubectl get nodes -o name | cut -d/ -f2); do \
        echo "  → $node"; \
        docker save sentinel-api:local | docker exec -i $node ctr -n k8s.io images import -; \
    done
    kubectl apply -f helm/ollama-local.yaml
    kubectl apply -f helm/postgres-local.yaml
    kubectl apply -f helm/grafana-stack-local.yaml
    kubectl wait --for=condition=ready pod -l app=ollama --timeout=120s
    kubectl wait --for=condition=ready pod -l app=sentinel-postgres --timeout=60s
    kubectl wait --for=condition=ready pod -l app=grafana --timeout=60s
    helm upgrade --install sentinel ./helm/sentinel -f ./helm/sentinel/values-local.yaml

# Full rebuild + redeploy (cleans stale migration job)
k8s-deploy:
    helm uninstall sentinel || true
    kubectl delete job sentinel-migration --ignore-not-found
    just k8s-up

# Tear down local K8s stack
k8s-down:
    helm uninstall sentinel || true
    kubectl delete -f helm/grafana-stack-local.yaml || true
    kubectl delete -f helm/postgres-local.yaml || true
    kubectl delete -f helm/ollama-local.yaml || true

# Tail sentinel logs
k8s-logs:
    kubectl logs -l app.kubernetes.io/name=sentinel --all-containers --prefix -f

# Kagent (local Kind cluster)
# ---------------------------

# Create a Kind cluster with kagent CRDs for local development
kagent-dev-up:
    ./scripts/kind-setup.sh

# Delete the local Kind cluster
kagent-dev-down:
    kind delete cluster --name sentinel-dev

# Housekeeping
# ------------

# Remove caches and build artifacts
clean:
    find src/ tests/ -type d -name "__pycache__" -exec rm -rf {} \; 2>/dev/null || true
    find . -maxdepth 1 -type d -name "*.egg-info" -exec rm -rf {} \; 2>/dev/null || true
    rm -rf .mypy_cache .pytest_cache .ruff_cache htmlcov .coverage 2>/dev/null || true
