from __future__ import annotations

import uuid
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


class TestListAvailableAutomations:
    def test_returns_registered_automations(self, client):
        # Given the automations registry
        # When listing available automations
        response = client.get("/api/automations/available")

        # Then the response includes repo_health_check
        assert response.status_code == 200
        data = response.json()
        assert "repo_health_check" in data["automations"]


class TestTriggerAutomation:
    @pytest.mark.usefixtures("_database_not_configured")
    def test_returns_503_when_database_not_configured(self, client):
        # Given the database is not configured
        # When triggering an automation
        response = client.post(
            "/api/automations/trigger",
            json={"automation_name": "repo_health_check"},
        )

        # Then a 503 is returned
        assert response.status_code == 503

    @pytest.mark.usefixtures("_database_configured")
    def test_returns_400_for_missing_automation_name(self, client):
        # Given an empty payload
        # When triggering an automation
        response = client.post("/api/automations/trigger", json={})

        # Then a 400 is returned
        assert response.status_code == 400
        assert "automation_name is required" in response.json()["error"]

    @pytest.mark.usefixtures("_database_configured")
    def test_returns_400_for_unknown_automation(self, client):
        # Given an unknown automation name
        # When triggering
        response = client.post(
            "/api/automations/trigger",
            json={"automation_name": "does_not_exist"},
        )

        # Then a 400 is returned with available automations
        assert response.status_code == 400
        data = response.json()
        assert "Unknown automation" in data["error"]
        assert "available" in data

    @pytest.mark.usefixtures("_database_configured")
    def test_enqueues_automation_successfully(self, client):
        # Given a valid automation name
        job_id = uuid.uuid4()

        # When triggering
        with mock.patch(
            "sentinel.interfaces.api.routers.automations.router.enqueue.enqueue_automation",
            return_value=job_id,
        ):
            response = client.post(
                "/api/automations/trigger",
                json={
                    "automation_name": "repo_health_check",
                    "params": {"repos": ["sentinel"]},
                },
            )

        # Then a 202 is returned with the job ID
        assert response.status_code == 202
        data = response.json()
        assert data["job_id"] == str(job_id)
        assert data["automation_name"] == "repo_health_check"
