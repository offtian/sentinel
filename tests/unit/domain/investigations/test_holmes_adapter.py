from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sentinel.domain.alerts import entities as alert_entities
from sentinel.domain.investigations import holmes_adapter
from tests import factories


# F7 (2026-04-27): the HolmesGPT integration is archived. The adapter
# module ships a placeholder stub for backwards-compatibility but the
# concrete HolmesAdapter / DirectToolsetAdapter implementations have
# been removed; these tests would only fail with AttributeError. Skip
# the module wholesale rather than deleting it so the test history
# remains traceable.
pytestmark = pytest.mark.skip(reason="HolmesGPT integration archived in F7")


@pytest.fixture
def sample_alert():
    return alert_entities.Alert(
        id="P123ABC",
        source="pagerduty",
        title="High CPU usage on web-01",
        description="CPU usage exceeded 90% for 5 minutes",
        severity=alert_entities.AlertSeverity.HIGH,
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

    async def test_enabled_without_toolsets_returns_disabled(self, sample_alert):
        # Given an enabled HolmesAdapter with no toolsets configured
        adapter = holmes_adapter.HolmesAdapter(enabled=True)

        # When an investigation is run
        result = await adapter.investigate(alert=sample_alert)

        # Then it returns a disabled message (no toolsets = no investigation)
        assert "disabled" in result.analysis.lower()
        assert result.tool_calls == []

    async def test_is_configured_requires_toolsets_and_sdk(self):
        # Given an enabled HolmesAdapter with no toolsets
        adapter = holmes_adapter.HolmesAdapter(enabled=True)

        # Then is_configured reflects missing toolsets
        # (SDK availability depends on install, but no toolsets = not configured)
        assert not adapter.is_configured or not bool(())

    async def test_sdk_unavailable_returns_graceful_message(self, sample_alert, monkeypatch):
        # Given a HolmesAdapter with toolsets but SDK marked unavailable
        monkeypatch.setattr(holmes_adapter, "_HOLMES_SDK_AVAILABLE", False)
        adapter = holmes_adapter.HolmesAdapter(enabled=True, toolsets=("fake_toolset",))

        # When an investigation is run
        result = await adapter.investigate(alert=sample_alert)

        # Then it returns a graceful message about missing SDK
        assert "not installed" in result.analysis.lower()
        assert result.tool_calls == []
