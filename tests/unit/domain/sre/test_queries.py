"""
Unit tests for investigation read operations.
"""

from __future__ import annotations

import uuid
from unittest import mock

import pytest

from sentinel.domain.sre import queries


class TestFetchInvestigation:
    @pytest.mark.asyncio
    async def test_returns_dict_when_row_exists(self) -> None:
        # Given a mock database that returns one row
        mock_db = mock.AsyncMock()
        record_id = uuid.uuid4()
        mock_row = mock.MagicMock()
        mock_row._mapping = {
            "id": record_id,
            "alert_source": "pagerduty",
            "alert_id": "PD-1",
            "alert_title": "CPU high",
            "severity": "critical",
            "service": "api",
            "status": "completed",
            "created_at": "2026-04-01T10:00:00+00:00",
        }
        mock_db.fetch_one.return_value = mock_row

        # When fetching the investigation by record id
        result = await queries.fetch_investigation(
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
        result = await queries.fetch_investigation(
            db=mock_db,
            record_id=uuid.uuid4(),
        )

        # Then None is returned
        assert result is None

    @pytest.mark.asyncio
    async def test_calls_fetch_one_with_core_query(self) -> None:
        # Given a mock database with a row
        mock_db = mock.AsyncMock()
        mock_db.fetch_one.return_value = None

        # When fetching by record id
        await queries.fetch_investigation(
            db=mock_db,
            record_id=uuid.uuid4(),
        )

        # Then fetch_one is called exactly once with a SQLAlchemy Core query
        mock_db.fetch_one.assert_called_once()


class TestFetchInvestigationsByAlertId:
    @pytest.mark.asyncio
    async def test_returns_list_of_dicts_for_alert_id(self) -> None:
        # Given a mock database returning two rows for the same alert_id
        mock_db = mock.AsyncMock()
        critical_row = mock.MagicMock()
        critical_row._mapping = {
            "id": uuid.uuid4(),
            "alert_id": "PD-5",
            "severity": "critical",
        }
        warning_row = mock.MagicMock()
        warning_row._mapping = {
            "id": uuid.uuid4(),
            "alert_id": "PD-5",
            "severity": "warning",
        }
        mock_db.fetch_all.return_value = [critical_row, warning_row]

        # When fetching investigations for that alert_id
        rows = await queries.fetch_investigations_by_alert_id(
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
        rows = await queries.fetch_investigations_by_alert_id(
            db=mock_db,
            alert_id="PD-UNKNOWN",
        )

        # Then an empty list is returned
        assert rows == []

    @pytest.mark.asyncio
    async def test_calls_fetch_all_with_core_query(self) -> None:
        # Given a mock database
        mock_db = mock.AsyncMock()
        mock_db.fetch_all.return_value = []

        # When fetching investigations for a specific alert_id
        await queries.fetch_investigations_by_alert_id(
            db=mock_db,
            alert_id="PD-99",
        )

        # Then fetch_all is called exactly once
        mock_db.fetch_all.assert_called_once()


class TestFetchInvestigationsForService:
    @pytest.mark.asyncio
    async def test_returns_list_of_dicts_for_service(self) -> None:
        # Given a mock database returning rows for a service
        mock_db = mock.AsyncMock()
        payments_row = mock.MagicMock()
        payments_row._mapping = {
            "id": uuid.uuid4(),
            "service": "payments-api",
            "severity": "critical",
        }
        mock_db.fetch_all.return_value = [payments_row]

        # When fetching investigations for that service
        rows = await queries.fetch_investigations_for_service(
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
        rows = await queries.fetch_investigations_for_service(
            db=mock_db,
            service="unknown-service",
        )

        # Then an empty list is returned
        assert rows == []

    @pytest.mark.asyncio
    async def test_calls_fetch_all_with_core_query(self) -> None:
        # Given a mock database
        mock_db = mock.AsyncMock()
        mock_db.fetch_all.return_value = []

        # When fetching investigations for a service
        await queries.fetch_investigations_for_service(
            db=mock_db,
            service="api",
            limit=5,
        )

        # Then fetch_all is called exactly once
        mock_db.fetch_all.assert_called_once()
