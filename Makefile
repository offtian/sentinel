.PHONY: install lock run-api run-celery-worker test lint lint-fix \
	run-db-migrations build-migration downgrade-db-migration \
	docker-build docker-compose-up smoke-test test-evals

# Setup
install:
	uv sync --all-extras

lock:
	uv lock

# Development
run-api:
	uv run uvicorn sentinel.interfaces.api.app:app --host 0.0.0.0 --port 8000 --reload

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

# Database
run-db-migrations:
	uv run alembic -c src/sentinel/data/migrations/alembic.ini upgrade head

build-migration:
	uv run alembic -c src/sentinel/data/migrations/alembic.ini revision --autogenerate -m "$(MESSAGE)"

downgrade-db-migration:
	uv run alembic -c src/sentinel/data/migrations/alembic.ini downgrade -1

# Docker
docker-build:
	docker build -t sentinel:latest .

docker-compose-up:
	docker compose up -d
