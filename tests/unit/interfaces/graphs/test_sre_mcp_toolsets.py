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
from tests.functional.conftest import (
    FakeAgentResult,
    _build_fake_config,
    _make_fake_agent,
)


class TestClassifyAlertToolsets:
    @pytest.mark.asyncio
    async def test_passes_shared_mcp_toolsets_to_classifier_agent(self) -> None:
        # Given shared MCP toolsets
        shared_toolset = mock.Mock()
        captured_kwargs: dict[str, object] = {}

        async def spy_classify(*, user_prompt, deps, **kwargs):
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

        async def fake_analyse(*, user_prompt, deps, **kwargs):
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

        config = _build_fake_config(
            {
                "alert_classifier": _make_fake_agent(spy_classify),
                "root_cause_analyser": _make_fake_agent(fake_analyse),
            }
        )

        alert = factories.make_alert()

        # When the pipeline runs with classifier_toolsets
        await sre_investigation.investigate_alert(
            alert=alert,
            agent_for=config.agent_for,
            holmes=factories.MockHolmesAdapter(),
            post_to_slack=False,
            classifier_toolsets=(shared_toolset,),
        )

        # Then the classifier agent received the shared toolsets
        assert captured_kwargs.get("toolsets") == [shared_toolset]


class TestAnalyseRootCauseToolsetOrdering:
    @pytest.mark.asyncio
    async def test_composes_per_agent_then_shared_toolsets_in_order(self) -> None:
        # Given per-agent and shared MCP toolsets
        per_agent_toolset = mock.Mock(name="observability")
        shared_mcp_toolset = mock.Mock(name="datadog-mcp")
        captured_kwargs: dict[str, object] = {}

        async def fake_classify(*, user_prompt, deps, **kwargs):
            return FakeAgentResult(
                alert_classifier.AlertClassification(
                    severity="high",
                    affected_service="api-service",
                    category="infrastructure",
                    summary="Test alert",
                    requires_immediate_action=True,
                )
            )

        async def spy_analyse(*, user_prompt, deps, **kwargs):
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

        config = _build_fake_config(
            {
                "alert_classifier": _make_fake_agent(fake_classify),
                "root_cause_analyser": _make_fake_agent(spy_analyse),
            }
        )

        alert = factories.make_alert()

        # When the pipeline runs with per-agent first, shared MCP second
        await sre_investigation.investigate_alert(
            alert=alert,
            agent_for=config.agent_for,
            holmes=factories.MockHolmesAdapter(),
            post_to_slack=False,
            analyser_toolsets=(per_agent_toolset, shared_mcp_toolset),
        )

        # Then the analyser received toolsets in declaration order
        assert captured_kwargs.get("toolsets") == [per_agent_toolset, shared_mcp_toolset]
