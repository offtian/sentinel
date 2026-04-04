"""
Unit tests for ticket review read operations.
"""

from __future__ import annotations

import uuid
from unittest import mock

import pytest

from sentinel.domain.support import queries


class TestFetchTicketReview:
    @pytest.mark.asyncio
    async def test_returns_dict_when_row_exists(self) -> None:
        # Given a mock database that returns one row
        mock_db = mock.AsyncMock()
        record_id = uuid.uuid4()
        mock_row = mock.MagicMock()
        mock_row._mapping = {
            "id": record_id,
            "ticket_id": "10001",
            "ticket_key": "SUPPORT-42",
            "suggested_response": "Please restart the service.",
            "status": "drafted",
            "created_at": "2026-04-01T10:00:00+00:00",
        }
        mock_db.fetch_one.return_value = mock_row

        # When fetching the review by record id
        result = await queries.fetch_ticket_review(
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
        result = await queries.fetch_ticket_review(
            db=mock_db,
            record_id=uuid.uuid4(),
        )

        # Then None is returned
        assert result is None

    @pytest.mark.asyncio
    async def test_calls_fetch_one_with_core_query(self) -> None:
        # Given a mock database
        mock_db = mock.AsyncMock()
        mock_db.fetch_one.return_value = None

        # When fetching by record id
        await queries.fetch_ticket_review(
            db=mock_db,
            record_id=uuid.uuid4(),
        )

        # Then fetch_one is called exactly once
        mock_db.fetch_one.assert_called_once()


class TestFetchReviewsForTicket:
    @pytest.mark.asyncio
    async def test_returns_list_of_dicts_for_ticket_key(self) -> None:
        # Given a mock database returning two rows for the same ticket_key
        mock_db = mock.AsyncMock()
        drafted_row = mock.MagicMock()
        drafted_row._mapping = {
            "id": uuid.uuid4(),
            "ticket_key": "SUPPORT-10",
            "status": "drafted",
        }
        accepted_row = mock.MagicMock()
        accepted_row._mapping = {
            "id": uuid.uuid4(),
            "ticket_key": "SUPPORT-10",
            "status": "accepted",
        }
        mock_db.fetch_all.return_value = [drafted_row, accepted_row]

        # When fetching reviews for that ticket_key
        rows = await queries.fetch_reviews_for_ticket(
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
        rows = await queries.fetch_reviews_for_ticket(
            db=mock_db,
            ticket_key="SUPPORT-UNKNOWN",
        )

        # Then an empty list is returned
        assert rows == []

    @pytest.mark.asyncio
    async def test_calls_fetch_all_with_core_query(self) -> None:
        # Given a mock database
        mock_db = mock.AsyncMock()
        mock_db.fetch_all.return_value = []

        # When fetching reviews for a specific ticket key
        await queries.fetch_reviews_for_ticket(
            db=mock_db,
            ticket_key="SUPPORT-99",
        )

        # Then fetch_all is called exactly once
        mock_db.fetch_all.assert_called_once()


class TestFetchReviewStats:
    @pytest.mark.asyncio
    async def test_returns_dict_mapping_status_to_count(self) -> None:
        # Given a mock database returning rows for two statuses
        mock_db = mock.AsyncMock()
        drafted_row = mock.MagicMock()
        drafted_row._mapping = {"status": "drafted", "count": 5}
        accepted_row = mock.MagicMock()
        accepted_row._mapping = {"status": "accepted", "count": 12}
        mock_db.fetch_all.return_value = [drafted_row, accepted_row]

        # When fetching review stats
        stats = await queries.fetch_review_stats(db=mock_db)

        # Then the result maps the returned statuses to their counts
        assert stats["drafted"] == 5
        assert stats["accepted"] == 12

    @pytest.mark.asyncio
    async def test_returns_zero_for_missing_statuses(self) -> None:
        # Given a mock database returning only one status row
        mock_db = mock.AsyncMock()
        accepted_row = mock.MagicMock()
        accepted_row._mapping = {"status": "accepted", "count": 3}
        mock_db.fetch_all.return_value = [accepted_row]

        # When fetching review stats
        stats = await queries.fetch_review_stats(db=mock_db)

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
        stats = await queries.fetch_review_stats(db=mock_db)

        # Then all four review statuses are returned with count zero
        assert stats == {"drafted": 0, "accepted": 0, "rejected": 0, "modified": 0}

    @pytest.mark.asyncio
    async def test_calls_fetch_all_with_core_query(self) -> None:
        # Given a mock database
        mock_db = mock.AsyncMock()
        mock_db.fetch_all.return_value = []

        # When fetching review stats
        await queries.fetch_review_stats(db=mock_db)

        # Then fetch_all is called exactly once
        mock_db.fetch_all.assert_called_once()
