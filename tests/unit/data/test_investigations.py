"""
Unit tests for investigation persistence via the databases library.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest import mock

import pytest

from sentinel.data import investigations as investigation_persistence


class TestPersistInvestigation:
    @pytest.mark.asyncio
    async def test_inserts_row_and_returns_uuid(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When an investigation is persisted with required fields only
        result_id = await investigation_persistence.persist_investigation(
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
        await investigation_persistence.persist_investigation(
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
    async def test_sql_targets_investigation_records_table(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When an investigation is persisted
        await investigation_persistence.persist_investigation(
            db=mock_db,
            alert_source="pagerduty",
            alert_id="PD-1",
            alert_title="Disk full",
            severity="critical",
            service="storage",
        )

        # Then the SQL references the correct table
        call_kwargs = mock_db.execute.call_args.kwargs
        assert "investigation_records" in call_kwargs["query"]

    @pytest.mark.asyncio
    async def test_values_dict_contains_required_columns(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When an investigation is persisted with required fields
        await investigation_persistence.persist_investigation(
            db=mock_db,
            alert_source="pagerduty",
            alert_id="PD-1",
            alert_title="Disk full",
            severity="critical",
            service="storage",
        )

        # Then the values dict contains all required column keys
        values = mock_db.execute.call_args.kwargs["values"]
        assert "id" in values
        assert "alert_source" in values
        assert "alert_id" in values
        assert "alert_title" in values
        assert "severity" in values
        assert "service" in values
        assert "status" in values
        assert "created_at" in values

    @pytest.mark.asyncio
    async def test_default_status_is_completed(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When an investigation is persisted without an explicit status
        await investigation_persistence.persist_investigation(
            db=mock_db,
            alert_source="pagerduty",
            alert_id="PD-1",
            alert_title="Test alert",
            severity="info",
            service="api",
        )

        # Then the status defaults to "completed"
        values = mock_db.execute.call_args.kwargs["values"]
        assert values["status"] == "completed"

    @pytest.mark.asyncio
    async def test_optional_fields_are_passed_through(self) -> None:
        # Given a mock database connection and all optional fields
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None
        trace_id = uuid.uuid4()
        started = datetime(2026, 4, 1, 10, 0, 0, tzinfo=timezone.utc)
        completed = datetime(2026, 4, 1, 10, 5, 0, tzinfo=timezone.utc)

        # When an investigation is persisted with all optional fields
        await investigation_persistence.persist_investigation(
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

        # Then all optional values are present in the values dict
        values = mock_db.execute.call_args.kwargs["values"]
        assert values["root_cause"] == "OOM killer triggered"
        assert values["remediation"] == "Increase memory limit"
        assert values["confidence_score"] == 0.92
        assert values["findings_json"] == {"summary": "OOM", "details": []}
        assert values["started_at"] == started
        assert values["completed_at"] == completed
        assert values["trace_id"] == trace_id


class TestFetchInvestigation:
    @pytest.mark.asyncio
    async def test_returns_dict_when_row_exists(self) -> None:
        # Given a mock database that returns one row
        mock_db = mock.AsyncMock()
        record_id = uuid.uuid4()
        mock_db.fetch_one.return_value = {
            "id": record_id,
            "alert_source": "pagerduty",
            "alert_id": "PD-1",
            "alert_title": "CPU high",
            "severity": "critical",
            "service": "api",
            "status": "completed",
            "created_at": "2026-04-01T10:00:00+00:00",
        }

        # When fetching the investigation by record id
        result = await investigation_persistence.fetch_investigation(
            db=mock_db,
            record_id=record_id,
        )

        # Then the row is returned as a dict
        assert result is not None
        assert result["alert_source"] == "pagerduty"

    @pytest.mark.asyncio
    async def test_returns_none_when_row_absent(self) -> None:
        # Given a mock database that returns no row
        mock_db = mock.AsyncMock()
        mock_db.fetch_one.return_value = None

        # When fetching a non-existent investigation
        result = await investigation_persistence.fetch_investigation(
            db=mock_db,
            record_id=uuid.uuid4(),
        )

        # Then None is returned
        assert result is None

    @pytest.mark.asyncio
    async def test_sql_filters_by_id(self) -> None:
        # Given a mock database with a row
        mock_db = mock.AsyncMock()
        record_id = uuid.uuid4()
        mock_db.fetch_one.return_value = {"id": record_id}

        # When fetching by record id
        await investigation_persistence.fetch_investigation(
            db=mock_db,
            record_id=record_id,
        )

        # Then the query filters by id and the value matches
        call_kwargs = mock_db.fetch_one.call_args.kwargs
        assert "investigation_records" in call_kwargs["query"]
        assert call_kwargs["values"]["id"] == record_id


class TestFetchInvestigationsByAlertId:
    @pytest.mark.asyncio
    async def test_returns_list_of_dicts_for_alert_id(self) -> None:
        # Given a mock database returning two rows for the same alert_id
        mock_db = mock.AsyncMock()
        mock_db.fetch_all.return_value = [
            {"id": uuid.uuid4(), "alert_id": "PD-5", "severity": "critical"},
            {"id": uuid.uuid4(), "alert_id": "PD-5", "severity": "warning"},
        ]

        # When fetching investigations for that alert_id
        rows = await investigation_persistence.fetch_investigations_by_alert_id(
            db=mock_db,
            alert_id="PD-5",
        )

        # Then both rows are returned as dicts
        assert len(rows) == 2
        assert rows[0]["alert_id"] == "PD-5"

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_records(self) -> None:
        # Given a mock database that returns no rows
        mock_db = mock.AsyncMock()
        mock_db.fetch_all.return_value = []

        # When fetching for an alert_id with no investigations
        rows = await investigation_persistence.fetch_investigations_by_alert_id(
            db=mock_db,
            alert_id="PD-UNKNOWN",
        )

        # Then an empty list is returned
        assert rows == []

    @pytest.mark.asyncio
    async def test_sql_filters_by_alert_id(self) -> None:
        # Given a mock database
        mock_db = mock.AsyncMock()
        mock_db.fetch_all.return_value = []

        # When fetching investigations for a specific alert_id
        await investigation_persistence.fetch_investigations_by_alert_id(
            db=mock_db,
            alert_id="PD-99",
        )

        # Then the query targets the correct table and value
        call_kwargs = mock_db.fetch_all.call_args.kwargs
        assert "investigation_records" in call_kwargs["query"]
        assert call_kwargs["values"]["alert_id"] == "PD-99"


class TestFetchInvestigationsForService:
    @pytest.mark.asyncio
    async def test_returns_list_of_dicts_for_service(self) -> None:
        # Given a mock database returning rows for a service
        mock_db = mock.AsyncMock()
        mock_db.fetch_all.return_value = [
            {"id": uuid.uuid4(), "service": "payments-api", "severity": "critical"},
        ]

        # When fetching investigations for that service
        rows = await investigation_persistence.fetch_investigations_for_service(
            db=mock_db,
            service="payments-api",
        )

        # Then the row is returned
        assert len(rows) == 1
        assert rows[0]["service"] == "payments-api"

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_records(self) -> None:
        # Given a mock database that returns no rows
        mock_db = mock.AsyncMock()
        mock_db.fetch_all.return_value = []

        # When fetching investigations for a service with no records
        rows = await investigation_persistence.fetch_investigations_for_service(
            db=mock_db,
            service="unknown-service",
        )

        # Then an empty list is returned
        assert rows == []

    @pytest.mark.asyncio
    async def test_sql_filters_by_service_and_applies_limit(self) -> None:
        # Given a mock database
        mock_db = mock.AsyncMock()
        mock_db.fetch_all.return_value = []

        # When fetching investigations for a service with a custom limit
        await investigation_persistence.fetch_investigations_for_service(
            db=mock_db,
            service="api",
            limit=5,
        )

        # Then the query targets the correct table and passes the correct values
        call_kwargs = mock_db.fetch_all.call_args.kwargs
        assert "investigation_records" in call_kwargs["query"]
        assert call_kwargs["values"]["service"] == "api"
        assert call_kwargs["values"]["limit"] == 5

    @pytest.mark.asyncio
    async def test_default_limit_is_ten(self) -> None:
        # Given a mock database
        mock_db = mock.AsyncMock()
        mock_db.fetch_all.return_value = []

        # When fetching without specifying a limit
        await investigation_persistence.fetch_investigations_for_service(
            db=mock_db,
            service="api",
        )

        # Then the default limit of 10 is used
        call_kwargs = mock_db.fetch_all.call_args.kwargs
        assert call_kwargs["values"]["limit"] == 10
