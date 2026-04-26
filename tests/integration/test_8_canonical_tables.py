"""
Integration test: full RFC 12.3 canonical-table chain with WORM enforcement.

Closes F3.11 of the hedge-fund foundations plan. Writes one row to each of
the nine canonical tables in dependency order, asserts FK integrity by
reading the chain back, then exercises the audit_log WORM trigger to
confirm UPDATE and DELETE both raise.

The plan describes "8 canonical tables" — that count groups
``investigation_task`` + ``task_status_change`` (RFC 12.3.7) and
``quality_verdict`` + ``approval_record`` (RFC 12.3.8) as paired tables.
This test exercises every table the F3 schema slice touched, which is nine
rows in dependency order:

    1. alert_request                  (F3.2 — root envelope)
    2. runbook_match                  (F3.3 — FK alert_request)
    3. investigation_records          (F3.7 — FK alert_request, runbook_match;
                                       findings stored as JSONB on this row,
                                       no dedicated finding table in the
                                       foundations slice)
    4. agent_calls (= tool_call)      (F3.8 — extension columns in use)
    5. investigation_task             (F3.4 — FK investigation_records)
    6. task_status_change             (F3.4 — FK investigation_task)
    7. quality_verdict                (F3.5 — FK investigation_records)
    8. approval_record                (F3.5 — FK quality_verdict)
    9. audit_log                      (F3.6 — WORM-protected, no FK)

The WORM trigger only fires server-side, so this test must run against a
real Postgres. The contract is that the developer ran
``just run-db-migrations`` once before invoking ``just test-integration``.
The module-scope fixture probes the connection and the presence of the
canonical tables; if either check fails, the whole module is skipped
cleanly (no false failures on environments without a live DB).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel.data import database
from sentinel.data.sql import (
    alert_requests,
    audit,
    quality,
    runbooks,
    tasks,
    tracing,
)
from sentinel.data.sql import (
    investigations as investigations_module,
)


_TENANT_ID = "pm-foundations-f3-test"
_REQUIRED_TABLES = (
    "alert_request",
    "runbook_match",
    "investigation_records",
    "agent_calls",
    "investigation_task",
    "task_status_change",
    "quality_verdict",
    "approval_record",
    "audit_log",
)


@pytest.fixture(scope="module")
async def _db_or_skip():
    """
    Probe the live DB or skip the module cleanly.

    Two probes are required because either failure mode (no DB / schema
    not at head) should skip rather than fail. ``SELECT 1`` checks the
    connection; ``to_regclass`` per canonical table checks the schema is
    at or beyond F3.10.
    """
    try:
        async with database.get_session() as session:
            await session.execute(text("SELECT 1"))
            for table_name in _REQUIRED_TABLES:
                row = await session.execute(
                    text("SELECT to_regclass(:tbl)"),
                    {"tbl": table_name},
                )
                if row.scalar() is None:
                    pytest.skip(
                        f"DB schema not at F3 head for F3.11: missing table {table_name!r}; "
                        "run `just run-db-migrations` first."
                    )
    except DBAPIError as exc:
        pytest.skip(f"DB not reachable for F3.11 integration test: {exc!r}")
    except Exception as exc:
        pytest.skip(f"DB not reachable for F3.11 integration test: {exc!r}")


async def _insert_canonical_chain(
    *, session: AsyncSession, request_id: uuid.UUID
) -> dict[str, object]:
    """
    Insert one row into each F3 canonical table in FK-dependency order.

    Returns a dict of the inserted ORM rows so the caller can read each
    row back through its FK relationship after commit. The audit_log row
    is committed inside this helper because the BEFORE INSERT trigger
    that populates ``row_hash`` is observed via an explicit SELECT after
    the row is durable.
    """
    # 1. alert_request (root envelope row)
    alert_row = alert_requests.AlertRequestRecord(
        request_id=request_id,
        tenant_id=_TENANT_ID,
        provider="pagerduty",
        alert_id=f"P-F3-{request_id.hex[:8]}",
        severity="critical",
        dedup_status="new",
    )
    session.add(alert_row)
    await session.flush()

    # 2. runbook_match (FK -> alert_request.request_id)
    runbook_match = runbooks.RunbookMatchRecord(
        request_id=request_id,
        runbook_id="rb-foundations-test",
        runbook_version_sha="a" * 32,
        match_method="tag",
        match_confidence=0.95,
    )
    session.add(runbook_match)
    await session.flush()

    # 3. investigation_records (F3.7 columns in use; findings ride on the
    # row as a JSONB column — no dedicated finding table in foundations).
    investigation_row = investigations_module.InvestigationRecord(
        alert_source="pagerduty",
        alert_id=alert_row.alert_id,
        alert_title="F3.11 synthetic chain",
        severity="critical",
        service="foundations-test-svc",
        status="completed",
        findings_json={
            "findings": [
                {
                    "text": "Synthetic finding for F3.11",
                    "evidence_refs": ["s3://bucket/object/1"],
                }
            ]
        },
        request_id=request_id,
        runbook_match_id=runbook_match.match_id,
        model_id_primary="openai/gpt-4.1",
        iteration_count=3,
        terminated_reason=None,
        loop_cap_hit=False,
    )
    session.add(investigation_row)
    await session.flush()

    # 4. agent_calls (= tool_call in the F3.8 extension shape)
    tool_call = tracing.AgentCallRecord(
        trace_id=uuid.uuid4(),
        node_execution_id=uuid.uuid4(),
        agent_name="k8s-investigator",
        model_id="openai/gpt-4.1",
        started_at=datetime.now(tz=UTC),
        tool_name="k8s_describe_pod",
        capability_token=f"cap:k8s_describe_pod:rb-foundations-test:{_TENANT_ID}",
        evidence_object_ids=["s3://bucket/object/1"],
        succeeded=True,
        tenant_id=_TENANT_ID,
    )
    session.add(tool_call)
    await session.flush()

    # 5. investigation_task (FK -> investigation_records.id)
    task_row = tasks.InvestigationTaskRecord(
        investigation_id=investigation_row.id,
        task_text="Confirm crashloop root cause",
        evidence_refs={"refs": ["s3://bucket/object/1"]},
    )
    session.add(task_row)
    await session.flush()

    # 6. task_status_change (FK -> investigation_task.task_id)
    status_change = tasks.TaskStatusChangeRecord(
        task_id=task_row.task_id,
        from_status=None,
        to_status="completed",
        reason="initial completion",
    )
    session.add(status_change)
    await session.flush()

    # 7. quality_verdict (FK -> investigation_records.id)
    verdict = quality.QualityVerdictRecord(
        investigation_id=investigation_row.id,
        groundedness_pass=True,
        evidence_ref_count=1,
        confidence_score=0.92,
        verdict_reason="every finding has at least one evidence_ref",
    )
    session.add(verdict)
    await session.flush()

    # 8. approval_record (FK -> quality_verdict.verdict_id)
    approval = quality.ApprovalRecord(
        verdict_id=verdict.verdict_id,
        approver="oncall-sre",
        decision="approved",
    )
    session.add(approval)
    await session.flush()

    # 9. audit_log (no FK; row_hash trigger fires BEFORE INSERT)
    audit_row = audit.AuditLogRecord(
        actor="agent:sre-investigator",
        action="published",
        resource_type="investigation",
        resource_id=str(investigation_row.id),
        details_json=json.dumps({"summary": "synthetic chain"}),
        input_hash="0" * 64,
        request_id=request_id,
        prev_hash=None,
    )
    session.add(audit_row)
    await session.commit()

    return {
        "alert_row": alert_row,
        "runbook_match": runbook_match,
        "investigation_row": investigation_row,
        "tool_call": tool_call,
        "task_row": task_row,
        "status_change": status_change,
        "verdict": verdict,
        "approval": approval,
        "audit_row": audit_row,
    }


async def _insert_audit_log_probe_row(
    *,
    actor: str,
    action: str,
    resource_id_prefix: str,
    request_id: uuid.UUID,
) -> uuid.UUID:
    """
    Commit a single audit_log probe row and return its ``id``.

    Used by the WORM-guard tests as the substrate for the UPDATE / DELETE
    attempts. The row commits in its own session so the subsequent
    failing-write transaction is fully independent.
    """
    async with database.get_session() as session:
        audit_row = audit.AuditLogRecord(
            actor=actor,
            action=action,
            resource_type="probe",
            resource_id=f"{resource_id_prefix}-{request_id.hex[:8]}",
            details_json=json.dumps({"probe": True}),
            input_hash="0" * 64,
            request_id=request_id,
        )
        session.add(audit_row)
        await session.commit()
        return audit_row.id


async def _attempt_audit_log_write(*, sql: str, committed_id: uuid.UUID) -> None:
    """
    Run a one-shot statement against ``audit_log`` and commit.

    Wrapped in its own helper so the ``pytest.raises`` block in the WORM
    tests stays a single simple statement (PT012). The BEFORE UPDATE /
    DELETE trigger raises ``audit_log is append-only`` server-side, which
    SQLAlchemy surfaces as ``DBAPIError``.
    """
    async with database.get_session() as session:
        await session.execute(text(sql), {"id": committed_id})
        await session.commit()


@pytest.mark.usefixtures("_db_or_skip")
class TestCanonicalTableChain:
    """
    Full RFC 12.3 chain (F3.2 - F3.8): one row per canonical table, FK-linked.
    """

    async def test_full_chain_writes_and_reads_back(self):
        # Given a fresh request_id (UUIDv4 — UUIDv7 is not in the foundations slice)
        # and a synthetic alert flow that will write one row to each canonical
        # table in dependency order
        request_id = uuid.uuid4()

        # When inserting one row into each canonical table within a single
        # transaction, in FK-dependency order, then reading back the trigger-
        # populated ``row_hash`` via an explicit SELECT (the SQLAlchemy session
        # does not refresh that column from the BEFORE INSERT trigger output
        # without an explicit re-fetch)
        async with database.get_session() as session:
            inserted = await _insert_canonical_chain(session=session, request_id=request_id)
            audit_row = inserted["audit_row"]
            row_hash_result = await session.execute(
                text("SELECT row_hash FROM audit_log WHERE id = :id"),
                {"id": audit_row.id},
            )
            persisted_row_hash = row_hash_result.scalar()

        # Then the audit_log row's hash was populated server-side by the
        # ``audit_log_compute_row_hash`` BEFORE INSERT trigger
        assert persisted_row_hash is not None
        assert len(persisted_row_hash) == 64  # sha256 hex digest

        runbook_match = inserted["runbook_match"]
        investigation_row = inserted["investigation_row"]
        tool_call = inserted["tool_call"]
        task_row = inserted["task_row"]
        status_change = inserted["status_change"]
        verdict = inserted["verdict"]
        approval = inserted["approval"]

        # And every FK relationship in the chain reads back through joins
        async with database.get_session() as read_session:
            # investigation_records carries the F3.7 columns
            stmt = select(investigations_module.InvestigationRecord).where(
                investigations_module.InvestigationRecord.request_id == request_id
            )
            investigation_back = (await read_session.execute(stmt)).scalar_one()
            assert investigation_back.runbook_match_id == runbook_match.match_id
            assert investigation_back.iteration_count == 3
            assert investigation_back.loop_cap_hit is False
            assert investigation_back.model_id_primary == "openai/gpt-4.1"
            assert investigation_back.findings_json is not None
            assert investigation_back.findings_json["findings"][0]["text"] == (
                "Synthetic finding for F3.11"
            )

            # runbook_match carries the alert_request envelope id
            stmt = select(runbooks.RunbookMatchRecord).where(
                runbooks.RunbookMatchRecord.match_id == runbook_match.match_id
            )
            runbook_back = (await read_session.execute(stmt)).scalar_one()
            assert runbook_back.request_id == request_id

            # agent_calls carries the F3.8 tool_name + tenant_id
            stmt = select(tracing.AgentCallRecord).where(
                tracing.AgentCallRecord.id == tool_call.id
            )
            tool_call_back = (await read_session.execute(stmt)).scalar_one()
            assert tool_call_back.tool_name == "k8s_describe_pod"
            assert tool_call_back.tenant_id == _TENANT_ID
            assert tool_call_back.succeeded is True

            # investigation_task -> investigation_records FK
            stmt = select(tasks.InvestigationTaskRecord).where(
                tasks.InvestigationTaskRecord.task_id == task_row.task_id
            )
            task_back = (await read_session.execute(stmt)).scalar_one()
            assert task_back.investigation_id == investigation_row.id

            # task_status_change -> investigation_task FK
            stmt = select(tasks.TaskStatusChangeRecord).where(
                tasks.TaskStatusChangeRecord.id == status_change.id
            )
            status_back = (await read_session.execute(stmt)).scalar_one()
            assert status_back.task_id == task_row.task_id

            # quality_verdict -> investigation_records FK
            stmt = select(quality.QualityVerdictRecord).where(
                quality.QualityVerdictRecord.verdict_id == verdict.verdict_id
            )
            verdict_back = (await read_session.execute(stmt)).scalar_one()
            assert verdict_back.investigation_id == investigation_row.id

            # approval_record -> quality_verdict FK
            stmt = select(quality.ApprovalRecord).where(quality.ApprovalRecord.id == approval.id)
            approval_back = (await read_session.execute(stmt)).scalar_one()
            assert approval_back.verdict_id == verdict.verdict_id

            # audit_log carries the request_id link back to alert_request
            stmt = select(audit.AuditLogRecord).where(audit.AuditLogRecord.id == audit_row.id)
            audit_back = (await read_session.execute(stmt)).scalar_one()
            assert audit_back.request_id == request_id
            assert audit_back.row_hash == persisted_row_hash


@pytest.mark.usefixtures("_db_or_skip")
class TestAuditLogWormTrigger:
    """
    F3.6 audit_log WORM trigger: UPDATE and DELETE both raise server-side.
    """

    async def test_update_on_audit_log_raises(self):
        # Given a freshly-committed audit_log row with row_hash populated
        # server-side by the BEFORE INSERT trigger
        request_id = uuid.uuid4()
        committed_id = await _insert_audit_log_probe_row(
            actor="worm-test",
            action="probe",
            resource_id_prefix="probe",
            request_id=request_id,
        )

        # When attempting to UPDATE the row in a fresh transaction
        with pytest.raises(DBAPIError) as excinfo:
            await _attempt_audit_log_write(
                sql="UPDATE audit_log SET actor = 'tampered' WHERE id = :id",
                committed_id=committed_id,
            )

        # Then the WORM guard raised the canonical append-only error
        assert "audit_log is append-only" in str(excinfo.value)

    async def test_delete_on_audit_log_raises(self):
        # Given a separate freshly-committed audit_log row
        request_id = uuid.uuid4()
        committed_id = await _insert_audit_log_probe_row(
            actor="worm-test",
            action="probe-delete",
            resource_id_prefix="probe-del",
            request_id=request_id,
        )

        # When attempting to DELETE the row in a fresh transaction
        with pytest.raises(DBAPIError) as excinfo:
            await _attempt_audit_log_write(
                sql="DELETE FROM audit_log WHERE id = :id",
                committed_id=committed_id,
            )

        # Then the WORM guard raised the canonical append-only error
        assert "audit_log is append-only" in str(excinfo.value)
