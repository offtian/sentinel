"""
Unit tests for ticket review write operations.
"""

from __future__ import annotations

import uuid
from unittest import mock

import pytest

from sentinel.domain.support import operations


class TestPersistTicketReview:
    @pytest.mark.asyncio
    async def test_inserts_row_and_returns_uuid(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When a ticket review is persisted with required fields only
        result_id = await operations.persist_ticket_review(
            db=mock_db,
            ticket_id="10001",
            ticket_key="SUPPORT-42",
            suggested_response="Please restart the service.",
        )

        # Then a UUID is returned
        assert isinstance(result_id, uuid.UUID)

    @pytest.mark.asyncio
    async def test_calls_db_execute_once(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When a ticket review is persisted
        await operations.persist_ticket_review(
            db=mock_db,
            ticket_id="10002",
            ticket_key="SUPPORT-43",
            suggested_response="Check the logs for errors.",
        )

        # Then execute is called exactly once
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_receives_core_insert(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When a ticket review is persisted
        await operations.persist_ticket_review(
            db=mock_db,
            ticket_id="10003",
            ticket_key="SUPPORT-44",
            suggested_response="The issue is known and will be resolved soon.",
        )

        # Then the query is a SQLAlchemy Core insert targeting the correct table
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        compiled_sql = str(query)
        assert "ticket_review_records" in compiled_sql

    @pytest.mark.asyncio
    async def test_optional_fields_are_accepted(self) -> None:
        # Given a mock database connection and all optional fields
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None
        trace_id = uuid.uuid4()

        # When a ticket review is persisted with all optional fields
        result_id = await operations.persist_ticket_review(
            db=mock_db,
            ticket_id="10006",
            ticket_key="SUPPORT-47",
            suggested_response="Please update your billing info.",
            sources_json={"sources": ["notion://doc1"]},
            confidence_score=0.88,
            category="billing",
            trace_id=trace_id,
        )

        # Then a UUID is returned and execute is called
        assert isinstance(result_id, uuid.UUID)
        mock_db.execute.assert_called_once()


class TestUpdateReviewStatus:
    @pytest.mark.asyncio
    async def test_calls_db_execute_once(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None
        record_id = uuid.uuid4()

        # When updating the review status
        await operations.update_review_status(
            db=mock_db,
            record_id=record_id,
            status="accepted",
        )

        # Then execute is called exactly once
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_receives_core_update(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None
        record_id = uuid.uuid4()

        # When updating the review status
        await operations.update_review_status(
            db=mock_db,
            record_id=record_id,
            status="rejected",
        )

        # Then the query is a SQLAlchemy Core update targeting the correct table
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        compiled_sql = str(query)
        assert "ticket_review_records" in compiled_sql

    @pytest.mark.asyncio
    async def test_returns_none(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When updating review status
        result = await operations.update_review_status(
            db=mock_db,
            record_id=uuid.uuid4(),
            status="accepted",
        )

        # Then None is returned
        assert result is None
