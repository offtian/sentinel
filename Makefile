.PHONY: install verify-install lock run run-api run-worker run-chat test lint lint-fix \
	ruff-check ruff-format typecheck check-imports \
	run-db-migrations build-migration downgrade-db-migration \
	docker-build docker-compose-up smoke-test test-evals clean \
	k8s-up k8s-deploy k8s-down k8s-logs

# Setup
install:
	uv sync --locked --all-extras
	@$(MAKE) verify-install

verify-install:
	@".venv/bin/python" -c "import sentinel; print(sentinel.__file__)"

lock:
	uv lock

# Development
run:
	# Starts HTTP API + Slack Socket Mode handler together (reads SLACK_APP_TOKEN from .env)
	uv run python -m sentinel.main

run-api:
	# API-only mode with hot-reload (no Slack bot — use `make run` for the full bot)
	uv run uvicorn sentinel.interfaces.api.app:app --host 127.0.0.1 --port 8000 --reload

run-worker:
	# Background worker that polls the job queue and executes pipelines
	uv run python -m sentinel.worker

run-chat:
	# Streamlit chat UI for local e2e testing (no Slack/K8s required)
	uv run streamlit run src/sentinel/interfaces/chat/app.py --server.port 8501

# Testing
test:
	uv run pytest tests/unit/ -x -vv

test-integration:
	uv run pytest tests/integration/ -x -vv

test-evals:
	uv run pytest tests/functional/ -x -vv

smoke-test:
	curl -s http://localhost:8000/health | python -m json.tool

# Code Quality
lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/
	uv run mypy src/
	uv run lint-imports

lint-fix:
	uv run ruff check --fix src/ tests/
	uv run ruff format src/ tests/

ruff-check:
	uv run ruff check src/ tests/

ruff-format:
	uv run ruff format src/ tests/

typecheck:
	uv run mypy src/

check-imports:
	uv run lint-imports

# Database
run-db-migrations:
	uv run python -m alembic -c src/sentinel/data/alembic.ini upgrade head

build-migration:
	uv run python -m alembic -c src/sentinel/data/alembic.ini revision --autogenerate -m "$(MESSAGE)"

downgrade-db-migration:
	uv run python -m alembic -c src/sentinel/data/alembic.ini downgrade -1

# Docker
docker-build:
	docker build -t sentinel:latest .

docker-compose-up:
	docker compose up -d

# Kubernetes (local — Docker Desktop)
k8s-up:
	docker build -t sentinel-api:local .
	@echo "Loading image into K8s nodes..."
	@for node in $$(kubectl get nodes -o name | cut -d/ -f2); do \
		echo "  → $$node"; \
		docker save sentinel-api:local | docker exec -i $$node ctr -n k8s.io images import -; \
	done
	kubectl apply -f helm/ollama-local.yaml
	kubectl apply -f helm/postgres-local.yaml
	kubectl apply -f helm/grafana-stack-local.yaml
	kubectl wait --for=condition=ready pod -l app=ollama --timeout=120s
	kubectl wait --for=condition=ready pod -l app=sentinel-postgres --timeout=60s
	kubectl wait --for=condition=ready pod -l app=grafana --timeout=60s
	helm upgrade --install sentinel ./helm/sentinel -f ./helm/sentinel/values-local.yaml

k8s-deploy:
	# Full rebuild + redeploy (cleans stale migration job, rebuilds image, upgrades Helm)
	helm uninstall sentinel || true
	kubectl delete job sentinel-migration --ignore-not-found
	$(MAKE) k8s-up

k8s-down:
	helm uninstall sentinel || true
	kubectl delete -f helm/grafana-stack-local.yaml || true
	kubectl delete -f helm/postgres-local.yaml || true
	kubectl delete -f helm/ollama-local.yaml || true

k8s-logs:
	kubectl logs -l app.kubernetes.io/name=sentinel --all-containers --prefix -f

clean:
	find src/ tests/ -type d -name "__pycache__" -exec rm -rf {} +
	find . -maxdepth 1 -type d -name "*.egg-info" -exec rm -rf {} +
	rm -rf .mypy_cache .pytest_cache .ruff_cache htmlcov .coverage