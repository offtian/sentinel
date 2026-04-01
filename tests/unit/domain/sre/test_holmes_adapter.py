from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sentinel.domain.sre import entities, holmes_adapter
from tests import factories


@pytest.fixture
def sample_alert():
    return entities.Alert(
        id="P123ABC",
        source="pagerduty",
        title="High CPU usage on web-01",
        description="CPU usage exceeded 90% for 5 minutes",
        severity=entities.AlertSeverity.HIGH,
        service="api-service",
        triggered_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


class TestMockHolmesAdapter:
    async def test_default_mock_result(self, sample_alert):
        adapter = factories.MockHolmesAdapter()
        result = await adapter.investigate(alert=sample_alert)

        assert result.analysis == "Mock investigation: no issues found."
        assert len(result.tool_calls) == 2
        assert result.sources_queried == ["datadog_logs", "kubernetes"]

    async def test_custom_mock_result(self, sample_alert):
        custom_result = holmes_adapter.HolmesInvestigationResult(
            analysis="Custom analysis",
            tool_calls=[{"tool": "custom_tool", "result": "custom result"}],
            sources_queried=["custom_source"],
        )
        adapter = factories.MockHolmesAdapter(result=custom_result)
        result = await adapter.investigate(alert=sample_alert)

        assert result.analysis == "Custom analysis"
        assert len(result.tool_calls) == 1


class TestHolmesAdapter:
    async def test_disabled_adapter(self, sample_alert):
        adapter = holmes_adapter.HolmesAdapter(enabled=False)
        result = await adapter.investigate(alert=sample_alert)

        assert "disabled" in result.analysis.lower()
        assert result.tool_calls == []
        assert result.sources_queried == []

    async def test_enabled_adapter_placeholder(self, sample_alert):
        adapter = holmes_adapter.HolmesAdapter(enabled=True)
        result = await adapter.investigate(alert=sample_alert)

        # The enabled adapter returns a placeholder until SDK integration is done
        assert "pending" in result.analysis.lower()
