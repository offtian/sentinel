from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from sentinel.application.support import persist


def _make_session() -> MagicMock:
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.execute = AsyncMock()
    return session


class TestSaveTicketReview:
    async def test_saves_record_with_all_fields(self):
        # Given a session and full ticket review data
        session = _make_session()

        # When saving the ticket review
        with patch("sentinel.application.support.persist.logs"):
            record = await persist.save_ticket_review(
                session,
                ticket_id="10001",
                ticket_key="SUPPORT-42",
                suggested_response="Please reset your password at /account/reset.",
                sources_json={
                    "sources": [{"title": "Login Guide", "url": "https://docs.example.com"}]
                },
                confidence_score=0.9,
                category="account",
            )

        # Then the record is added and committed
        session.add.assert_called_once()
        session.commit.assert_awaited_once()
        session.refresh.assert_awaited_once()

        assert record.ticket_key == "SUPPORT-42"
        assert record.suggested_response == "Please reset your password at /account/reset."
        assert record.confidence_score == 0.9
        assert record.status == "drafted"

    async def test_saves_record_with_minimal_fields(self):
        # Given only required ticket review data
        # When saving the review
        session = _make_session()

        with patch("sentinel.application.support.persist.logs"):
            record = await persist.save_ticket_review(
                session,
                ticket_id="10002",
                ticket_key="SUPPORT-99",
                suggested_response="We are looking into this.",
            )

        # Then optional fields default to None
        assert record.sources_json is None
        assert record.confidence_score is None
        assert record.category is None
        assert record.status == "drafted"


class TestGetTicketReview:
    async def test_returns_record_when_found(self):
        # Given a session that finds a review record
        session = _make_session()
        record_id = uuid.uuid4()
        mock_record = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_record
        session.execute.return_value = mock_result

        # When fetching by ID
        result = await persist.get_ticket_review(session, record_id=record_id)

        # Then the record is returned
        assert result == mock_record

    async def test_returns_none_when_not_found(self):
        # Given a session that finds nothing
        session = _make_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        # When fetching a non-existent ID
        result = await persist.get_ticket_review(session, record_id=uuid.uuid4())

        # Then None is returned
        assert result is None


class TestGetReviewsForTicket:
    async def test_returns_all_reviews_for_ticket(self):
        # Given a session with two reviews for the same ticket
        session = _make_session()
        mock_record_1 = MagicMock()
        mock_record_2 = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_record_1, mock_record_2]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        session.execute.return_value = mock_result

        # When fetching all reviews for a ticket
        results = await persist.get_reviews_for_ticket(session, ticket_key="SUPPORT-42")

        # Then both reviews are returned
        assert len(results) == 2

    async def test_returns_empty_when_no_reviews(self):
        # Given a session with no reviews for the ticket
        session = _make_session()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        session.execute.return_value = mock_result

        # When fetching reviews for an unknown ticket
        results = await persist.get_reviews_for_ticket(session, ticket_key="SUPPORT-999")

        # Then an empty list is returned
        assert results == []


class TestUpdateReviewStatus:
    async def test_updates_status_when_record_exists(self):
        # Given a session with an existing record
        session = _make_session()
        record_id = uuid.uuid4()
        mock_record = MagicMock()
        mock_record.id = record_id

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_record
        session.execute.return_value = mock_result

        # When updating the status
        with patch("sentinel.application.support.persist.logs"):
            await persist.update_review_status(
                session,
                record_id=record_id,
                status="accepted",
            )

        # Then the record status is updated and committed
        assert mock_record.status == "accepted"
        session.commit.assert_awaited_once()

    async def test_returns_none_when_record_not_found(self):
        # Given a session where the record does not exist
        session = _make_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        # When updating status for a non-existent record
        result = await persist.update_review_status(
            session,
            record_id=uuid.uuid4(),
            status="accepted",
        )

        # Then None is returned and no commit is made
        assert result is None
        session.commit.assert_not_awaited()
