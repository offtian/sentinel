from __future__ import annotations

from unittest import mock

import pytest
from fastapi.testclient import TestClient

from sentinel.interfaces.api import app as api_app
from sentinel.utils import metrics


@pytest.fixture(autouse=True)
def _mock_db_lifespan():
    # Patch DB connect/disconnect, engine init, and the LangGraph
    # checkpointer/graph builders so these tests can run without a real
    # Postgres instance -- the /metrics endpoint has no DB or workflow
    # dependency.
    saver_close = mock.AsyncMock()
    with (
        mock.patch("sentinel.interfaces.api.app.async_db.connect_db"),
        mock.patch("sentinel.interfaces.api.app.async_db.disconnect_db"),
        mock.patch("sentinel.interfaces.api.app.database.get_engine"),
        mock.patch("sentinel.interfaces.api.app.database.close_engine"),
        mock.patch("sentinel.interfaces.api.app.bootstrap_otel.instrument_sqlalchemy"),
        mock.patch(
            "sentinel.interfaces.api.app.workflows_checkpointer.build_checkpointer",
            new=mock.AsyncMock(return_value=(mock.MagicMock(), saver_close)),
        ),
        mock.patch(
            "sentinel.interfaces.api.app.workflows_support_review.build_support_review_graph"
        ),
        mock.patch(
            "sentinel.interfaces.api.app.settings",
            return_value=mock.Mock(
                database_url="fake", otel_metrics_enabled=True, otel_service_name="sentinel-test"
            ),
        ),
    ):
        yield


class TestMetricsEndpoint:
    def test_returns_prometheus_exposition_format(self):
        # Given the FastAPI app
        with TestClient(api_app.app) as client:
            # When requesting /metrics
            response = client.get("/metrics")

            # Then the response is 200 and uses Prometheus exposition format
            assert response.status_code == 200
            content_type = response.headers["content-type"]
            assert "text/plain" in content_type

    def test_exposes_custom_sentinel_metric_names(self):
        # Given the FastAPI app with metrics initialised
        with TestClient(api_app.app) as client:
            # When fetching /metrics after recording an investigation
            metrics.record_investigation_completed(
                confidence_label="high",
                approval_required=False,
                outcome="completed",
            )
            response = client.get("/metrics")

            # Then the custom metric appears in the body
            assert "sentinel_investigations_total" in response.text
