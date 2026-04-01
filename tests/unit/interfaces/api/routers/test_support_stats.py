from __future__ import annotations

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


class TestGetSupportStats:
    @pytest.mark.usefixtures("_database_not_configured")
    def test_returns_503_when_database_not_configured(self, client):
        # Given the database is not configured
        # When requesting stats
        response = client.get("/api/support/stats")

        # Then a 503 is returned
        assert response.status_code == 503

    @pytest.mark.usefixtures("_database_configured")
    def test_returns_stats_with_reviews(self, client):
        # Given some reviews exist
        mock_counts = {
            "drafted": 5,
            "accepted": 10,
            "rejected": 3,
            "modified": 2,
        }

        # When requesting stats
        with mock.patch(
            "sentinel.interfaces.api.routers.support.router.support_persist.get_review_stats",
            return_value=mock_counts,
        ):
            response = client.get("/api/support/stats")

        # Then the response includes counts and acceptance rate
        assert response.status_code == 200
        data = response.json()
        assert data["total_reviews"] == 20
        assert data["total_reviewed"] == 15
        assert data["acceptance_rate"] == pytest.approx(10 / 15, abs=0.001)
        assert data["counts"]["accepted"] == 10

    @pytest.mark.usefixtures("_database_configured")
    def test_returns_null_acceptance_rate_when_no_reviews(self, client):
        # Given no reviewed reviews
        mock_counts = {
            "drafted": 3,
            "accepted": 0,
            "rejected": 0,
            "modified": 0,
        }

        # When requesting stats
        with mock.patch(
            "sentinel.interfaces.api.routers.support.router.support_persist.get_review_stats",
            return_value=mock_counts,
        ):
            response = client.get("/api/support/stats")

        # Then acceptance_rate is null
        assert response.status_code == 200
        data = response.json()
        assert data["acceptance_rate"] is None
        assert data["total_reviews"] == 3
        assert data["total_reviewed"] == 0
