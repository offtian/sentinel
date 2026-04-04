"""
Unit tests for ticket review persistence via the databases library.
"""

from __future__ import annotations

import uuid
from unittest import mock

import pytest

from sentinel.data import ticket_reviews as ticket_review_persistence


class TestPersistTicketReview:
    @pytest.mark.asyncio
    async def test_inserts_row_and_returns_uuid(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When a ticket review is persisted with required fields only
        result_id = await ticket_review_persistence.persist_ticket_review(
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
        await ticket_review_persistence.persist_ticket_review(
            db=mock_db,
            ticket_id="10002",
            ticket_key="SUPPORT-43",
            suggested_response="Check the logs for errors.",
        )

        # Then execute is called exactly once
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_sql_targets_ticket_review_records_table(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When a ticket review is persisted
        await ticket_review_persistence.persist_ticket_review(
            db=mock_db,
            ticket_id="10003",
            ticket_key="SUPPORT-44",
            suggested_response="The issue is known and will be resolved soon.",
        )

        # Then the SQL references the correct table
        call_kwargs = mock_db.execute.call_args.kwargs
        assert "ticket_review_records" in call_kwargs["query"]

    @pytest.mark.asyncio
    async def test_values_dict_contains_required_columns(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When a ticket review is persisted
        await ticket_review_persistence.persist_ticket_review(
            db=mock_db,
            ticket_id="10004",
            ticket_key="SUPPORT-45",
            suggested_response="Please try clearing your cache.",
        )

        # Then the values dict contains all required column keys
        values = mock_db.execute.call_args.kwargs["values"]
        assert "id" in values
        assert "ticket_id" in values
        assert "ticket_key" in values
        assert "suggested_response" in values
        assert "status" in values
        assert "created_at" in values

    @pytest.mark.asyncio
    async def test_default_status_is_drafted(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When a ticket review is persisted without an explicit status
        await ticket_review_persistence.persist_ticket_review(
            db=mock_db,
            ticket_id="10005",
            ticket_key="SUPPORT-46",
            suggested_response="We are looking into your issue.",
        )

        # Then the status defaults to "drafted"
        values = mock_db.execute.call_args.kwargs["values"]
        assert values["status"] == "drafted"

    @pytest.mark.asyncio
    async def test_optional_fields_are_passed_through(self) -> None:
        # Given a mock database connection and all optional fields
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None
        trace_id = uuid.uuid4()

        # When a ticket review is persisted with all optional fields
        await ticket_review_persistence.persist_ticket_review(
            db=mock_db,
            ticket_id="10006",
            ticket_key="SUPPORT-47",
            suggested_response="Please update your billing info.",
            sources_json={"sources": ["notion://doc1"]},
            confidence_score=0.88,
            category="billing",
            trace_id=trace_id,
        )

        # Then all optional values are present in the values dict
        values = mock_db.execute.call_args.kwargs["values"]
        assert values["sources_json"] == {"sources": ["notion://doc1"]}
        assert values["confidence_score"] == 0.88
        assert values["category"] == "billing"
        assert values["trace_id"] == trace_id


class TestFetchTicketReview:
    @pytest.mark.asyncio
    async def test_returns_dict_when_row_exists(self) -> None:
        # Given a mock database that returns one row
        mock_db = mock.AsyncMock()
        record_id = uuid.uuid4()
        mock_db.fetch_one.return_value = {
            "id": record_id,
            "ticket_id": "10001",
            "ticket_key": "SUPPORT-42",
            "suggested_response": "Please restart the service.",
            "status": "drafted",
            "created_at": "2026-04-01T10:00:00+00:00",
        }

        # When fetching the review by record id
        result = await ticket_review_persistence.fetch_ticket_review(
            db=mock_db,
            record_id=record_id,
        )

        # Then the row is returned as a dict
        assert result is not None
        assert result["ticket_key"] == "SUPPORT-42"

    @pytest.mark.asyncio
    async def test_returns_none_when_row_absent(self) -> None:
        # Given a mock database that returns no row
        mock_db = mock.AsyncMock()
        mock_db.fetch_one.return_value = None

        # When fetching a non-existent review
        result = await ticket_review_persistence.fetch_ticket_review(
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
        await ticket_review_persistence.fetch_ticket_review(
            db=mock_db,
            record_id=record_id,
        )

        # Then the query filters by id and the value matches
        call_kwargs = mock_db.fetch_one.call_args.kwargs
        assert "ticket_review_records" in call_kwargs["query"]
        assert call_kwargs["values"]["id"] == record_id


class TestFetchReviewsForTicket:
    @pytest.mark.asyncio
    async def test_returns_list_of_dicts_for_ticket_key(self) -> None:
        # Given a mock database returning two rows for the same ticket_key
        mock_db = mock.AsyncMock()
        mock_db.fetch_all.return_value = [
            {"id": uuid.uuid4(), "ticket_key": "SUPPORT-10", "status": "drafted"},
            {"id": uuid.uuid4(), "ticket_key": "SUPPORT-10", "status": "accepted"},
        ]

        # When fetching reviews for that ticket_key
        rows = await ticket_review_persistence.fetch_reviews_for_ticket(
            db=mock_db,
            ticket_key="SUPPORT-10",
        )

        # Then both rows are returned as dicts
        assert len(rows) == 2
        assert rows[0]["ticket_key"] == "SUPPORT-10"

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_records(self) -> None:
        # Given a mock database that returns no rows
        mock_db = mock.AsyncMock()
        mock_db.fetch_all.return_value = []

        # When fetching reviews for a ticket with no records
        rows = await ticket_review_persistence.fetch_reviews_for_ticket(
            db=mock_db,
            ticket_key="SUPPORT-UNKNOWN",
        )

        # Then an empty list is returned
        assert rows == []

    @pytest.mark.asyncio
    async def test_sql_filters_by_ticket_key(self) -> None:
        # Given a mock database
        mock_db = mock.AsyncMock()
        mock_db.fetch_all.return_value = []

        # When fetching reviews for a specific ticket key
        await ticket_review_persistence.fetch_reviews_for_ticket(
            db=mock_db,
            ticket_key="SUPPORT-99",
        )

        # Then the query targets the correct table and filters by ticket_key
        call_kwargs = mock_db.fetch_all.call_args.kwargs
        assert "ticket_review_records" in call_kwargs["query"]
        assert call_kwargs["values"]["ticket_key"] == "SUPPORT-99"


class TestUpdateReviewStatus:
    @pytest.mark.asyncio
    async def test_calls_db_execute_once(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None
        record_id = uuid.uuid4()

        # When updating the review status
        await ticket_review_persistence.update_review_status(
            db=mock_db,
            record_id=record_id,
            status="accepted",
        )

        # Then execute is called exactly once
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_sql_targets_ticket_review_records_table(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None
        record_id = uuid.uuid4()

        # When updating the review status
        await ticket_review_persistence.update_review_status(
            db=mock_db,
            record_id=record_id,
            status="rejected",
        )

        # Then the SQL targets the correct table
        call_kwargs = mock_db.execute.call_args.kwargs
        assert "ticket_review_records" in call_kwargs["query"]

    @pytest.mark.asyncio
    async def test_values_dict_contains_status_and_record_id(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None
        record_id = uuid.uuid4()

        # When updating the review status to modified
        await ticket_review_persistence.update_review_status(
            db=mock_db,
            record_id=record_id,
            status="modified",
        )

        # Then the values dict contains the record id and new status
        values = mock_db.execute.call_args.kwargs["values"]
        assert values["id"] == record_id
        assert values["status"] == "modified"

    @pytest.mark.asyncio
    async def test_returns_none(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When updating review status
        result = await ticket_review_persistence.update_review_status(
            db=mock_db,
            record_id=uuid.uuid4(),
            status="accepted",
        )

        # Then None is returned
        assert result is None


class TestFetchReviewStats:
    @pytest.mark.asyncio
    async def test_returns_dict_mapping_status_to_count(self) -> None:
        # Given a mock database returning rows for two statuses
        mock_db = mock.AsyncMock()
        mock_db.fetch_all.return_value = [
            {"status": "drafted", "count": 5},
            {"status": "accepted", "count": 12},
        ]

        # When fetching review stats
        stats = await ticket_review_persistence.fetch_review_stats(db=mock_db)

        # Then the result maps the returned statuses to their counts
        assert stats["drafted"] == 5
        assert stats["accepted"] == 12

    @pytest.mark.asyncio
    async def test_returns_zero_for_missing_statuses(self) -> None:
        # Given a mock database returning only one status row
        mock_db = mock.AsyncMock()
        mock_db.fetch_all.return_value = [
            {"status": "accepted", "count": 3},
        ]

        # When fetching review stats
        stats = await ticket_review_persistence.fetch_review_stats(db=mock_db)

        # Then all ReviewStatus values are present with 0 default for missing ones
        assert stats["drafted"] == 0
        assert stats["accepted"] == 3
        assert stats["rejected"] == 0
        assert stats["modified"] == 0

    @pytest.mark.asyncio
    async def test_returns_all_statuses_with_zero_when_no_records(self) -> None:
        # Given a mock database returning no rows
        mock_db = mock.AsyncMock()
        mock_db.fetch_all.return_value = []

        # When fetching review stats with no data
        stats = await ticket_review_persistence.fetch_review_stats(db=mock_db)

        # Then all four review statuses are returned with count zero
        assert stats == {"drafted": 0, "accepted": 0, "rejected": 0, "modified": 0}

    @pytest.mark.asyncio
    async def test_sql_groups_by_status(self) -> None:
        # Given a mock database
        mock_db = mock.AsyncMock()
        mock_db.fetch_all.return_value = []

        # When fetching review stats
        await ticket_review_persistence.fetch_review_stats(db=mock_db)

        # Then the query targets the correct table and groups by status
        call_kwargs = mock_db.fetch_all.call_args.kwargs
        assert "ticket_review_records" in call_kwargs["query"]
        assert "GROUP BY" in call_kwargs["query"].upper()
