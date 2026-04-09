"""
Unit tests for shared MCP toolset wiring in the SRE investigation pipeline.

Covers:
- ClassifyAlert passes classifier_toolsets to the agent.
- AnalyseRootCause composes per-agent toolsets before shared MCP toolsets.
"""

from __future__ import annotations

from unittest import mock

import pytest

from sentinel.interfaces.graphs import sre_investigation
from sentinel.interfaces.graphs.agents import alert_classifier, root_cause_analyser
from tests import factories
from tests.functional.conftest import FakeAgentResult


class TestClassifyAlertToolsets:
    @pytest.mark.asyncio
    async def test_passes_shared_mcp_toolsets_to_classifier_agent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given shared MCP toolsets
        shared_toolset = mock.Mock()
        captured_kwargs: dict[str, object] = {}

        async def spy_classify(*, user_prompt, model, deps, **kwargs):
            captured_kwargs.update(kwargs)
            return FakeAgentResult(
                alert_classifier.AlertClassification(
                    severity="medium",
                    affected_service="api-service",
                    category="infrastructure",
                    summary="Test alert",
                    requires_immediate_action=False,
                )
            )

        monkeypatch.setattr(alert_classifier.agent, "run", spy_classify)

        # And a no-op Holmes and analyser so the pipeline completes
        async def fake_analyse(*, user_prompt, model, deps, **kwargs):
            return FakeAgentResult(
                root_cause_analyser.RootCauseAnalysis(
                    root_cause="Unknown",
                    confidence=0.5,
                    evidence=[],
                    remediation_steps=[],
                    affected_services=[],
                    timeline="Unknown",
                )
            )

        monkeypatch.setattr(root_cause_analyser.agent, "run", fake_analyse)

        alert = factories.make_alert()

        # When the pipeline runs with classifier_toolsets
        await sre_investigation.investigate_alert(
            alert=alert,
            holmes=factories.MockHolmesAdapter(),
            classifier_model="test-model",
            analyser_model="test-model",
            post_to_slack=False,
            classifier_toolsets=(shared_toolset,),
        )

        # Then the classifier agent received the shared toolsets
        assert captured_kwargs.get("toolsets") == [shared_toolset]


class TestAnalyseRootCauseToolsetOrdering:
    @pytest.mark.asyncio
    async def test_composes_per_agent_then_shared_toolsets_in_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given per-agent and shared MCP toolsets
        per_agent_toolset = mock.Mock(name="observability")
        shared_mcp_toolset = mock.Mock(name="datadog-mcp")
        captured_kwargs: dict[str, object] = {}

        async def fake_classify(*, user_prompt, model, deps, **kwargs):
            return FakeAgentResult(
                alert_classifier.AlertClassification(
                    severity="high",
                    affected_service="api-service",
                    category="infrastructure",
                    summary="Test alert",
                    requires_immediate_action=True,
                )
            )

        async def spy_analyse(*, user_prompt, model, deps, **kwargs):
            captured_kwargs.update(kwargs)
            return FakeAgentResult(
                root_cause_analyser.RootCauseAnalysis(
                    root_cause="OOMKilled",
                    confidence=0.9,
                    evidence=["Memory spike"],
                    remediation_steps=["Increase memory"],
                    affected_services=["api-service"],
                    timeline="14:32 UTC",
                )
            )

        monkeypatch.setattr(alert_classifier.agent, "run", fake_classify)
        monkeypatch.setattr(root_cause_analyser.agent, "run", spy_analyse)

        alert = factories.make_alert()

        # When the pipeline runs with per-agent first, shared MCP second
        await sre_investigation.investigate_alert(
            alert=alert,
            holmes=factories.MockHolmesAdapter(),
            classifier_model="test-model",
            analyser_model="test-model",
            post_to_slack=False,
            analyser_toolsets=(per_agent_toolset, shared_mcp_toolset),
        )

        # Then the analyser received toolsets in declaration order
        assert captured_kwargs.get("toolsets") == [per_agent_toolset, shared_mcp_toolset]
