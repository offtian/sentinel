"""
Integration test: F7 runbook-scoped toolset adversarial slice.

Two adversarial scenarios exercise RunbookScopedToolset end-to-end against
a live Postgres database, verifying that:

  A. A cross-tenant namespace call — k8s_get_pod_logs(namespace="other-pm")
     while envelope tenant_id="pm-a" — raises TenantScopeViolationError AND
     writes an audit_log row with rejection_kind="TenantScopeViolationError".

  B. A tool absent from the runbook allow-list — prom_query_range against a
     runbook that only permits k8s_* tools — raises ToolNotInRunbookError AND
     writes an audit_log row with rejection_kind="ToolNotInRunbookError".

Requires a live Postgres with ``just run-db-migrations`` applied. Skips
cleanly when the DB is unreachable or the audit_log table is absent.
"""

from __future__ import annotations

import functools
import json
import types
import uuid
from typing import Any

import databases
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from sentinel.data import _dsn, database
from sentinel.data.sql import audit
from sentinel.domain.audit import operations as audit_ops
from sentinel.domain.tools import grants as grants_mod
from sentinel.plugins.toolsets import _runbook_scope as scope_mod
from sentinel.settings import settings
from tests import factories


_REQUIRED_TABLE = "audit_log"


@pytest.fixture(scope="module")
async def _db_or_skip():
    """Probe the live DB or skip the module cleanly."""
    try:
        async with database.get_session() as session:
            await session.execute(text("SELECT 1"))
            row = await session.execute(
                text("SELECT to_regclass(:tbl)"),
                {"tbl": _REQUIRED_TABLE},
            )
            if row.scalar() is None:
                pytest.skip(
                    f"DB schema missing table {_REQUIRED_TABLE!r}; "
                    "run `just run-db-migrations` first."
                )
    except DBAPIError as exc:
        pytest.skip(f"DB not reachable for F7 integration test: {exc!r}")
    except Exception as exc:
        pytest.skip(f"DB not reachable for F7 integration test: {exc!r}")


@pytest.fixture(scope="module")
async def live_db(_db_or_skip: None) -> databases.Database:
    """Return a connected databases.Database instance for audit_fn injection."""
    url = settings.database_url
    if not url:
        pytest.skip("DATABASE_URL not configured — cannot bind audit_fn")
    db = databases.Database(_dsn.to_libpq(url))
    await db.connect()
    yield db
    await db.disconnect()


class _StubToolset:
    """Minimal toolset stub — returns a canned result; never reached in rejection tests."""

    async def call_tool(self, name: str, tool_args: dict[str, Any], ctx: Any, tool: Any) -> Any:
        return f"result-of-{name}"

    async def get_tools(self, ctx: Any) -> dict[str, Any]:
        return {}

    @property
    def id(self) -> str | None:
        return "stub"


def _make_ctx(*, runbook: Any, tenant_id: str) -> types.SimpleNamespace:
    envelope = factories.make_envelope(tenant_id=tenant_id)
    deps = types.SimpleNamespace(
        runbook=runbook,
        envelope=envelope,
        _tool_call_counters={},
    )
    return types.SimpleNamespace(deps=deps)


@pytest.mark.usefixtures("_db_or_skip")
class TestTenantIsolation:
    """F7 adversarial slice: RunbookScopedToolset raises and writes audit rows."""

    async def test_cross_tenant_namespace_raises_and_writes_audit_row(
        self, live_db: databases.Database
    ) -> None:
        # Given a runbook that allows k8s_get_pod_logs for tenant "pm-a", a
        # live audit_fn backed by the connected database, and a wrapper
        # configured with a unique runbook_id to isolate this test's audit row
        runbook_id = f"f7-tenant-{uuid.uuid4().hex[:8]}"
        runbook = factories.make_runbook(
            runbook_id=runbook_id,
            allowed_tools=("k8s_get_pod_logs",),
        )
        ctx = _make_ctx(runbook=runbook, tenant_id="pm-a")
        audit_fn = functools.partial(audit_ops.record_audit_entry, db=live_db)
        wrapper = scope_mod.RunbookScopedToolset(
            _StubToolset(), label="f7-tenant-test", audit_fn=audit_fn
        )

        # When the wrapper intercepts k8s_get_pod_logs with a cross-tenant
        # namespace ("other-pm" ≠ envelope tenant_id "pm-a")
        with pytest.raises(grants_mod.TenantScopeViolationError):
            await wrapper.call_tool(
                "k8s_get_pod_logs",
                {"namespace": "other-pm"},
                ctx,
                None,
            )

        # Then an audit_log row with rejection_kind="TenantScopeViolationError"
        # was durably written, carrying the tenant_id and cross-tenant namespace
        async with database.get_session() as session:
            stmt = select(audit.AuditLogRecord).where(
                audit.AuditLogRecord.actor == "tool_runtime",
                audit.AuditLogRecord.action == "tool_call_rejected",
                audit.AuditLogRecord.resource_id == "k8s_get_pod_logs",
            )
            rows = (await session.execute(stmt)).scalars().all()

        matching = [r for r in rows if json.loads(r.details_json).get("runbook_id") == runbook_id]
        assert len(matching) == 1, (
            f"Expected 1 audit row for runbook_id={runbook_id!r}, found {len(matching)}"
        )
        details = json.loads(matching[0].details_json)
        assert details["rejection_kind"] == "TenantScopeViolationError"
        assert details["tenant_id"] == "pm-a"
        assert details["call_namespace"] == "other-pm"

    async def test_tool_not_in_runbook_raises_and_writes_audit_row(
        self, live_db: databases.Database
    ) -> None:
        # Given a runbook that only permits k8s_* tools, a live audit_fn backed
        # by the connected database, and a wrapper configured with a unique
        # runbook_id to isolate this test's audit row
        runbook_id = f"f7-scope-{uuid.uuid4().hex[:8]}"
        runbook = factories.make_runbook(
            runbook_id=runbook_id,
            allowed_tools=("k8s_get_pod_logs", "k8s_get_events"),
        )
        ctx = _make_ctx(runbook=runbook, tenant_id="pm-a")
        audit_fn = functools.partial(audit_ops.record_audit_entry, db=live_db)
        wrapper = scope_mod.RunbookScopedToolset(
            _StubToolset(), label="f7-scope-test", audit_fn=audit_fn
        )

        # When the wrapper intercepts a call to prom_query_range, which is
        # absent from the runbook's allow-list
        with pytest.raises(grants_mod.ToolNotInRunbookError):
            await wrapper.call_tool(
                "prom_query_range",
                {"query": "up", "start": "now-1h", "end": "now"},
                ctx,
                None,
            )

        # Then an audit_log row with rejection_kind="ToolNotInRunbookError"
        # was durably written, carrying the tool name and tenant context
        async with database.get_session() as session:
            stmt = select(audit.AuditLogRecord).where(
                audit.AuditLogRecord.actor == "tool_runtime",
                audit.AuditLogRecord.action == "tool_call_rejected",
                audit.AuditLogRecord.resource_id == "prom_query_range",
            )
            rows = (await session.execute(stmt)).scalars().all()

        matching = [r for r in rows if json.loads(r.details_json).get("runbook_id") == runbook_id]
        assert len(matching) == 1, (
            f"Expected 1 audit row for runbook_id={runbook_id!r}, found {len(matching)}"
        )
        details = json.loads(matching[0].details_json)
        assert details["rejection_kind"] == "ToolNotInRunbookError"
        assert details["tenant_id"] == "pm-a"
