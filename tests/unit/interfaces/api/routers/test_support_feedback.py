from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from sentinel.interfaces.api import app as api_app


@pytest.fixture
def client():
    return TestClient(api_app.app)


@pytest.fixture
def _database_configured(monkeypatch):
    from sentinel.settings import get_settings

    monkeypatch.setattr(get_settings(), "database_url", "postgresql+asyncpg://fake/db")


@pytest.fixture
def _database_not_configured(monkeypatch):
    from sentinel.settings import get_settings

    monkeypatch.setattr(get_settings(), "database_url", "")


class TestGetReview:
    @pytest.mark.usefixtures("_database_not_configured")
    def test_returns_503_when_database_not_configured(self, client):
        # Given the database is not configured
        review_id = uuid.uuid4()

        # When a review is requested
        response = client.get(f"/api/support/reviews/{review_id}")

        # Then a 503 is returned
        assert response.status_code == 503
        assert response.json()["detail"] == "Database not configured"

    @pytest.mark.usefixtures("_database_configured")
    def test_returns_404_when_review_not_found(self, client):
        # Given a review ID that does not exist
        review_id = uuid.uuid4()

        # When a review is requested
        with mock.patch(
            "sentinel.interfaces.api.routers.support.router.support_queries.fetch_ticket_review",
            return_value=None,
        ):
            response = client.get(f"/api/support/reviews/{review_id}")

        # Then a 404 is returned
        assert response.status_code == 404
        assert response.json()["error"] == "Review not found"

    @pytest.mark.usefixtures("_database_configured")
    def test_returns_review_when_found(self, client):
        # Given a review record exists
        review_id = uuid.uuid4()
        created = datetime(2024, 6, 1, tzinfo=UTC)
        record = {
            "id": review_id,
            "ticket_id": "10001",
            "ticket_key": "SUPPORT-42",
            "suggested_response": "Try resetting your password.",
            "sources_json": {"sources": []},
            "confidence_score": 0.85,
            "category": "account",
            "status": "drafted",
            "created_at": created,
            "reviewed_at": None,
        }

        # When a review is requested
        with mock.patch(
            "sentinel.interfaces.api.routers.support.router.support_queries.fetch_ticket_review",
            return_value=record,
        ):
            response = client.get(f"/api/support/reviews/{review_id}")

        # Then the review details are returned
        assert response.status_code == 200
        data = response.json()
        assert data["ticket_key"] == "SUPPORT-42"
        assert data["status"] == "drafted"
        assert data["confidence_score"] == 0.85


class TestSubmitReviewFeedback:
    @pytest.mark.usefixtures("_database_not_configured")
    def test_returns_503_when_database_not_configured(self, client):
        # Given the database is not configured
        review_id = uuid.uuid4()

        # When feedback is submitted
        response = client.post(
            f"/api/support/reviews/{review_id}/feedback",
            json={"status": "accepted"},
        )

        # Then a 503 is returned
        assert response.status_code == 503

    @pytest.mark.usefixtures("_database_configured")
    def test_returns_400_for_invalid_status(self, client):
        # Given an invalid status value
        review_id = uuid.uuid4()

        # When feedback with an invalid status is submitted
        response = client.post(
            f"/api/support/reviews/{review_id}/feedback",
            json={"status": "invalid_status"},
        )

        # Then a 400 is returned with validation message
        assert response.status_code == 400
        assert "Invalid status" in response.json()["error"]

    @pytest.mark.usefixtures("_database_configured")
    def test_returns_404_when_review_not_found(self, client):
        # Given a review ID that does not exist
        review_id = uuid.uuid4()

        # When feedback is submitted
        with mock.patch(
            "sentinel.interfaces.api.routers.support.router.support_queries.fetch_ticket_review",
            return_value=None,
        ):
            response = client.post(
                f"/api/support/reviews/{review_id}/feedback",
                json={"status": "accepted"},
            )

        # Then a 404 is returned
        assert response.status_code == 404

    @pytest.mark.usefixtures("_database_configured")
    def test_updates_status_successfully(self, client):
        # Given a review record exists
        review_id = uuid.uuid4()
        existing_record = {"id": review_id, "status": "drafted"}

        # When feedback is submitted
        with (
            mock.patch(
                "sentinel.interfaces.api.routers.support.router.support_queries.fetch_ticket_review",
                return_value=existing_record,
            ),
            mock.patch(
                "sentinel.interfaces.api.routers.support.router.support_ops.update_review_status",
            ),
        ):
            response = client.post(
                f"/api/support/reviews/{review_id}/feedback",
                json={"status": "accepted"},
            )

        # Then the updated status is returned
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"
        assert data["reviewed_at"] is not None

    @pytest.mark.usefixtures("_database_configured")
    @pytest.mark.parametrize("status", ["accepted", "rejected", "modified"])
    def test_accepts_valid_statuses(self, status, client):
        # Given a valid status value
        review_id = uuid.uuid4()
        existing_record = {"id": review_id, "status": "drafted"}

        # When feedback with that status is submitted
        with (
            mock.patch(
                "sentinel.interfaces.api.routers.support.router.support_queries.fetch_ticket_review",
                return_value=existing_record,
            ),
            mock.patch(
                "sentinel.interfaces.api.routers.support.router.support_ops.update_review_status",
            ),
        ):
            response = client.post(
                f"/api/support/reviews/{review_id}/feedback",
                json={"status": status},
            )

        # Then the request succeeds
        assert response.status_code == 200
