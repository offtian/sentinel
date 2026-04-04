"""
Unit tests for investigation write operations.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest import mock

import pytest

from sentinel.domain.sre import operations


class TestPersistInvestigation:
    @pytest.mark.asyncio
    async def test_inserts_row_and_returns_uuid(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When an investigation is persisted with required fields only
        result_id = await operations.persist_investigation(
            db=mock_db,
            alert_source="pagerduty",
            alert_id="PD-12345",
            alert_title="High CPU on prod-api",
            severity="critical",
            service="prod-api",
        )

        # Then a UUID is returned
        assert isinstance(result_id, uuid.UUID)

    @pytest.mark.asyncio
    async def test_calls_db_execute_once(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When an investigation is persisted
        await operations.persist_investigation(
            db=mock_db,
            alert_source="datadog",
            alert_id="DD-99",
            alert_title="Memory spike",
            severity="warning",
            service="worker",
        )

        # Then execute is called exactly once
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_default_status_is_completed(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When an investigation is persisted without an explicit status
        await operations.persist_investigation(
            db=mock_db,
            alert_source="pagerduty",
            alert_id="PD-1",
            alert_title="Test alert",
            severity="info",
            service="api",
        )

        # Then the SQLAlchemy Core insert is called with status "completed"
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        compiled = query.compile(compile_kwargs={"literal_binds": False})
        assert (
            "completed" in str(compiled.params.values())
            or compiled.params.get("status") == "completed"
        )

    @pytest.mark.asyncio
    async def test_execute_receives_core_insert(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When an investigation is persisted
        await operations.persist_investigation(
            db=mock_db,
            alert_source="pagerduty",
            alert_id="PD-1",
            alert_title="Disk full",
            severity="critical",
            service="storage",
        )

        # Then execute receives a SQLAlchemy Core insert object
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        compiled_sql = str(query)
        assert "investigation_records" in compiled_sql

    @pytest.mark.asyncio
    async def test_optional_fields_are_passed_through(self) -> None:
        # Given a mock database connection and all optional fields
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None
        trace_id = uuid.uuid4()
        started = datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC)
        completed = datetime(2026, 4, 1, 10, 5, 0, tzinfo=UTC)

        # When an investigation is persisted with all optional fields
        result_id = await operations.persist_investigation(
            db=mock_db,
            alert_source="datadog",
            alert_id="DD-42",
            alert_title="Latency spike",
            severity="warning",
            service="api",
            status="completed",
            root_cause="OOM killer triggered",
            remediation="Increase memory limit",
            confidence_score=0.92,
            findings_json={"summary": "OOM", "details": []},
            started_at=started,
            completed_at=completed,
            trace_id=trace_id,
        )

        # Then a UUID is returned and execute is called
        assert isinstance(result_id, uuid.UUID)
        mock_db.execute.assert_called_once()
