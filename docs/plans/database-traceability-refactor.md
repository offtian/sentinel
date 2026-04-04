# Database Traceability Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Standardise all database operations on the `databases` async library, add a `databases.Database` singleton, add `trace_id` correlation across all tables, and introduce pipeline execution tracing tables — all via Alembic migrations.

**Architecture:** The project currently has a split persistence strategy: SQLAlchemy `AsyncSession` + SQLModel ORM for operational tables (investigations, tickets, jobs, audit) vs `databases.Database` with raw SQL for comparison/eval tables. This refactor unifies everything on the `databases` library pattern (explicit raw SQL, dependency-injected `db` parameter). New traceability tables (`pipeline_runs`, `node_executions`, `agent_calls`) capture the full execution chain with a `trace_id` UUID correlation ID. An `ExecutionTracer` domain class replaces the in-memory `TraceCollector`.

**Tech Stack:** Python `databases` library (async PostgreSQL), Alembic migrations, `attrs` frozen classes, `structlog`

> **Architecture deviation (implemented):** During implementation, persistence functions were placed in the **domain layer** (`domain/$category/{queries,operations}.py`) instead of the originally planned `data/` layer. This aligns with the project's existing pattern where domain logic owns its own persistence via SQLAlchemy Core expressions with `databases.Database` dependency injection. The `data/` layer retains only SQLModel table definitions (for Alembic metadata) and the `databases.Database` singleton. See updated file paths in the tables below.

---

## File Structure

### New Files
| Planned File | Actual File | Responsibility |
|------|------|---------------|
| `src/sentinel/data/db.py` | `src/sentinel/data/db.py` | `databases.Database` singleton: `get_db()`, `connect_db()`, `disconnect_db()` |
| `src/sentinel/data/investigations.py` | `src/sentinel/domain/sre/{queries,operations}.py` | Investigation persistence (reads + writes) |
| `src/sentinel/data/ticket_reviews.py` | `src/sentinel/domain/support/{queries,operations}.py` | Ticket review persistence (reads + writes) |
| `src/sentinel/data/audit.py` | `src/sentinel/domain/audit/operations.py` | Audit log persistence (append-only) |
| `src/sentinel/data/jobs.py` | `src/sentinel/domain/jobs/{queries,operations}.py` | Job queue persistence (reads + writes) |
| `src/sentinel/data/tracing.py` | `src/sentinel/domain/pipeline/{queries,operations}.py` | Pipeline/node/agent trace persistence |
| `src/sentinel/domain/pipeline/tracer.py` | _(not yet implemented)_ | `ExecutionTracer` — DB-backed replacement for `TraceCollector` |
| `src/sentinel/data/migrations/alembic/versions/003_add_traceability.py` | `src/sentinel/data/migrations/alembic/versions/003_add_traceability.py` | Alembic migration: new tables + `trace_id` on existing tables |
| `src/sentinel/data/migrations/alembic/versions/002a_...` | `src/sentinel/data/migrations/alembic/versions/002a_create_support_and_sre_tables.py` | Alembic migration: investigation_records + ticket_review_records tables |
| `src/sentinel/data/tracing_models.py` | `src/sentinel/data/tracing_models.py` | SQLModel classes for pipeline_runs, node_executions, agent_calls |
| `src/sentinel/data/evaluation_models.py` | `src/sentinel/data/evaluation_models.py` | SQLModel classes for comparison_runs, eval_runs |
| `tests/unit/data/test_db.py` | `tests/unit/data/test_db.py` | Tests for database singleton |
| `tests/unit/data/test_investigations.py` | `tests/unit/domain/sre/test_{queries,operations}.py` | Tests for investigation persistence |
| `tests/unit/data/test_ticket_reviews.py` | `tests/unit/domain/support/test_{queries,operations}.py` | Tests for ticket review persistence |
| `tests/unit/data/test_audit.py` | `tests/unit/domain/audit/test_operations.py` | Tests for audit persistence |
| `tests/unit/data/test_jobs.py` | `tests/unit/domain/jobs/test_{queries,operations}.py` | Tests for job queue persistence |
| `tests/unit/data/test_tracing.py` | `tests/unit/domain/pipeline/test_{queries,operations}.py` | Tests for tracing persistence |
| `tests/unit/domain/pipeline/test_tracer.py` | _(not yet implemented)_ | Tests for `ExecutionTracer` |

### Modified Files
| File | Change |
|------|--------|
| `src/sentinel/data/__init__.py` | Re-export `get_db`, `connect_db`, `disconnect_db` |
| `src/sentinel/data/database.py` | Keep as-is for backward compat during migration; deprecate later |
| `src/sentinel/interfaces/api/app.py` | Add `databases.Database` lifecycle alongside existing engine |
| `src/sentinel/worker.py` | Add `databases.Database` lifecycle; pass `db` to persistence closures |
| `src/sentinel/interfaces/mcp/server.py` | Use `get_db()` instead of manual `_db` module state |
| `src/sentinel/interfaces/mcp/tools/investigation.py` | Import from `data.jobs` instead of inline raw SQL |
| `src/sentinel/domain/pipeline/types.py` | Add `ExecutionTracer` protocol; keep `TraceCollector` for backward compat |
| `src/sentinel/interfaces/graphs/sre_investigation.py` | Accept `ExecutionTracer`; record node executions |
| `src/sentinel/interfaces/graphs/support_review.py` | Accept `ExecutionTracer`; record node executions |

### Deleted After Migration (final cleanup task)
| File | Replaced By | Status |
|------|-------------|--------|
| `src/sentinel/application/sre/persist.py` | `domain/sre/{queries,operations}.py` | Still exists — pending Task 12 |
| `src/sentinel/application/support/persist.py` | `domain/support/{queries,operations}.py` | Still exists — pending Task 12 |
| `src/sentinel/application/audit/persist.py` | `domain/audit/operations.py` | Still exists — pending Task 12 |
| `src/sentinel/application/jobs/enqueue.py` | `domain/jobs/operations.py` | Still exists — pending Task 12 |
| `src/sentinel/application/jobs/dequeue.py` | `domain/jobs/{queries,operations}.py` | Still exists — pending Task 12 |

---

## Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Persistence library | `databases` with SQLAlchemy Core expressions | User requirement; already proven in `comparison.py`/`eval_runs.py`; type-safe column refs via `col()` wrapper |
| Persistence location | `domain/$category/{queries,operations}.py` | Architecture correction: queries belong in domain layer alongside business logic, not in data layer |
| Tracing granularity | pipeline_run → node_execution → agent_call (3 levels) | Matches actual execution hierarchy; `tool_invocations` deferred (captured in agent message history JSON) |
| Correlation strategy | `trace_id` UUID on all tables | Single ID links webhook → job → pipeline → nodes → agents; enables `WHERE trace_id = ?` across all tables |
| `TraceCollector` backward compat | Keep as protocol; `ExecutionTracer` satisfies same interface | Streamlit UI and tests still work without change |
| Job queue `FOR UPDATE SKIP LOCKED` | Rewrite as raw SQL via `databases` | The SQLModel version uses `with_for_update(skip_locked=True)`; equivalent raw SQL is `SELECT ... FOR UPDATE SKIP LOCKED` |
| SQLModel models | Keep `models.py`, `job_models.py`, `audit_models.py` for Alembic metadata | Alembic uses SQLModel metadata for autogenerate; models remain as schema definitions even though runtime access uses raw SQL |
| Migration strategy | Parallel operation period | New `data/*.py` modules coexist with old `application/*/persist.py` until all callers migrated; then delete old files |

---

### Task 1: Database Singleton (`data/db.py`) ✅

**Files:**
- Create: `src/sentinel/data/db.py`
- Modify: `src/sentinel/data/__init__.py`
- Test: `tests/unit/data/test_db.py`

- [x] **Step 1: Write the failing test**

```python
# tests/unit/data/test_db.py
"""Tests for the databases.Database singleton."""

from __future__ import annotations

from unittest import mock

import databases
import pytest

from sentinel.data import db


class TestGetDb:
    def test_returns_database_instance(self) -> None:
        # Given a configured database URL
        mock_settings = mock.MagicMock()
        mock_settings.database_url = "postgresql+asyncpg://user:pass@localhost/sentinel"

        with mock.patch.object(db, "get_settings", return_value=mock_settings):
            db._db = None  # Reset singleton

            # When get_db is called
            result = db.get_db()

            # Then it returns a databases.Database instance
            assert isinstance(result, databases.Database)

    def test_returns_same_instance_on_second_call(self) -> None:
        # Given get_db has been called once
        mock_settings = mock.MagicMock()
        mock_settings.database_url = "postgresql+asyncpg://user:pass@localhost/sentinel"

        with mock.patch.object(db, "get_settings", return_value=mock_settings):
            db._db = None
            first = db.get_db()

            # When get_db is called again
            second = db.get_db()

            # Then the same instance is returned
            assert first is second

    def test_raises_when_no_database_url(self) -> None:
        # Given no database URL is configured
        mock_settings = mock.MagicMock()
        mock_settings.database_url = ""

        with mock.patch.object(db, "get_settings", return_value=mock_settings):
            db._db = None

            # When get_db is called
            # Then it raises RuntimeError
            with pytest.raises(RuntimeError, match="DATABASE_URL"):
                db.get_db()


class TestConnectDb:
    @pytest.mark.asyncio
    async def test_connect_calls_database_connect(self) -> None:
        # Given a Database instance
        mock_db = mock.AsyncMock(spec=databases.Database)
        db._db = mock_db

        # When connect_db is called
        await db.connect_db()

        # Then the database connect method is called
        mock_db.connect.assert_awaited_once()


class TestDisconnectDb:
    @pytest.mark.asyncio
    async def test_disconnect_calls_database_disconnect(self) -> None:
        # Given a connected Database instance
        mock_db = mock.AsyncMock(spec=databases.Database)
        db._db = mock_db

        # When disconnect_db is called
        await db.disconnect_db()

        # Then the database disconnect method is called
        mock_db.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect_resets_singleton(self) -> None:
        # Given a connected Database instance
        mock_db = mock.AsyncMock(spec=databases.Database)
        db._db = mock_db

        # When disconnect_db is called
        await db.disconnect_db()

        # Then the singleton is reset
        assert db._db is None
```

- [x] **Step 2: Run test to verify it fails**

Run: `just test tests/unit/data/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sentinel.data.db'`

- [x] **Step 3: Write minimal implementation**

```python
# src/sentinel/data/db.py
"""
Async database singleton using the databases library.

Provides a single databases.Database instance shared across the application.
All entry points (API, worker, MCP server) call connect_db() on startup
and disconnect_db() on shutdown.
"""

from __future__ import annotations

import databases

from sentinel.settings import get_settings


_db: databases.Database | None = None


def get_db() -> databases.Database:
    """
    Return the cached databases.Database singleton.

    :raises RuntimeError: if DATABASE_URL is not configured.
    """
    global _db  # noqa: PLW0603
    if _db is None:
        url = get_settings().database_url
        if not url:
            raise RuntimeError(
                "DATABASE_URL is not configured. "
                "Set the DATABASE_URL environment variable."
            )
        # The databases library expects postgresql:// not postgresql+asyncpg://
        clean_url = url.replace("+asyncpg", "")
        _db = databases.Database(clean_url)
    return _db


async def connect_db() -> None:
    """Open the database connection pool. Call during application startup."""
    db = get_db()
    await db.connect()


async def disconnect_db() -> None:
    """Close the database connection pool and reset the singleton."""
    global _db  # noqa: PLW0603
    if _db is not None:
        await _db.disconnect()
        _db = None
```

- [x] **Step 4: Update `__init__.py` to re-export**

```python
# src/sentinel/data/__init__.py
from sentinel.data.db import connect_db, disconnect_db, get_db

__all__ = ["connect_db", "disconnect_db", "get_db"]
```

- [x] **Step 5: Run test to verify it passes**

Run: `just test tests/unit/data/test_db.py -v`
Expected: All 5 tests PASS

- [x] **Step 6: Commit**

```bash
git add src/sentinel/data/db.py src/sentinel/data/__init__.py tests/unit/data/test_db.py
git commit -m "feat: add databases.Database singleton in data/db.py"
```

> **Completed:** commit `13ff47d`

---

### Task 2: Alembic Migration — Traceability Tables and trace_id Columns ✅

**Files:**
- Create: `src/sentinel/data/migrations/alembic/versions/003_add_traceability.py`

- [x] **Step 1: Write the migration**

```python
# src/sentinel/data/migrations/alembic/versions/003_add_traceability.py
"""
Add pipeline traceability tables and trace_id correlation columns.

Revision ID: 003
Revises: 002
Create Date: 2026-04-04

Adds:
- trace_id column to investigation_records, ticket_review_records, job_requests
- pipeline_runs table: one row per pipeline execution
- node_executions table: one row per graph node execution
- agent_calls table: one row per PydanticAI agent run
"""

import sqlalchemy as sa
from alembic import op


revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- Add trace_id to existing tables --
    op.add_column(
        "investigation_records",
        sa.Column("trace_id", sa.Uuid(), nullable=True, index=True),
    )
    op.add_column(
        "ticket_review_records",
        sa.Column("trace_id", sa.Uuid(), nullable=True, index=True),
    )
    op.add_column(
        "job_requests",
        sa.Column("trace_id", sa.Uuid(), nullable=True, index=True),
    )

    # -- pipeline_runs --
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("trace_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("pipeline_type", sa.String(), nullable=False),
        sa.Column("job_request_id", sa.Uuid(), nullable=True, index=True),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column("input_json", sa.JSON(), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # -- node_executions --
    op.create_table(
        "node_executions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("trace_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("pipeline_run_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("node_name", sa.String(), nullable=False),
        sa.Column("node_order", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column("input_json", sa.JSON(), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # -- agent_calls --
    op.create_table(
        "agent_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("trace_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("node_execution_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("agent_name", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False, server_default=""),
        sa.Column("messages_json", sa.JSON(), nullable=True),
        sa.Column("token_usage_json", sa.JSON(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("agent_calls")
    op.drop_table("node_executions")
    op.drop_table("pipeline_runs")
    op.remove_column("job_requests", "trace_id")
    op.remove_column("ticket_review_records", "trace_id")
    op.remove_column("investigation_records", "trace_id")
```

- [x] **Step 2: Verify migration syntax**

Run: `cd /Users/fengtian/projects/sentinel && python -c "import importlib; importlib.import_module('sentinel.data.migrations.alembic.versions.003_add_traceability')"`
Expected: No import errors

- [x] **Step 3: Commit**

```bash
git add src/sentinel/data/migrations/alembic/versions/003_add_traceability.py
git commit -m "feat: add Alembic migration 003 for traceability tables and trace_id columns"
```

> **Completed:** commit `f7fb699`. Additional migration `002a` added in commit `c196b43` for `investigation_records` and `ticket_review_records` tables.

---

### Task 3: Investigation Persistence via `databases` ✅

> **Architecture change:** Implemented in `domain/sre/{queries,operations}.py` instead of `data/investigations.py`. Uses SQLAlchemy Core expressions with `col()` wrapper, not raw SQL strings. Tests at `tests/unit/domain/sre/test_{queries,operations}.py`.

**Files (actual):**
- Create: `src/sentinel/domain/sre/queries.py`, `src/sentinel/domain/sre/operations.py`
- Test: `tests/unit/domain/sre/test_queries.py`, `tests/unit/domain/sre/test_operations.py`

- [x] **Step 1: Write the failing test**

```python
# tests/unit/data/test_investigations.py
"""Tests for investigation persistence via the databases library."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest import mock

import pytest

from sentinel.data import investigations


@pytest.fixture
def mock_db() -> mock.AsyncMock:
    return mock.AsyncMock()


class TestPersistInvestigation:
    @pytest.mark.asyncio
    async def test_inserts_investigation_record(self, mock_db: mock.AsyncMock) -> None:
        # Given investigation details
        alert_id = "alert-123"
        trace_id = uuid.uuid4()

        # When persist_investigation is called
        record_id = await investigations.persist_investigation(
            db=mock_db,
            alert_source="pagerduty",
            alert_id=alert_id,
            alert_title="CPU spike on api-server",
            severity="critical",
            service="api-server",
            status="completed",
            root_cause="Memory leak in connection pool",
            remediation="Restart pods and increase pool limit",
            confidence_score=0.85,
            findings_json={"summary": "Found memory leak"},
            started_at=datetime(2026, 4, 4, 10, 0, tzinfo=UTC),
            completed_at=datetime(2026, 4, 4, 10, 5, tzinfo=UTC),
            trace_id=trace_id,
        )

        # Then a UUID is returned
        assert isinstance(record_id, uuid.UUID)

        # And the database execute was called with INSERT
        mock_db.execute.assert_awaited_once()
        call_kwargs = mock_db.execute.call_args
        assert "INSERT INTO investigation_records" in call_kwargs.kwargs["query"]
        assert call_kwargs.kwargs["values"]["alert_id"] == alert_id
        assert call_kwargs.kwargs["values"]["trace_id"] == trace_id


class TestFetchInvestigation:
    @pytest.mark.asyncio
    async def test_fetches_by_id(self, mock_db: mock.AsyncMock) -> None:
        # Given a record exists
        record_id = uuid.uuid4()
        mock_db.fetch_one.return_value = {"id": record_id, "alert_id": "alert-1", "status": "completed"}

        # When fetch_investigation is called
        result = await investigations.fetch_investigation(db=mock_db, record_id=record_id)

        # Then the record is returned
        assert result is not None
        assert result["id"] == record_id

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self, mock_db: mock.AsyncMock) -> None:
        # Given no record exists
        mock_db.fetch_one.return_value = None

        # When fetch_investigation is called
        result = await investigations.fetch_investigation(db=mock_db, record_id=uuid.uuid4())

        # Then None is returned
        assert result is None


class TestFetchInvestigationsByAlertId:
    @pytest.mark.asyncio
    async def test_fetches_by_alert_id(self, mock_db: mock.AsyncMock) -> None:
        # Given records exist for an alert
        mock_db.fetch_all.return_value = [
            {"id": uuid.uuid4(), "alert_id": "alert-1"},
        ]

        # When fetch_investigations_by_alert_id is called
        results = await investigations.fetch_investigations_by_alert_id(
            db=mock_db, alert_id="alert-1"
        )

        # Then matching records are returned
        assert len(results) == 1


class TestFetchInvestigationsForService:
    @pytest.mark.asyncio
    async def test_fetches_by_service(self, mock_db: mock.AsyncMock) -> None:
        # Given records exist for a service
        mock_db.fetch_all.return_value = [
            {"id": uuid.uuid4(), "service": "api-server"},
        ]

        # When fetch_investigations_for_service is called
        results = await investigations.fetch_investigations_for_service(
            db=mock_db, service="api-server", limit=5
        )

        # Then matching records are returned
        assert len(results) == 1
        call_kwargs = mock_db.fetch_all.call_args.kwargs
        assert call_kwargs["values"]["limit"] == 5
```

- [x] **Step 2: Run test to verify it fails**

Run: `just test tests/unit/data/test_investigations.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Write minimal implementation**

```python
# src/sentinel/data/investigations.py
"""
Persist and fetch investigation records via the databases library.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import databases

from sentinel.utils import logs


async def persist_investigation(
    *,
    db: databases.Database,
    alert_source: str,
    alert_id: str,
    alert_title: str,
    severity: str,
    service: str,
    status: str = "completed",
    root_cause: str | None = None,
    remediation: str | None = None,
    confidence_score: float | None = None,
    findings_json: dict[str, Any] | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    trace_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """
    Insert an investigation record.

    :param db: The async database connection.
    :param alert_source: Source of the alert (e.g. "pagerduty").
    :param alert_id: Unique identifier for the alert.
    :param alert_title: Human-readable alert title.
    :param severity: Alert severity level.
    :param service: Affected service name.
    :param status: Investigation status (default "completed").
    :param root_cause: Identified root cause.
    :param remediation: Recommended remediation steps.
    :param confidence_score: Confidence score (0.0-1.0).
    :param findings_json: Structured findings data.
    :param started_at: When the investigation started.
    :param completed_at: When the investigation completed.
    :param trace_id: Correlation ID for end-to-end tracing.
    :returns: The UUID of the inserted row.
    """
    row_id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    query = """
        INSERT INTO investigation_records (
            id, alert_source, alert_id, alert_title, severity, service,
            status, root_cause, remediation, confidence_score, findings_json,
            started_at, completed_at, created_at, trace_id
        ) VALUES (
            :id, :alert_source, :alert_id, :alert_title, :severity, :service,
            :status, :root_cause, :remediation, :confidence_score, :findings_json,
            :started_at, :completed_at, :created_at, :trace_id
        )
    """
    await db.execute(
        query=query,
        values={
            "id": row_id,
            "alert_source": alert_source,
            "alert_id": alert_id,
            "alert_title": alert_title,
            "severity": severity,
            "service": service,
            "status": status,
            "root_cause": root_cause,
            "remediation": remediation,
            "confidence_score": confidence_score,
            "findings_json": findings_json,
            "started_at": started_at or now,
            "completed_at": completed_at or now,
            "created_at": now,
            "trace_id": trace_id,
        },
    )

    logs.log_event(
        "investigation_persisted",
        params={"record_id": str(row_id), "alert_id": alert_id},
    )

    return row_id


async def fetch_investigation(
    *,
    db: databases.Database,
    record_id: uuid.UUID,
) -> dict[str, Any] | None:
    """
    Fetch an investigation record by its ID.

    :param db: The async database connection.
    :param record_id: The investigation record UUID.
    :returns: Row dict or None if not found.
    """
    query = """
        SELECT id, alert_source, alert_id, alert_title, severity, service,
               status, root_cause, remediation, confidence_score, findings_json,
               started_at, completed_at, created_at, trace_id
        FROM investigation_records
        WHERE id = :id
    """
    row = await db.fetch_one(query=query, values={"id": record_id})
    return dict(row) if row is not None else None


async def fetch_investigations_by_alert_id(
    *,
    db: databases.Database,
    alert_id: str,
) -> list[dict[str, Any]]:
    """
    Fetch all investigations for a specific alert.

    :param db: The async database connection.
    :param alert_id: The alert identifier to filter by.
    :returns: List of row dicts ordered by created_at descending.
    """
    query = """
        SELECT id, alert_source, alert_id, alert_title, severity, service,
               status, confidence_score, created_at, trace_id
        FROM investigation_records
        WHERE alert_id = :alert_id
        ORDER BY created_at DESC
    """
    rows = await db.fetch_all(query=query, values={"alert_id": alert_id})
    return [dict(row) for row in rows]


async def fetch_investigations_for_service(
    *,
    db: databases.Database,
    service: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Fetch recent investigations for a given service.

    :param db: The async database connection.
    :param service: Service name to filter by.
    :param limit: Maximum rows to return.
    :returns: List of row dicts ordered by created_at descending.
    """
    query = """
        SELECT id, alert_source, alert_id, alert_title, severity, service,
               status, confidence_score, created_at, trace_id
        FROM investigation_records
        WHERE service = :service
        ORDER BY created_at DESC
        LIMIT :limit
    """
    rows = await db.fetch_all(
        query=query, values={"service": service, "limit": limit}
    )
    return [dict(row) for row in rows]
```

- [x] **Step 4: Run test to verify it passes**

Run: `just test tests/unit/data/test_investigations.py -v`
Expected: All tests PASS

- [x] **Step 5: Commit**

```bash
git add src/sentinel/data/investigations.py tests/unit/data/test_investigations.py
git commit -m "feat: add investigation persistence via databases library"
```

> **Completed:** commits `c4cc5f3`, `bde4af0` (domain layer migration), and subsequent refactors.

---

### Task 4: Ticket Review Persistence via `databases` ✅

> **Architecture change:** Implemented in `domain/support/{queries,operations}.py` instead of `data/ticket_reviews.py`. Tests at `tests/unit/domain/support/test_{queries,operations}.py`.

**Files (actual):**
- Create: `src/sentinel/domain/support/queries.py`, `src/sentinel/domain/support/operations.py`
- Test: `tests/unit/domain/support/test_queries.py`, `tests/unit/domain/support/test_operations.py`

- [x] **Step 1: Write the failing test**

```python
# tests/unit/data/test_ticket_reviews.py
"""Tests for ticket review persistence via the databases library."""

from __future__ import annotations

import uuid
from unittest import mock

import pytest

from sentinel.data import ticket_reviews


@pytest.fixture
def mock_db() -> mock.AsyncMock:
    return mock.AsyncMock()


class TestPersistTicketReview:
    @pytest.mark.asyncio
    async def test_inserts_ticket_review_record(self, mock_db: mock.AsyncMock) -> None:
        # Given ticket review details
        trace_id = uuid.uuid4()

        # When persist_ticket_review is called
        record_id = await ticket_reviews.persist_ticket_review(
            db=mock_db,
            ticket_id="TICKET-100",
            ticket_key="SD-100",
            suggested_response="Try restarting the service.",
            sources_json={"sources": [{"title": "Runbook"}]},
            confidence_score=0.9,
            category="infrastructure",
            trace_id=trace_id,
        )

        # Then a UUID is returned
        assert isinstance(record_id, uuid.UUID)

        # And the database execute was called with INSERT
        mock_db.execute.assert_awaited_once()
        call_kwargs = mock_db.execute.call_args
        assert "INSERT INTO ticket_review_records" in call_kwargs.kwargs["query"]
        assert call_kwargs.kwargs["values"]["trace_id"] == trace_id


class TestFetchTicketReview:
    @pytest.mark.asyncio
    async def test_fetches_by_id(self, mock_db: mock.AsyncMock) -> None:
        # Given a record exists
        record_id = uuid.uuid4()
        mock_db.fetch_one.return_value = {"id": record_id, "ticket_key": "SD-100"}

        # When fetch_ticket_review is called
        result = await ticket_reviews.fetch_ticket_review(db=mock_db, record_id=record_id)

        # Then the record is returned
        assert result is not None
        assert result["ticket_key"] == "SD-100"


class TestFetchReviewsForTicket:
    @pytest.mark.asyncio
    async def test_fetches_by_ticket_key(self, mock_db: mock.AsyncMock) -> None:
        # Given records exist
        mock_db.fetch_all.return_value = [{"id": uuid.uuid4(), "ticket_key": "SD-100"}]

        # When fetch_reviews_for_ticket is called
        results = await ticket_reviews.fetch_reviews_for_ticket(db=mock_db, ticket_key="SD-100")

        # Then matching records are returned
        assert len(results) == 1


class TestUpdateReviewStatus:
    @pytest.mark.asyncio
    async def test_updates_status(self, mock_db: mock.AsyncMock) -> None:
        # Given a record exists
        record_id = uuid.uuid4()

        # When update_review_status is called
        await ticket_reviews.update_review_status(
            db=mock_db, record_id=record_id, status="accepted"
        )

        # Then the database execute was called with UPDATE
        mock_db.execute.assert_awaited_once()
        call_kwargs = mock_db.execute.call_args
        assert "UPDATE ticket_review_records" in call_kwargs.kwargs["query"]
        assert call_kwargs.kwargs["values"]["status"] == "accepted"
```

- [x] **Step 2: Run test to verify it fails**

Run: `just test tests/unit/data/test_ticket_reviews.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Write minimal implementation**

```python
# src/sentinel/data/ticket_reviews.py
"""
Persist and fetch ticket review records via the databases library.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import databases

from sentinel.utils import logs


async def persist_ticket_review(
    *,
    db: databases.Database,
    ticket_id: str,
    ticket_key: str,
    suggested_response: str,
    sources_json: dict[str, Any] | None = None,
    confidence_score: float | None = None,
    category: str | None = None,
    trace_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """
    Insert a ticket review record.

    :param db: The async database connection.
    :param ticket_id: Internal ticket identifier.
    :param ticket_key: Human-readable ticket key (e.g. "SD-100").
    :param suggested_response: AI-drafted response text.
    :param sources_json: Documentation sources used.
    :param confidence_score: Response confidence (0.0-1.0).
    :param category: Ticket category.
    :param trace_id: Correlation ID for end-to-end tracing.
    :returns: The UUID of the inserted row.
    """
    row_id = uuid.uuid4()
    query = """
        INSERT INTO ticket_review_records (
            id, ticket_id, ticket_key, suggested_response, sources_json,
            confidence_score, category, status, created_at, trace_id
        ) VALUES (
            :id, :ticket_id, :ticket_key, :suggested_response, :sources_json,
            :confidence_score, :category, :status, :created_at, :trace_id
        )
    """
    await db.execute(
        query=query,
        values={
            "id": row_id,
            "ticket_id": ticket_id,
            "ticket_key": ticket_key,
            "suggested_response": suggested_response,
            "sources_json": sources_json,
            "confidence_score": confidence_score,
            "category": category,
            "status": "drafted",
            "created_at": datetime.now(tz=UTC),
            "trace_id": trace_id,
        },
    )

    logs.log_event(
        "ticket_review_persisted",
        params={"record_id": str(row_id), "ticket_key": ticket_key},
    )

    return row_id


async def fetch_ticket_review(
    *,
    db: databases.Database,
    record_id: uuid.UUID,
) -> dict[str, Any] | None:
    """
    Fetch a ticket review record by its ID.

    :param db: The async database connection.
    :param record_id: The ticket review record UUID.
    :returns: Row dict or None if not found.
    """
    query = """
        SELECT id, ticket_id, ticket_key, suggested_response, sources_json,
               confidence_score, category, status, created_at, reviewed_at, trace_id
        FROM ticket_review_records
        WHERE id = :id
    """
    row = await db.fetch_one(query=query, values={"id": record_id})
    return dict(row) if row is not None else None


async def fetch_reviews_for_ticket(
    *,
    db: databases.Database,
    ticket_key: str,
) -> list[dict[str, Any]]:
    """
    Fetch all reviews for a specific ticket.

    :param db: The async database connection.
    :param ticket_key: Ticket key to filter by.
    :returns: List of row dicts ordered by created_at descending.
    """
    query = """
        SELECT id, ticket_id, ticket_key, suggested_response, sources_json,
               confidence_score, category, status, created_at, reviewed_at, trace_id
        FROM ticket_review_records
        WHERE ticket_key = :ticket_key
        ORDER BY created_at DESC
    """
    rows = await db.fetch_all(query=query, values={"ticket_key": ticket_key})
    return [dict(row) for row in rows]


async def update_review_status(
    *,
    db: databases.Database,
    record_id: uuid.UUID,
    status: str,
) -> None:
    """
    Update the status of a ticket review.

    :param db: The async database connection.
    :param record_id: The ticket review record UUID.
    :param status: New status value (e.g. "accepted", "rejected").
    """
    query = """
        UPDATE ticket_review_records
        SET status = :status, reviewed_at = :reviewed_at
        WHERE id = :id
    """
    await db.execute(
        query=query,
        values={
            "id": record_id,
            "status": status,
            "reviewed_at": datetime.now(tz=UTC),
        },
    )

    logs.log_event(
        "ticket_review_status_updated",
        params={"record_id": str(record_id), "status": status},
    )
```

- [x] **Step 4: Run test to verify it passes**

Run: `just test tests/unit/data/test_ticket_reviews.py -v`
Expected: All tests PASS

- [x] **Step 5: Commit**

```bash
git add src/sentinel/data/ticket_reviews.py tests/unit/data/test_ticket_reviews.py
git commit -m "feat: add ticket review persistence via databases library"
```

> **Completed:** commits `47192be`, `bde4af0` (domain layer migration), and subsequent refactors.

---

### Task 5: Audit Log Persistence via `databases` ✅

> **Architecture change:** Implemented in `domain/audit/operations.py` instead of `data/audit.py`. Tests at `tests/unit/domain/audit/test_operations.py`.

**Files (actual):**
- Create: `src/sentinel/domain/audit/operations.py`
- Test: `tests/unit/domain/audit/test_operations.py`

- [x] **Step 1: Write the failing test**

```python
# tests/unit/data/test_audit.py
"""Tests for audit log persistence via the databases library."""

from __future__ import annotations

import uuid
from unittest import mock

import pytest

from sentinel.data import audit


@pytest.fixture
def mock_db() -> mock.AsyncMock:
    return mock.AsyncMock()


class TestRecordAuditEntry:
    @pytest.mark.asyncio
    async def test_inserts_audit_record(self, mock_db: mock.AsyncMock) -> None:
        # Given audit entry details
        details = {"action_detail": "classified as critical"}

        # When record_audit_entry is called
        record_id = await audit.record_audit_entry(
            db=mock_db,
            actor="sre_pipeline",
            action="alert.classified",
            resource_type="investigation",
            resource_id="inv-123",
            details=details,
            input_hash="abc123",
            model_id="gpt-4.1",
            prompt_version="v2",
        )

        # Then a UUID is returned
        assert isinstance(record_id, uuid.UUID)

        # And the database execute was called with INSERT
        mock_db.execute.assert_awaited_once()
        call_kwargs = mock_db.execute.call_args
        assert "INSERT INTO audit_log" in call_kwargs.kwargs["query"]
        assert call_kwargs.kwargs["values"]["actor"] == "sre_pipeline"

    @pytest.mark.asyncio
    async def test_serializes_details_to_json(self, mock_db: mock.AsyncMock) -> None:
        # Given details with various types
        details = {"count": 42, "tags": ["prod", "critical"]}

        # When record_audit_entry is called
        await audit.record_audit_entry(
            db=mock_db,
            actor="system",
            action="test",
            resource_type="test",
            resource_id="test-1",
            details=details,
            input_hash="hash",
        )

        # Then details_json is a string
        call_kwargs = mock_db.execute.call_args
        details_value = call_kwargs.kwargs["values"]["details_json"]
        assert isinstance(details_value, str)
        assert "42" in details_value
```

- [x] **Step 2: Run test to verify it fails**

Run: `just test tests/unit/data/test_audit.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Write minimal implementation**

```python
# src/sentinel/data/audit.py
"""
Persist audit log entries via the databases library.

Append-only — no UPDATE or DELETE operations.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import databases

from sentinel.utils import logs


async def record_audit_entry(
    *,
    db: databases.Database,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str,
    details: dict[str, Any],
    input_hash: str,
    model_id: str = "",
    prompt_version: str = "",
) -> uuid.UUID:
    """
    Append an immutable audit entry to the audit log.

    :param db: The async database connection.
    :param actor: Who performed the action.
    :param action: What action was performed.
    :param resource_type: Type of resource acted upon.
    :param resource_id: Identifier of the resource.
    :param details: Structured details about the action.
    :param input_hash: Hash of the input data.
    :param model_id: LLM model identifier (if applicable).
    :param prompt_version: Prompt template version (if applicable).
    :returns: The UUID of the inserted row.
    """
    row_id = uuid.uuid4()
    query = """
        INSERT INTO audit_log (
            id, timestamp, actor, action, resource_type, resource_id,
            details_json, input_hash, model_id, prompt_version
        ) VALUES (
            :id, :timestamp, :actor, :action, :resource_type, :resource_id,
            :details_json, :input_hash, :model_id, :prompt_version
        )
    """
    await db.execute(
        query=query,
        values={
            "id": row_id,
            "timestamp": datetime.now(tz=UTC),
            "actor": actor,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "details_json": json.dumps(details, default=str),
            "input_hash": input_hash,
            "model_id": model_id,
            "prompt_version": prompt_version,
        },
    )

    logs.log_event(
        "audit_entry_recorded",
        params={
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
        },
    )

    return row_id
```

- [x] **Step 4: Run test to verify it passes**

Run: `just test tests/unit/data/test_audit.py -v`
Expected: All tests PASS

- [x] **Step 5: Commit**

```bash
git add src/sentinel/data/audit.py tests/unit/data/test_audit.py
git commit -m "feat: add audit log persistence via databases library"
```

> **Completed:** commits `c1db893`, `bde4af0` (domain layer migration), and subsequent refactors.

---

### Task 6: Job Queue Persistence via `databases` ✅

> **Architecture change:** Implemented in `domain/jobs/{queries,operations}.py` instead of `data/jobs.py`. Tests at `tests/unit/domain/jobs/test_{queries,operations}.py`.

**Files (actual):**
- Create: `src/sentinel/domain/jobs/queries.py`, `src/sentinel/domain/jobs/operations.py`
- Test: `tests/unit/domain/jobs/test_queries.py`, `tests/unit/domain/jobs/test_operations.py`

This is the most complex migration because of `SELECT ... FOR UPDATE SKIP LOCKED`.

- [x] **Step 1: Write the failing test**

```python
# tests/unit/data/test_jobs.py
"""Tests for job queue persistence via the databases library."""

from __future__ import annotations

import uuid
from unittest import mock

import pytest

from sentinel.data import jobs


@pytest.fixture
def mock_db() -> mock.AsyncMock:
    return mock.AsyncMock()


class TestEnqueueJob:
    @pytest.mark.asyncio
    async def test_inserts_job_request(self, mock_db: mock.AsyncMock) -> None:
        # Given job details
        payload = {"alert_id": "alert-1", "source": "pagerduty"}

        # When enqueue_job is called
        job_id = await jobs.enqueue_job(
            db=mock_db,
            job_type="sre_investigation",
            payload=payload,
            requested_by="webhook",
            source_id="alert-1",
            trace_id=uuid.uuid4(),
        )

        # Then a UUID is returned
        assert isinstance(job_id, uuid.UUID)

        # And the database execute was called with INSERT
        mock_db.execute.assert_awaited_once()
        call_kwargs = mock_db.execute.call_args
        assert "INSERT INTO job_requests" in call_kwargs.kwargs["query"]
        assert call_kwargs.kwargs["values"]["job_type"] == "sre_investigation"

    @pytest.mark.asyncio
    async def test_generates_idempotency_key(self, mock_db: mock.AsyncMock) -> None:
        # Given job details
        # When enqueue_job is called
        await jobs.enqueue_job(
            db=mock_db,
            job_type="sre_investigation",
            payload={"alert_id": "a1"},
            requested_by="test",
            source_id="a1",
        )

        # Then idempotency_key is set
        call_kwargs = mock_db.execute.call_args
        key = call_kwargs.kwargs["values"]["idempotency_key"]
        assert "sre_investigation" in key
        assert "a1" in key


class TestClaimNextJob:
    @pytest.mark.asyncio
    async def test_returns_job_when_available(self, mock_db: mock.AsyncMock) -> None:
        # Given a pending job exists
        job_id = uuid.uuid4()
        mock_db.fetch_one.return_value = {
            "id": job_id,
            "job_type": "sre_investigation",
            "payload_json": "{}",
            "status": "pending",
        }

        # When claim_next_job is called
        result = await jobs.claim_next_job(db=mock_db, worker_id="worker-1")

        # Then the job is returned
        assert result is not None
        assert result["id"] == job_id

        # And it was claimed with UPDATE
        assert mock_db.execute.await_count >= 1

    @pytest.mark.asyncio
    async def test_returns_none_when_no_jobs(self, mock_db: mock.AsyncMock) -> None:
        # Given no pending jobs
        mock_db.fetch_one.return_value = None

        # When claim_next_job is called
        result = await jobs.claim_next_job(db=mock_db, worker_id="worker-1")

        # Then None is returned
        assert result is None


class TestCompleteJob:
    @pytest.mark.asyncio
    async def test_marks_job_completed(self, mock_db: mock.AsyncMock) -> None:
        # Given a running job
        job_id = uuid.uuid4()

        # When complete_job is called
        result_id = await jobs.complete_job(
            db=mock_db,
            job_id=job_id,
            result_json='{"status": "ok"}',
            worker_id="worker-1",
        )

        # Then a result record UUID is returned
        assert isinstance(result_id, uuid.UUID)

        # And two executes: UPDATE job_requests + INSERT job_results
        assert mock_db.execute.await_count == 2


class TestFailJob:
    @pytest.mark.asyncio
    async def test_marks_job_failed_with_retry(self, mock_db: mock.AsyncMock) -> None:
        # Given a running job with retries remaining
        job_id = uuid.uuid4()

        # When fail_job is called with should_retry=True
        result_id = await jobs.fail_job(
            db=mock_db,
            job_id=job_id,
            error_message="Connection timeout",
            worker_id="worker-1",
            should_retry=True,
        )

        # Then a result record UUID is returned
        assert isinstance(result_id, uuid.UUID)

        # And the job status is reset to pending (UPDATE includes pending status)
        update_call = mock_db.execute.call_args_list[0]
        assert "pending" in str(update_call.kwargs["values"].get("status", ""))


class TestRecoverStaleJobs:
    @pytest.mark.asyncio
    async def test_requeues_stale_jobs(self, mock_db: mock.AsyncMock) -> None:
        # Given stale running jobs for this worker
        mock_db.execute.return_value = None  # UPDATE returns no value

        # When recover_stale_jobs is called
        await jobs.recover_stale_jobs(db=mock_db, worker_id="worker-1")

        # Then UPDATE was executed
        mock_db.execute.assert_awaited_once()
        call_kwargs = mock_db.execute.call_args
        assert "UPDATE job_requests" in call_kwargs.kwargs["query"]
        assert call_kwargs.kwargs["values"]["worker_id"] == "worker-1"
```

- [x] **Step 2: Run test to verify it fails**

Run: `just test tests/unit/data/test_jobs.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Write minimal implementation**

```python
# src/sentinel/data/jobs.py
"""
Job queue persistence via the databases library.

Implements PostgreSQL-backed work queue with SELECT ... FOR UPDATE SKIP LOCKED.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import databases

from sentinel.utils import logs


def _make_idempotency_key(*, job_type: str, source_id: str) -> str:
    """Generate a deterministic idempotency key for deduplication."""
    return hashlib.sha256(f"{job_type}:{source_id}".encode()).hexdigest()


async def enqueue_job(
    *,
    db: databases.Database,
    job_type: str,
    payload: dict[str, Any],
    requested_by: str,
    source_id: str,
    priority: int = 1,
    max_retries: int = 3,
    trace_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """
    Enqueue a job for background processing.

    :param db: The async database connection.
    :param job_type: Type of job (e.g. "sre_investigation").
    :param payload: Job payload dict.
    :param requested_by: Who requested the job.
    :param source_id: Source identifier for idempotency.
    :param priority: Job priority (lower = higher priority).
    :param max_retries: Maximum retry attempts.
    :param trace_id: Correlation ID for end-to-end tracing.
    :returns: The UUID of the inserted job.
    """
    job_id = uuid.uuid4()
    payload_json = json.dumps(payload, default=str)
    payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
    idempotency_key = _make_idempotency_key(job_type=job_type, source_id=source_id)

    query = """
        INSERT INTO job_requests (
            id, job_type, payload_json, payload_hash, status, priority,
            requested_by, idempotency_key, max_retries, trace_id
        ) VALUES (
            :id, :job_type, :payload_json, :payload_hash, :status, :priority,
            :requested_by, :idempotency_key, :max_retries, :trace_id
        )
    """
    await db.execute(
        query=query,
        values={
            "id": job_id,
            "job_type": job_type,
            "payload_json": payload_json,
            "payload_hash": payload_hash,
            "status": "pending",
            "priority": priority,
            "requested_by": requested_by,
            "idempotency_key": idempotency_key,
            "max_retries": max_retries,
            "trace_id": trace_id,
        },
    )

    logs.log_event(
        "job_enqueued",
        params={
            "job_id": str(job_id),
            "job_type": job_type,
            "priority": priority,
            "requested_by": requested_by,
        },
    )

    return job_id


async def claim_next_job(
    *,
    db: databases.Database,
    worker_id: str,
    job_types: tuple[str, ...] = ("sre_investigation", "support_review"),
) -> dict[str, Any] | None:
    """
    Claim the next available job using SELECT ... FOR UPDATE SKIP LOCKED.

    :param db: The async database connection.
    :param worker_id: Identifier for the claiming worker.
    :param job_types: Job types to claim.
    :returns: Job dict with status updated to 'running', or None.
    """
    # Build IN clause placeholders
    type_placeholders = ", ".join(f":jt{i}" for i in range(len(job_types)))
    type_values = {f"jt{i}": jt for i, jt in enumerate(job_types)}

    select_query = f"""
        SELECT id, job_type, payload_json, payload_hash, status, priority,
               requested_by, idempotency_key, locked_by, locked_at,
               retry_count, max_retries, created_at, trace_id
        FROM job_requests
        WHERE status = 'pending'
          AND job_type IN ({type_placeholders})
        ORDER BY priority ASC, created_at ASC
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    """

    row = await db.fetch_one(query=select_query, values=type_values)
    if row is None:
        return None

    row_dict = dict(row)
    job_id = row_dict["id"]
    now = datetime.now(tz=UTC)

    update_query = """
        UPDATE job_requests
        SET status = 'running', locked_by = :worker_id, locked_at = :locked_at
        WHERE id = :id
    """
    await db.execute(
        query=update_query,
        values={"id": job_id, "worker_id": worker_id, "locked_at": now},
    )

    row_dict["status"] = "running"
    row_dict["locked_by"] = worker_id
    row_dict["locked_at"] = now

    logs.log_event(
        "job_claimed",
        params={
            "job_id": str(job_id),
            "job_type": row_dict["job_type"],
            "worker_id": worker_id,
        },
    )

    return row_dict


async def fetch_job(
    *,
    db: databases.Database,
    job_id: uuid.UUID,
) -> dict[str, Any] | None:
    """
    Fetch a job request record by ID.

    :param db: The async database connection.
    :param job_id: The job request UUID.
    :returns: Row dict or None if not found.
    """
    query = """
        SELECT id, job_type, payload_json, payload_hash, status, priority,
               requested_by, idempotency_key, locked_by, locked_at,
               retry_count, max_retries, created_at, trace_id
        FROM job_requests
        WHERE id = :id
    """
    row = await db.fetch_one(query=query, values={"id": job_id})
    return dict(row) if row is not None else None


async def complete_job(
    *,
    db: databases.Database,
    job_id: uuid.UUID,
    result_json: str | None = None,
    worker_id: str,
) -> uuid.UUID:
    """
    Mark a job as completed and persist the result.

    :param db: The async database connection.
    :param job_id: The job request UUID.
    :param result_json: Serialised result data.
    :param worker_id: Worker that executed the job.
    :returns: The UUID of the inserted job result record.
    """
    now = datetime.now(tz=UTC)

    update_query = """
        UPDATE job_requests SET status = 'completed' WHERE id = :id
    """
    await db.execute(query=update_query, values={"id": job_id})

    result_id = uuid.uuid4()
    insert_query = """
        INSERT INTO job_results (
            id, job_request_id, status, result_json, started_at,
            completed_at, duration_ms, worker_id
        ) VALUES (
            :id, :job_request_id, :status, :result_json, :started_at,
            :completed_at, :duration_ms, :worker_id
        )
    """
    await db.execute(
        query=insert_query,
        values={
            "id": result_id,
            "job_request_id": job_id,
            "status": "completed",
            "result_json": result_json,
            "started_at": now,
            "completed_at": now,
            "duration_ms": 0,
            "worker_id": worker_id,
        },
    )

    logs.log_event(
        "job_completed",
        params={"job_id": str(job_id), "worker_id": worker_id},
    )

    return result_id


async def fail_job(
    *,
    db: databases.Database,
    job_id: uuid.UUID,
    error_message: str,
    worker_id: str,
    should_retry: bool = False,
) -> uuid.UUID:
    """
    Mark a job as failed and persist the error.

    :param db: The async database connection.
    :param job_id: The job request UUID.
    :param error_message: Error description.
    :param worker_id: Worker that executed the job.
    :param should_retry: Whether to re-queue the job as pending.
    :returns: The UUID of the inserted job result record.
    """
    now = datetime.now(tz=UTC)

    if should_retry:
        update_query = """
            UPDATE job_requests
            SET status = :status, locked_by = NULL, locked_at = NULL,
                retry_count = retry_count + 1
            WHERE id = :id
        """
        await db.execute(
            query=update_query,
            values={"id": job_id, "status": "pending"},
        )
    else:
        update_query = """
            UPDATE job_requests SET status = 'failed' WHERE id = :id
        """
        await db.execute(query=update_query, values={"id": job_id})

    result_id = uuid.uuid4()
    insert_query = """
        INSERT INTO job_results (
            id, job_request_id, status, error_message, completed_at,
            duration_ms, worker_id
        ) VALUES (
            :id, :job_request_id, :status, :error_message, :completed_at,
            :duration_ms, :worker_id
        )
    """
    await db.execute(
        query=insert_query,
        values={
            "id": result_id,
            "job_request_id": job_id,
            "status": "failed",
            "error_message": error_message,
            "completed_at": now,
            "duration_ms": 0,
            "worker_id": worker_id,
        },
    )

    logs.log_event(
        "job_failed",
        params={
            "job_id": str(job_id),
            "error": error_message,
            "will_retry": should_retry,
        },
    )

    return result_id


async def recover_stale_jobs(
    *,
    db: databases.Database,
    worker_id: str,
) -> None:
    """
    Re-queue any jobs left in 'running' state by this worker.

    :param db: The async database connection.
    :param worker_id: Worker whose stale jobs to recover.
    """
    query = """
        UPDATE job_requests
        SET status = 'pending', locked_by = NULL, locked_at = NULL
        WHERE status = 'running' AND locked_by = :worker_id
    """
    await db.execute(query=query, values={"worker_id": worker_id})

    logs.log_event(
        "stale_jobs_recovered",
        params={"worker_id": worker_id},
    )
```

- [x] **Step 4: Run test to verify it passes**

Run: `just test tests/unit/data/test_jobs.py -v`
Expected: All tests PASS

- [x] **Step 5: Commit**

```bash
git add src/sentinel/data/jobs.py tests/unit/data/test_jobs.py
git commit -m "feat: add job queue persistence via databases library"
```

> **Completed:** commits `602b630`, `bde4af0` (domain layer migration), and subsequent refactors. Also added `JobType` enum in commit `a2c4cf1`.

---

### Task 7: Tracing Persistence (`data/tracing.py`) ✅

> **Architecture change:** Implemented in `domain/pipeline/{queries,operations}.py` instead of `data/tracing.py`. SQLModel classes in `data/tracing_models.py`. Tests at `tests/unit/domain/pipeline/test_{queries,operations}.py`.

**Files (actual):**
- Create: `src/sentinel/domain/pipeline/queries.py`, `src/sentinel/domain/pipeline/operations.py`, `src/sentinel/data/tracing_models.py`
- Test: `tests/unit/domain/pipeline/test_queries.py`, `tests/unit/domain/pipeline/test_operations.py`

- [x] **Step 1: Write the failing test**

```python
# tests/unit/data/test_tracing.py
"""Tests for pipeline tracing persistence via the databases library."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest import mock

import pytest

from sentinel.data import tracing


@pytest.fixture
def mock_db() -> mock.AsyncMock:
    return mock.AsyncMock()


class TestPersistPipelineRun:
    @pytest.mark.asyncio
    async def test_inserts_pipeline_run(self, mock_db: mock.AsyncMock) -> None:
        # Given pipeline run details
        trace_id = uuid.uuid4()
        started_at = datetime(2026, 4, 4, 10, 0, tzinfo=UTC)

        # When persist_pipeline_run is called
        run_id = await tracing.persist_pipeline_run(
            db=mock_db,
            trace_id=trace_id,
            pipeline_type="sre_investigation",
            job_request_id=uuid.uuid4(),
            started_at=started_at,
        )

        # Then a UUID is returned
        assert isinstance(run_id, uuid.UUID)

        # And INSERT was executed
        mock_db.execute.assert_awaited_once()
        call_kwargs = mock_db.execute.call_args
        assert "INSERT INTO pipeline_runs" in call_kwargs.kwargs["query"]
        assert call_kwargs.kwargs["values"]["trace_id"] == trace_id


class TestCompletePipelineRun:
    @pytest.mark.asyncio
    async def test_updates_pipeline_run_status(self, mock_db: mock.AsyncMock) -> None:
        # Given a running pipeline
        run_id = uuid.uuid4()

        # When complete_pipeline_run is called
        await tracing.complete_pipeline_run(
            db=mock_db,
            run_id=run_id,
            status="completed",
            output_json={"root_cause": "memory leak"},
            duration_ms=5000,
        )

        # Then UPDATE was executed
        mock_db.execute.assert_awaited_once()
        call_kwargs = mock_db.execute.call_args
        assert "UPDATE pipeline_runs" in call_kwargs.kwargs["query"]
        assert call_kwargs.kwargs["values"]["status"] == "completed"


class TestPersistNodeExecution:
    @pytest.mark.asyncio
    async def test_inserts_node_execution(self, mock_db: mock.AsyncMock) -> None:
        # Given node execution details
        trace_id = uuid.uuid4()
        pipeline_run_id = uuid.uuid4()
        started_at = datetime(2026, 4, 4, 10, 1, tzinfo=UTC)

        # When persist_node_execution is called
        node_id = await tracing.persist_node_execution(
            db=mock_db,
            trace_id=trace_id,
            pipeline_run_id=pipeline_run_id,
            node_name="ClassifyAlert",
            node_order=1,
            started_at=started_at,
        )

        # Then a UUID is returned
        assert isinstance(node_id, uuid.UUID)

        # And INSERT was executed
        mock_db.execute.assert_awaited_once()
        call_kwargs = mock_db.execute.call_args
        assert "INSERT INTO node_executions" in call_kwargs.kwargs["query"]


class TestCompleteNodeExecution:
    @pytest.mark.asyncio
    async def test_updates_node_execution(self, mock_db: mock.AsyncMock) -> None:
        # Given a running node
        node_id = uuid.uuid4()

        # When complete_node_execution is called
        await tracing.complete_node_execution(
            db=mock_db,
            node_id=node_id,
            status="completed",
            output_json={"severity": "critical"},
            duration_ms=1200,
        )

        # Then UPDATE was executed
        mock_db.execute.assert_awaited_once()
        call_kwargs = mock_db.execute.call_args
        assert "UPDATE node_executions" in call_kwargs.kwargs["query"]


class TestPersistAgentCall:
    @pytest.mark.asyncio
    async def test_inserts_agent_call(self, mock_db: mock.AsyncMock) -> None:
        # Given agent call details
        trace_id = uuid.uuid4()
        node_execution_id = uuid.uuid4()
        started_at = datetime(2026, 4, 4, 10, 1, 5, tzinfo=UTC)

        # When persist_agent_call is called
        call_id = await tracing.persist_agent_call(
            db=mock_db,
            trace_id=trace_id,
            node_execution_id=node_execution_id,
            agent_name="alert_classifier",
            model_id="gpt-4.1-mini",
            messages_json=[{"role": "user", "content": "classify"}],
            token_usage_json={"input": 100, "output": 50},
            duration_ms=800,
            started_at=started_at,
            completed_at=datetime(2026, 4, 4, 10, 1, 6, tzinfo=UTC),
        )

        # Then a UUID is returned
        assert isinstance(call_id, uuid.UUID)

        # And INSERT was executed
        mock_db.execute.assert_awaited_once()
        call_kwargs = mock_db.execute.call_args
        assert "INSERT INTO agent_calls" in call_kwargs.kwargs["query"]
        assert call_kwargs.kwargs["values"]["agent_name"] == "alert_classifier"


class TestFetchPipelineRun:
    @pytest.mark.asyncio
    async def test_fetches_by_trace_id(self, mock_db: mock.AsyncMock) -> None:
        # Given a pipeline run exists
        trace_id = uuid.uuid4()
        mock_db.fetch_one.return_value = {
            "id": uuid.uuid4(),
            "trace_id": trace_id,
            "pipeline_type": "sre_investigation",
        }

        # When fetch_pipeline_run is called
        result = await tracing.fetch_pipeline_run(db=mock_db, trace_id=trace_id)

        # Then the run is returned
        assert result is not None
        assert result["trace_id"] == trace_id


class TestFetchNodeExecutions:
    @pytest.mark.asyncio
    async def test_fetches_by_pipeline_run_id(self, mock_db: mock.AsyncMock) -> None:
        # Given node executions exist
        pipeline_run_id = uuid.uuid4()
        mock_db.fetch_all.return_value = [
            {"id": uuid.uuid4(), "node_name": "ClassifyAlert", "node_order": 1},
            {"id": uuid.uuid4(), "node_name": "InvestigateWithHolmes", "node_order": 2},
        ]

        # When fetch_node_executions is called
        results = await tracing.fetch_node_executions(
            db=mock_db, pipeline_run_id=pipeline_run_id
        )

        # Then ordered results are returned
        assert len(results) == 2
```

- [x] **Step 2: Run test to verify it fails**

Run: `just test tests/unit/data/test_tracing.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Write minimal implementation**

```python
# src/sentinel/data/tracing.py
"""
Persist and fetch pipeline execution traces via the databases library.

Three-level hierarchy: pipeline_run → node_execution → agent_call.
All linked by trace_id for correlation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import databases


async def persist_pipeline_run(
    *,
    db: databases.Database,
    trace_id: uuid.UUID,
    pipeline_type: str,
    job_request_id: uuid.UUID | None = None,
    started_at: datetime,
    input_json: dict[str, Any] | None = None,
) -> uuid.UUID:
    """
    Insert a pipeline run record.

    :param db: The async database connection.
    :param trace_id: Correlation ID for this execution.
    :param pipeline_type: Pipeline name (e.g. "sre_investigation").
    :param job_request_id: Associated job request UUID (if any).
    :param started_at: When the pipeline started.
    :param input_json: Pipeline input data.
    :returns: The UUID of the inserted row.
    """
    run_id = uuid.uuid4()
    query = """
        INSERT INTO pipeline_runs (
            id, trace_id, pipeline_type, job_request_id, status,
            input_json, started_at
        ) VALUES (
            :id, :trace_id, :pipeline_type, :job_request_id, :status,
            :input_json, :started_at
        )
    """
    await db.execute(
        query=query,
        values={
            "id": run_id,
            "trace_id": trace_id,
            "pipeline_type": pipeline_type,
            "job_request_id": job_request_id,
            "status": "running",
            "input_json": input_json,
            "started_at": started_at,
        },
    )
    return run_id


async def complete_pipeline_run(
    *,
    db: databases.Database,
    run_id: uuid.UUID,
    status: str,
    output_json: dict[str, Any] | None = None,
    error_message: str | None = None,
    duration_ms: int | None = None,
) -> None:
    """
    Update a pipeline run with completion data.

    :param db: The async database connection.
    :param run_id: The pipeline run UUID.
    :param status: Final status ("completed" or "failed").
    :param output_json: Pipeline output data.
    :param error_message: Error description (if failed).
    :param duration_ms: Total execution duration.
    """
    query = """
        UPDATE pipeline_runs
        SET status = :status, output_json = :output_json,
            error_message = :error_message, completed_at = now(),
            duration_ms = :duration_ms
        WHERE id = :id
    """
    await db.execute(
        query=query,
        values={
            "id": run_id,
            "status": status,
            "output_json": output_json,
            "error_message": error_message,
            "duration_ms": duration_ms,
        },
    )


async def persist_node_execution(
    *,
    db: databases.Database,
    trace_id: uuid.UUID,
    pipeline_run_id: uuid.UUID,
    node_name: str,
    node_order: int,
    started_at: datetime,
    input_json: dict[str, Any] | None = None,
) -> uuid.UUID:
    """
    Insert a node execution record.

    :param db: The async database connection.
    :param trace_id: Correlation ID.
    :param pipeline_run_id: Parent pipeline run UUID.
    :param node_name: Graph node class name.
    :param node_order: Execution order within the pipeline.
    :param started_at: When the node started.
    :param input_json: Node input data.
    :returns: The UUID of the inserted row.
    """
    node_id = uuid.uuid4()
    query = """
        INSERT INTO node_executions (
            id, trace_id, pipeline_run_id, node_name, node_order,
            status, input_json, started_at
        ) VALUES (
            :id, :trace_id, :pipeline_run_id, :node_name, :node_order,
            :status, :input_json, :started_at
        )
    """
    await db.execute(
        query=query,
        values={
            "id": node_id,
            "trace_id": trace_id,
            "pipeline_run_id": pipeline_run_id,
            "node_name": node_name,
            "node_order": node_order,
            "status": "running",
            "input_json": input_json,
            "started_at": started_at,
        },
    )
    return node_id


async def complete_node_execution(
    *,
    db: databases.Database,
    node_id: uuid.UUID,
    status: str,
    output_json: dict[str, Any] | None = None,
    error_message: str | None = None,
    duration_ms: int | None = None,
) -> None:
    """
    Update a node execution with completion data.

    :param db: The async database connection.
    :param node_id: The node execution UUID.
    :param status: Final status ("completed" or "failed").
    :param output_json: Node output data.
    :param error_message: Error description (if failed).
    :param duration_ms: Node execution duration.
    """
    query = """
        UPDATE node_executions
        SET status = :status, output_json = :output_json,
            error_message = :error_message, completed_at = now(),
            duration_ms = :duration_ms
        WHERE id = :id
    """
    await db.execute(
        query=query,
        values={
            "id": node_id,
            "status": status,
            "output_json": output_json,
            "error_message": error_message,
            "duration_ms": duration_ms,
        },
    )


async def persist_agent_call(
    *,
    db: databases.Database,
    trace_id: uuid.UUID,
    node_execution_id: uuid.UUID,
    agent_name: str,
    model_id: str = "",
    messages_json: list[dict[str, Any]] | None = None,
    token_usage_json: dict[str, Any] | None = None,
    duration_ms: int | None = None,
    started_at: datetime,
    completed_at: datetime | None = None,
) -> uuid.UUID:
    """
    Insert an agent call record.

    :param db: The async database connection.
    :param trace_id: Correlation ID.
    :param node_execution_id: Parent node execution UUID.
    :param agent_name: PydanticAI agent name.
    :param model_id: LLM model identifier.
    :param messages_json: Agent message history.
    :param token_usage_json: Token usage breakdown.
    :param duration_ms: Agent call duration.
    :param started_at: When the agent call started.
    :param completed_at: When the agent call completed.
    :returns: The UUID of the inserted row.
    """
    call_id = uuid.uuid4()
    query = """
        INSERT INTO agent_calls (
            id, trace_id, node_execution_id, agent_name, model_id,
            messages_json, token_usage_json, duration_ms,
            started_at, completed_at
        ) VALUES (
            :id, :trace_id, :node_execution_id, :agent_name, :model_id,
            :messages_json, :token_usage_json, :duration_ms,
            :started_at, :completed_at
        )
    """
    await db.execute(
        query=query,
        values={
            "id": call_id,
            "trace_id": trace_id,
            "node_execution_id": node_execution_id,
            "agent_name": agent_name,
            "model_id": model_id,
            "messages_json": messages_json,
            "token_usage_json": token_usage_json,
            "duration_ms": duration_ms,
            "started_at": started_at,
            "completed_at": completed_at,
        },
    )
    return call_id


async def fetch_pipeline_run(
    *,
    db: databases.Database,
    trace_id: uuid.UUID,
) -> dict[str, Any] | None:
    """
    Fetch a pipeline run by its trace_id.

    :param db: The async database connection.
    :param trace_id: The correlation ID.
    :returns: Row dict or None if not found.
    """
    query = """
        SELECT id, trace_id, pipeline_type, job_request_id, status,
               input_json, output_json, error_message,
               started_at, completed_at, duration_ms, created_at
        FROM pipeline_runs
        WHERE trace_id = :trace_id
    """
    row = await db.fetch_one(query=query, values={"trace_id": trace_id})
    return dict(row) if row is not None else None


async def fetch_node_executions(
    *,
    db: databases.Database,
    pipeline_run_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """
    Fetch all node executions for a pipeline run, ordered by node_order.

    :param db: The async database connection.
    :param pipeline_run_id: The pipeline run UUID.
    :returns: List of row dicts ordered by node_order ascending.
    """
    query = """
        SELECT id, trace_id, pipeline_run_id, node_name, node_order,
               status, input_json, output_json, error_message,
               started_at, completed_at, duration_ms, created_at
        FROM node_executions
        WHERE pipeline_run_id = :pipeline_run_id
        ORDER BY node_order ASC
    """
    rows = await db.fetch_all(
        query=query, values={"pipeline_run_id": pipeline_run_id}
    )
    return [dict(row) for row in rows]


async def fetch_agent_calls(
    *,
    db: databases.Database,
    node_execution_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """
    Fetch all agent calls for a node execution.

    :param db: The async database connection.
    :param node_execution_id: The node execution UUID.
    :returns: List of row dicts ordered by started_at ascending.
    """
    query = """
        SELECT id, trace_id, node_execution_id, agent_name, model_id,
               messages_json, token_usage_json, duration_ms,
               started_at, completed_at, created_at
        FROM agent_calls
        WHERE node_execution_id = :node_execution_id
        ORDER BY started_at ASC
    """
    rows = await db.fetch_all(
        query=query, values={"node_execution_id": node_execution_id}
    )
    return [dict(row) for row in rows]
```

- [x] **Step 4: Run test to verify it passes**

Run: `just test tests/unit/data/test_tracing.py -v`
Expected: All tests PASS

- [x] **Step 5: Commit**

```bash
git add src/sentinel/data/tracing.py tests/unit/data/test_tracing.py
git commit -m "feat: add pipeline tracing persistence via databases library"
```

> **Completed:** commit `c7db7d5`. Also added `evaluation_models.py` and moved comparison/eval persistence to domain layer in commit `83447ac`.

---

### Task 8: ExecutionTracer Domain Class ✅

**Files:**
- Create: `src/sentinel/domain/pipeline/tracer.py`
- Modify: `src/sentinel/domain/pipeline/types.py`
- Test: `tests/unit/domain/pipeline/test_tracer.py`

> **Pattern alignment:** The `ExecutionTracer` delegates to `domain.pipeline.operations` (SQLAlchemy Core), not `data.tracing` (raw SQL). Tests use `mock.AsyncMock()` for the `db` parameter and `mock.patch.object()` for domain layer calls, matching the existing test conventions.

- [x] **Step 1: Write the failing test**

```python
# tests/unit/domain/pipeline/test_tracer.py
"""Tests for the ExecutionTracer that persists traces to the database."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest import mock

import pytest

from sentinel.domain.pipeline import tracer


class TestStartPipeline:
    @pytest.mark.asyncio
    async def test_sets_trace_id_and_pipeline_run_id(self) -> None:
        # Given an ExecutionTracer with a mock database
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None
        et = tracer.ExecutionTracer(db=mock_db)

        # When start_pipeline is called
        with mock.patch.object(
            tracer, "pipeline_ops"
        ) as mock_ops:
            mock_ops.persist_pipeline_run = mock.AsyncMock(return_value=uuid.uuid4())
            await et.start_pipeline(
                pipeline_type="sre_investigation",
                job_request_id=uuid.uuid4(),
                input_data={"alert_id": "alert-1"},
            )

        # Then trace_id and pipeline_run_id are set
        assert et.trace_id is not None
        assert et.pipeline_run_id is not None

    @pytest.mark.asyncio
    async def test_delegates_to_pipeline_operations(self) -> None:
        # Given an ExecutionTracer with a mock database
        mock_db = mock.AsyncMock()
        et = tracer.ExecutionTracer(db=mock_db)
        job_id = uuid.uuid4()

        # When start_pipeline is called
        with mock.patch.object(
            tracer, "pipeline_ops"
        ) as mock_ops:
            mock_ops.persist_pipeline_run = mock.AsyncMock(return_value=uuid.uuid4())
            await et.start_pipeline(
                pipeline_type="sre_investigation",
                job_request_id=job_id,
            )

        # Then persist_pipeline_run is called with correct kwargs
        mock_ops.persist_pipeline_run.assert_awaited_once()
        call_kwargs = mock_ops.persist_pipeline_run.call_args.kwargs
        assert call_kwargs["pipeline_type"] == "sre_investigation"
        assert call_kwargs["job_request_id"] == job_id

    @pytest.mark.asyncio
    async def test_graceful_degradation_without_db(self) -> None:
        # Given an ExecutionTracer without a database
        et = tracer.ExecutionTracer(db=None)

        # When start_pipeline is called
        await et.start_pipeline(pipeline_type="sre_investigation")

        # Then no error is raised and trace_id is still set
        assert et.trace_id is not None
        assert et.pipeline_run_id is not None


class TestCompletePipeline:
    @pytest.mark.asyncio
    async def test_delegates_to_pipeline_operations(self) -> None:
        # Given a started pipeline tracer
        mock_db = mock.AsyncMock()
        et = tracer.ExecutionTracer(db=mock_db)
        et._trace_id = uuid.uuid4()
        et._pipeline_run_id = uuid.uuid4()
        et._pipeline_started_at = datetime(2026, 4, 4, 10, 0, tzinfo=UTC)

        # When complete_pipeline is called
        with mock.patch.object(
            tracer, "pipeline_ops"
        ) as mock_ops:
            mock_ops.complete_pipeline_run = mock.AsyncMock()
            await et.complete_pipeline(
                status="completed",
                output_data={"root_cause": "leak"},
            )

        # Then complete_pipeline_run is called with correct status
        mock_ops.complete_pipeline_run.assert_awaited_once()
        assert mock_ops.complete_pipeline_run.call_args.kwargs["status"] == "completed"

    @pytest.mark.asyncio
    async def test_noop_when_db_is_none(self) -> None:
        # Given an ExecutionTracer without a database
        et = tracer.ExecutionTracer(db=None)
        et._trace_id = uuid.uuid4()

        # When complete_pipeline is called
        await et.complete_pipeline(status="completed")

        # Then no error is raised (noop)


class TestStartNode:
    @pytest.mark.asyncio
    async def test_returns_node_uuid(self) -> None:
        # Given a started pipeline tracer
        mock_db = mock.AsyncMock()
        et = tracer.ExecutionTracer(db=mock_db)
        et._trace_id = uuid.uuid4()
        et._pipeline_run_id = uuid.uuid4()

        # When start_node is called
        with mock.patch.object(
            tracer, "pipeline_ops"
        ) as mock_ops:
            expected_id = uuid.uuid4()
            mock_ops.persist_node_execution = mock.AsyncMock(return_value=expected_id)
            node_id = await et.start_node(node_name="ClassifyAlert")

        # Then a UUID is returned
        assert node_id == expected_id

    @pytest.mark.asyncio
    async def test_increments_node_order(self) -> None:
        # Given a started pipeline tracer
        mock_db = mock.AsyncMock()
        et = tracer.ExecutionTracer(db=mock_db)
        et._trace_id = uuid.uuid4()
        et._pipeline_run_id = uuid.uuid4()

        # When two nodes are started
        with mock.patch.object(
            tracer, "pipeline_ops"
        ) as mock_ops:
            mock_ops.persist_node_execution = mock.AsyncMock(side_effect=[uuid.uuid4(), uuid.uuid4()])
            await et.start_node(node_name="ClassifyAlert")
            await et.start_node(node_name="InvestigateWithHolmes")

        # Then node_order increments
        first_call = mock_ops.persist_node_execution.call_args_list[0].kwargs
        second_call = mock_ops.persist_node_execution.call_args_list[1].kwargs
        assert first_call["node_order"] == 1
        assert second_call["node_order"] == 2


class TestCompleteNode:
    @pytest.mark.asyncio
    async def test_delegates_to_pipeline_operations(self) -> None:
        # Given a started node
        mock_db = mock.AsyncMock()
        et = tracer.ExecutionTracer(db=mock_db)
        et._trace_id = uuid.uuid4()
        et._pipeline_run_id = uuid.uuid4()
        node_id = uuid.uuid4()
        et._node_started_at[node_id] = datetime(2026, 4, 4, 10, 1, tzinfo=UTC)

        # When complete_node is called
        with mock.patch.object(
            tracer, "pipeline_ops"
        ) as mock_ops:
            mock_ops.complete_node_execution = mock.AsyncMock()
            await et.complete_node(
                node_id=node_id,
                status="completed",
                output_data={"severity": "critical"},
            )

        # Then complete_node_execution is called
        mock_ops.complete_node_execution.assert_awaited_once()
        assert mock_ops.complete_node_execution.call_args.kwargs["status"] == "completed"


class TestRecordAgentCall:
    @pytest.mark.asyncio
    async def test_delegates_to_pipeline_operations(self) -> None:
        # Given a started node
        mock_db = mock.AsyncMock()
        et = tracer.ExecutionTracer(db=mock_db)
        et._trace_id = uuid.uuid4()
        node_id = uuid.uuid4()

        # When record_agent_call is called
        with mock.patch.object(
            tracer, "pipeline_ops"
        ) as mock_ops:
            mock_ops.persist_agent_call = mock.AsyncMock(return_value=uuid.uuid4())
            await et.record_agent_call(
                node_id=node_id,
                agent_name="alert_classifier",
                model_id="gpt-4.1-mini",
                messages=[],
                duration_ms=500,
            )

        # Then persist_agent_call is called with correct agent name
        mock_ops.persist_agent_call.assert_awaited_once()
        assert mock_ops.persist_agent_call.call_args.kwargs["agent_name"] == "alert_classifier"


class TestTraceCollectorBackwardCompat:
    def test_has_record_method(self) -> None:
        # Given an ExecutionTracer
        et = tracer.ExecutionTracer(db=None)

        # Then it satisfies the TraceCollector interface
        assert hasattr(et, "record")
        assert callable(et.record)

    def test_record_appends_to_traces_list(self) -> None:
        # Given an ExecutionTracer
        et = tracer.ExecutionTracer(db=None)

        # When record is called (TraceCollector interface)
        et.record(agent_name="test_agent", messages=[])

        # Then traces are accumulated
        assert len(et.traces) == 1
        assert et.traces[0].agent_name == "test_agent"
```

- [x] **Step 2: Run test to verify it fails**

Run: `just test tests/unit/domain/pipeline/test_tracer.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Write minimal implementation**

```python
# src/sentinel/domain/pipeline/tracer.py
"""
Database-backed execution tracer for pipeline runs.

Replaces the in-memory TraceCollector with persistent tracing while
maintaining backward compatibility (exposes the same .record() and
.traces interface).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic_ai.messages import ModelMessage

from sentinel.domain.pipeline import operations as pipeline_ops
from sentinel.domain.pipeline import types


if TYPE_CHECKING:
    import databases


class ExecutionTracer:
    """
    Record pipeline execution traces to the database.

    Also satisfies the TraceCollector interface for backward compatibility
    with the Streamlit chat UI.

    When db is None, tracing calls are no-ops but trace_id is still
    generated for correlation.
    """

    def __init__(self, *, db: databases.Database | None) -> None:
        self._db = db
        self._trace_id: uuid.UUID | None = None
        self._pipeline_run_id: uuid.UUID | None = None
        self._pipeline_started_at: datetime | None = None
        self._node_order: int = 0
        self._node_started_at: dict[uuid.UUID, datetime] = {}

        # TraceCollector backward compatibility
        self.traces: list[types.AgentTrace] = []

    @property
    def trace_id(self) -> uuid.UUID | None:
        """Return the current trace correlation ID."""
        return self._trace_id

    @property
    def pipeline_run_id(self) -> uuid.UUID | None:
        """Return the current pipeline run ID."""
        return self._pipeline_run_id

    def record(self, *, agent_name: str, messages: list[ModelMessage]) -> None:
        """
        Record an agent trace (TraceCollector interface).

        Accumulates traces in-memory for Streamlit UI.
        """
        self.traces.append(types.AgentTrace(agent_name=agent_name, messages=messages))

    async def start_pipeline(
        self,
        *,
        pipeline_type: str,
        job_request_id: uuid.UUID | None = None,
        input_data: dict[str, Any] | None = None,
    ) -> None:
        """
        Record the start of a pipeline execution.

        :param pipeline_type: Pipeline name (e.g. "sre_investigation").
        :param job_request_id: Associated job request UUID.
        :param input_data: Pipeline input data.
        """
        self._trace_id = uuid.uuid4()
        self._pipeline_started_at = datetime.now(tz=UTC)
        self._node_order = 0

        if self._db is None:
            self._pipeline_run_id = uuid.uuid4()
            return

        self._pipeline_run_id = await pipeline_ops.persist_pipeline_run(
            db=self._db,
            trace_id=self._trace_id,
            pipeline_type=pipeline_type,
            job_request_id=job_request_id,
            started_at=self._pipeline_started_at,
            input_json=input_data,
        )

    async def complete_pipeline(
        self,
        *,
        status: str,
        output_data: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        """
        Record the completion of a pipeline execution.

        :param status: Final status ("completed" or "failed").
        :param output_data: Pipeline output data.
        :param error_message: Error message if failed.
        """
        if self._db is None or self._pipeline_run_id is None:
            return

        duration_ms = None
        if self._pipeline_started_at:
            delta = datetime.now(tz=UTC) - self._pipeline_started_at
            duration_ms = int(delta.total_seconds() * 1000)

        await pipeline_ops.complete_pipeline_run(
            db=self._db,
            run_id=self._pipeline_run_id,
            status=status,
            output_json=output_data,
            error_message=error_message,
            duration_ms=duration_ms,
        )

    async def start_node(
        self,
        *,
        node_name: str,
        input_data: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        """
        Record the start of a graph node execution.

        :param node_name: Name of the graph node class.
        :param input_data: Node input data.
        :returns: The node execution UUID.
        """
        self._node_order += 1
        now = datetime.now(tz=UTC)

        if self._db is None or self._pipeline_run_id is None:
            node_id = uuid.uuid4()
            self._node_started_at[node_id] = now
            return node_id

        node_id = await pipeline_ops.persist_node_execution(
            db=self._db,
            trace_id=self._trace_id,  # type: ignore[arg-type]
            pipeline_run_id=self._pipeline_run_id,
            node_name=node_name,
            node_order=self._node_order,
            started_at=now,
            input_json=input_data,
        )
        self._node_started_at[node_id] = now
        return node_id

    async def complete_node(
        self,
        *,
        node_id: uuid.UUID,
        status: str,
        output_data: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        """
        Record the completion of a graph node execution.

        :param node_id: The node execution UUID from start_node().
        :param status: Final status ("completed" or "failed").
        :param output_data: Node output data.
        :param error_message: Error message if failed.
        """
        if self._db is None:
            return

        duration_ms = None
        started_at = self._node_started_at.pop(node_id, None)
        if started_at:
            delta = datetime.now(tz=UTC) - started_at
            duration_ms = int(delta.total_seconds() * 1000)

        await pipeline_ops.complete_node_execution(
            db=self._db,
            node_id=node_id,
            status=status,
            output_json=output_data,
            error_message=error_message,
            duration_ms=duration_ms,
        )

    async def record_agent_call(
        self,
        *,
        node_id: uuid.UUID,
        agent_name: str,
        model_id: str = "",
        messages: list[ModelMessage] | None = None,
        token_usage: dict[str, Any] | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """
        Record a PydanticAI agent invocation.

        :param node_id: Parent node execution UUID.
        :param agent_name: Agent name.
        :param model_id: LLM model identifier.
        :param messages: Agent message history.
        :param token_usage: Token usage breakdown.
        :param duration_ms: Call duration in milliseconds.
        """
        if self._db is None or self._trace_id is None:
            return

        now = datetime.now(tz=UTC)

        # Serialise messages to JSON-safe format
        messages_json = None
        if messages:
            messages_json = [
                {"role": getattr(m, "role", "unknown"), "parts": str(m.parts)}
                for m in messages
            ]

        await pipeline_ops.persist_agent_call(
            db=self._db,
            trace_id=self._trace_id,
            node_execution_id=node_id,
            agent_name=agent_name,
            model_id=model_id,
            messages_json=messages_json,
            token_usage_json=token_usage,
            duration_ms=duration_ms,
            started_at=now,
            completed_at=now,
        )
```

- [x] **Step 4: Run test to verify it passes**

Run: `just test tests/unit/domain/pipeline/test_tracer.py -v`
Expected: All tests PASS

- [x] **Step 5: Commit**

```bash
git add src/sentinel/domain/pipeline/tracer.py tests/unit/domain/pipeline/test_tracer.py
git commit -m "feat: add ExecutionTracer for database-backed pipeline tracing"
```

---

### Task 9: Wire Database Singleton into Entry Points ✅

**Files:**
- Modify: `src/sentinel/interfaces/api/app.py`
- Modify: `src/sentinel/worker.py`
- Modify: `src/sentinel/interfaces/mcp/server.py`
- Modify: `src/sentinel/interfaces/mcp/tools/investigation.py`

- [x] **Step 1: Update `app.py` lifespan to connect/disconnect `databases.Database`**

In `src/sentinel/interfaces/api/app.py`, change the lifespan to also manage the `databases.Database` lifecycle:

```python
# Add import at top:
from sentinel.data import db as async_db

# Replace lifespan body:
@asynccontextmanager
async def lifespan(app: fastapi.FastAPI) -> AsyncGenerator[None]:
    bootstrap.initialise()

    if get_settings().database_url:
        database.get_engine()
        await async_db.connect_db()
        logs.log_event("database_engine_initialised")

    yield

    if get_settings().database_url:
        await async_db.disconnect_db()
    await database.close_engine()
    logs.log_event("database_engine_closed")
```

- [x] **Step 2: Update `worker.py` to connect/disconnect `databases.Database`**

In `src/sentinel/worker.py`, add the `databases.Database` lifecycle alongside the SQLAlchemy engine:

```python
# Add import at top:
from sentinel.data import db as async_db

# In _main(), after database.get_engine():
async def _main() -> None:
    bootstrap.initialise()
    args = _parse_args()

    worker_id = os.environ.get("HOSTNAME", f"worker-{os.getpid()}")

    if get_settings().database_url:
        database.get_engine()
        await async_db.connect_db()
        logs.log_event("worker.database_initialised")

    try:
        if args.run_once:
            await _run_once(worker_id=worker_id)
        else:
            await _poll_loop(worker_id=worker_id)
    finally:
        if get_settings().database_url:
            await async_db.disconnect_db()
        await database.close_engine()
```

- [x] **Step 3: Update MCP server to use `get_db()` from the singleton**

In `src/sentinel/interfaces/mcp/server.py`, replace manual `_db` state with the singleton:

```python
# Remove: _db: databases.Database | None = None
# Remove: db parameter from configure()
# Remove: databases import

# Add import:
from sentinel.data import db as async_db

# In tool functions that need db, replace _db with:
@mcp.tool()
async def trigger_investigation(alert_source: str, alert_id: str, description: str = "") -> str:
    try:
        db = async_db.get_db()
    except RuntimeError:
        db = None
    return await inv_tools.trigger_investigation(
        db=db, alert_source=alert_source, alert_id=alert_id, description=description,
    )
```

- [x] **Step 4: Update MCP `investigation.py` to use `data.jobs` instead of inline SQL**

In `src/sentinel/interfaces/mcp/tools/investigation.py`, replace inline raw SQL with calls to `data.jobs`:

```python
# Replace trigger_investigation body:
async def trigger_investigation(
    *,
    db: databases.Database | None,
    alert_source: str,
    alert_id: str,
    description: str = "",
) -> str:
    if db is None:
        return "Database not available. Cannot enqueue investigation."

    try:
        from sentinel.data import jobs

        job_id = await jobs.enqueue_job(
            db=db,
            job_type="sre_investigation",
            payload={
                "alert_source": alert_source,
                "alert_id": alert_id,
                "description": description,
            },
            requested_by=f"mcp:{alert_source}",
            source_id=alert_id,
        )
    except Exception as exc:
        logs.log_exception(exc, params={"tool": "trigger_investigation", "alert_id": alert_id})
        return f"Failed to enqueue investigation: {type(exc).__name__}"

    return f"Investigation triggered. job_id={job_id} alert={alert_source}/{alert_id}"
```

- [x] **Step 5: Run existing tests to verify nothing is broken**

Run: `just test -v`
Expected: All existing tests PASS

- [x] **Step 6: Commit**

```bash
git add src/sentinel/interfaces/api/app.py src/sentinel/worker.py src/sentinel/interfaces/mcp/server.py src/sentinel/interfaces/mcp/tools/investigation.py
git commit -m "feat: wire databases.Database singleton into API, worker, and MCP entry points"
```

---

### Task 10: Wire ExecutionTracer into Worker and Pipelines ✅

**Files:**
- Modify: `src/sentinel/worker.py`
- Modify: `src/sentinel/interfaces/graphs/sre_investigation.py`
- Modify: `src/sentinel/interfaces/graphs/support_review.py`

This task wires the `ExecutionTracer` into the worker's job dispatch and passes it through to the graph pipelines. The graphs already accept `trace_collector` — we pass `ExecutionTracer` instead (it satisfies the same interface).

> **Pattern alignment:** Uses `_get_optional_db()` helper (already extracted in `server.py` — extract a shared version or inline in worker). Imports domain layer modules (`domain.sre.operations`, `domain.support.operations`), not data layer. Import modules not objects per AGENT.md.

- [x] **Step 1: Update `_run_sre_investigation` in `worker.py`**

Replace the `_persist` closure with domain-layer persistence and add `ExecutionTracer`:

```python
# Replace old imports:
#   from sentinel.application.sre import persist as sre_persist
#   from sentinel.application.support import persist as support_persist
# With:
from sentinel.domain.sre import operations as sre_ops
from sentinel.domain.support import operations as support_ops
from sentinel.domain.pipeline import tracer as pipeline_tracer

# Note: `from sentinel.data import db as async_db` is already imported (Task 9).

# Add helper (matches server.py pattern):
def _get_optional_db() -> databases.Database | None:
    """Return the database connection, or None if not configured."""
    try:
        return async_db.get_db()
    except RuntimeError:
        return None

# Replace _run_sre_investigation:
async def _run_sre_investigation(payload: dict[str, object]) -> str:
    alert = sre_entities.Alert.model_validate(payload)
    cfg = get_config()
    holmes = cfg.build_holmes_adapter()
    pd_client = cfg.pagerduty_client if get_settings().pagerduty_api_key else None

    db = _get_optional_db()
    et = pipeline_tracer.ExecutionTracer(db=db)

    async def _persist(reply: common.InvestigationReply) -> None:
        if db is None:
            return
        await sre_ops.persist_investigation(
            db=db,
            alert_source=str(payload.get("source", "webhook")),
            alert_id=reply.alert_id,
            alert_title=str(payload.get("title", reply.alert_id)),
            severity=str(payload.get("severity", "unknown")),
            service=str(payload.get("service", "unknown")),
            root_cause=reply.root_cause,
            remediation=reply.remediation,
            confidence_score=reply.confidence.total if reply.confidence else None,
            findings_json={"summary": reply.findings_summary},
            trace_id=et.trace_id,
        )

    result = await sre_investigation.investigate_alert(
        alert=alert,
        holmes=holmes,
        pagerduty_client=pd_client,
        persist_fn=_persist,
        trace_collector=et,
    )

    return result.model_dump_json()
```

- [x] **Step 2: Update `_run_support_review` in `worker.py`**

```python
# Replace _run_support_review:
async def _run_support_review(payload: dict[str, object]) -> str:
    ticket = support_entities.Ticket.model_validate(payload)
    cfg = get_config()

    db = _get_optional_db()
    et = pipeline_tracer.ExecutionTracer(db=db)

    async def _persist(reply: common.SupportReply) -> None:
        if db is None:
            return
        await support_ops.persist_ticket_review(
            db=db,
            ticket_id=reply.ticket_id,
            ticket_key=reply.ticket_key,
            suggested_response=reply.suggested_response,
            sources_json={"sources": reply.sources} if reply.sources else None,
            confidence_score=reply.confidence.total if reply.confidence else None,
            category=reply.category,
            trace_id=et.trace_id,
        )

    result = await support_review.review_ticket(
        ticket=ticket,
        document_searcher=cfg.build_document_searcher(),
        ticket_searcher=cfg.build_ticket_searcher(),
        persist_fn=_persist,
        trace_collector=et,
    )

    return result.model_dump_json()
```

- [x] **Step 3: Run existing tests to verify backward compatibility**

Run: `just test -v`
Expected: All tests PASS — the `ExecutionTracer` satisfies the `TraceCollector` interface (has `.record()` and `.traces`).

- [x] **Step 4: Commit**

```bash
git add src/sentinel/worker.py
git commit -m "feat: wire ExecutionTracer into worker job dispatch"
```

---

### Task 11: Add trace_id to SQLModel Models (for Alembic metadata) ✅

**Files:**
- Modify: `src/sentinel/data/models.py`
- Modify: `src/sentinel/data/job_models.py`

The Alembic autogenerate reads SQLModel metadata. We need the models to match the migration (003). The `trace_id` column was added to the database by migration 003 but the SQLModel classes don't yet declare it — they must match for future `alembic revision --autogenerate` to work correctly.

> **Pattern alignment:** Follow existing `Field` conventions in `models.py`: nullable UUID with `index=True`, placed after the last timestamp field. Use `uuid.UUID` type annotation matching `tracing_models.py` pattern.

- [x] **Step 1: Add trace_id to InvestigationRecord and TicketReviewRecord**

In `src/sentinel/data/models.py`, add after the last field in each class:

```python
# InvestigationRecord — add after created_at field:
    trace_id: uuid.UUID | None = Field(default=None, index=True)

# TicketReviewRecord — add after reviewed_at field:
    trace_id: uuid.UUID | None = Field(default=None, index=True)
```

- [x] **Step 2: Add trace_id to JobRequestRecord**

In `src/sentinel/data/job_models.py`, add after `created_at` in `JobRequestRecord`:

```python
    trace_id: uuid.UUID | None = Field(default=None, index=True)
```

- [x] **Step 3: Run existing tests**

Run: `just test -v`
Expected: All tests PASS — new nullable fields with defaults don't break existing code.

- [x] **Step 4: Commit**

```bash
git add src/sentinel/data/models.py src/sentinel/data/job_models.py
git commit -m "feat: add trace_id column to SQLModel investigation, ticket, and job models"
```

---

### Task 12: Cleanup — Remove Old SQLAlchemy Persistence Modules

**Files:**
- Modify: `src/sentinel/worker.py` (remove old imports)
- Modify: any callers of old persist modules
- Delete: `src/sentinel/application/sre/persist.py`
- Delete: `src/sentinel/application/support/persist.py`
- Delete: `src/sentinel/application/audit/persist.py`
- Delete: `src/sentinel/application/jobs/enqueue.py`
- Delete: `src/sentinel/application/jobs/dequeue.py`

**Important:** Before deleting, search the codebase for all imports of these modules and update them.

> **Pattern alignment:** All replacements must use domain layer imports (import modules not objects per AGENT.md). The new modules use `db: databases.Database` as their first kwarg instead of `session: AsyncSession`. Use `_get_optional_db()` helper where the database may not be configured.

- [ ] **Step 1: Search for all imports of old persistence modules**

Use Grep tool (not bash `rg`) to find all imports:

```
# Search patterns:
"from sentinel.application.sre import persist"
"from sentinel.application.support import persist"
"from sentinel.application.audit import persist"
"from sentinel.application.jobs import dequeue"
"from sentinel.application.jobs import enqueue"
```

Document every file that imports these modules.

- [ ] **Step 2: Update each caller to use the new domain layer modules**

For each file found in Step 1:
- Replace `from sentinel.application.sre import persist` → `from sentinel.domain.sre import operations as sre_ops`
- Replace `from sentinel.application.support import persist` → `from sentinel.domain.support import operations as support_ops`
- Replace `from sentinel.application.audit import persist` → `from sentinel.domain.audit import operations as audit_ops`
- Replace `from sentinel.application.jobs import dequeue` → `from sentinel.domain.jobs import operations as job_ops` + `from sentinel.domain.jobs import queries as job_queries`
- Replace `from sentinel.application.jobs import enqueue` → `from sentinel.domain.jobs import operations as job_ops`
- Update function call signatures:
  - `session` parameter → `db` parameter
  - `database.get_session()` context manager → `_get_optional_db()` or `async_db.get_db()`
  - `persist.save_investigation(session, ...)` → `sre_ops.persist_investigation(db=db, ...)`
  - `dequeue.fetch_job_record(session, ...)` → `job_queries.fetch_job(db=db, ...)`
  - `dequeue.complete_job(session, ...)` → `job_ops.complete_job(db=db, ...)`

- [ ] **Step 3: Update test imports**

Search `tests/` for old imports and update them to match new module paths.

Use Grep tool to search:
```
"application.sre.persist" in tests/
"application.support.persist" in tests/
"application.audit.persist" in tests/
"application.jobs" in tests/
```

Update mock targets to patch the domain layer modules instead.

- [ ] **Step 4: Run full test suite**

Run: `just test -v`
Expected: All tests PASS with new imports

- [ ] **Step 5: Delete old persistence modules**

```bash
rm src/sentinel/application/sre/persist.py
rm src/sentinel/application/support/persist.py
rm src/sentinel/application/audit/persist.py
rm src/sentinel/application/jobs/enqueue.py
rm src/sentinel/application/jobs/dequeue.py
```

- [ ] **Step 6: Run full test suite and lint**

Run: `just test -v && just lint`
Expected: All tests PASS, no lint errors (no dangling imports, import-linter contracts satisfied)

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: remove old SQLAlchemy persistence modules in favour of domain layer"
```

---

### Task 13: Final Verification and Lint

- [ ] **Step 1: Run full test suite**

Run: `just test -v`
Expected: All tests PASS (475+ tests)

- [ ] **Step 2: Run lint**

Run: `just lint`
Expected: No errors (ruff + mypy + import-linter contracts all pass). In particular, verify import-linter contracts are satisfied — the domain layer must not import from interfaces or application layers.

- [ ] **Step 3: Verify migration applies cleanly (if DB available)**

Run: `just run-db-migrations`
Expected: Migrations 002a and 003 apply without errors. Tables `investigation_records`, `ticket_review_records`, `pipeline_runs`, `node_executions`, `agent_calls` all exist with `trace_id` columns.

- [ ] **Step 4: Verify trace_id correlation works end-to-end**

Check that the worker creates an `ExecutionTracer`, propagates `trace_id` to investigation/ticket records, and pipeline_runs/node_executions are written. This can be verified by running a functional test or manual inspection. Key verification points:

- `ExecutionTracer.trace_id` is a UUID set during `start_pipeline()`
- The same `trace_id` appears in:
  - `pipeline_runs.trace_id`
  - `node_executions.trace_id`
  - `agent_calls.trace_id`
  - `investigation_records.trace_id` (via persist callback)
  - `ticket_review_records.trace_id` (via persist callback)
- `TraceCollector` backward compat: `et.record()` still appends to `et.traces` for Streamlit UI

- [ ] **Step 5: Commit any remaining fixes**

```bash
git add -A
git commit -m "chore: final cleanup for database traceability refactor"
```
