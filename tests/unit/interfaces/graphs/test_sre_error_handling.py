from __future__ import annotations

import pytest

from sentinel.domain.sre import entities as sre_entities
from sentinel.domain.sre import holmes_adapter
from sentinel.interfaces.graphs import common, sre_investigation
from sentinel.interfaces.graphs.agents import alert_classifier, root_cause_analyser
from tests import factories
from tests.factories import make_alert
from tests.functional.conftest import (
    FakeAgentResult,
    _build_fake_config,
    _make_fake_agent,
)


class TestClassifyAlertErrorHandling:
    @pytest.mark.asyncio
    async def test_returns_failed_reply_when_agent_raises(self) -> None:
        # Given a ClassifyAlert node where the agent raises a timeout error
        async def failing_run(*, user_prompt, deps, **kwargs):
            raise TimeoutError("LLM request timed out")

        config = _build_fake_config(
            {
                "alert_classifier": _make_fake_agent(failing_run),
            }
        )

        alert = make_alert()

        # When the full pipeline is run
        result = await sre_investigation.investigate_alert(
            alert=alert,
            config=config,
            holmes=factories.MockHolmesAdapter(),
            post_to_slack=False,
        )

        # Then the reply contains an error indication, not a crash
        assert result.alert_id == alert.id
        assert result.root_cause is not None
        assert "failed" in result.root_cause.lower() or "error" in result.root_cause.lower()


class TestInvestigateWithHolmesErrorHandling:
    @pytest.mark.asyncio
    async def test_continues_pipeline_when_holmes_fails(self) -> None:
        # Given Holmes adapter that raises an error
        class FailingHolmes(holmes_adapter.BaseHolmesAdapter):
            @property
            def is_configured(self) -> bool:
                return True

            async def investigate(
                self, *, alert: sre_entities.Alert
            ) -> holmes_adapter.HolmesInvestigationResult:
                raise ConnectionError("Datadog API unreachable")

        # And working classifier and analyser agents
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

        async def fake_analyse(*, user_prompt, deps, **kwargs):
            return FakeAgentResult(
                root_cause_analyser.RootCauseAnalysis(
                    root_cause="Possible issue based on alert context",
                    confidence=0.4,
                    evidence=["Limited evidence - observability data unavailable"],
                    remediation_steps=["Check manually"],
                    affected_services=["api-service"],
                    timeline="Unknown",
                )
            )

        config = _build_fake_config(
            {
                "alert_classifier": _make_fake_agent(fake_classify),
                "root_cause_analyser": _make_fake_agent(fake_analyse),
            }
        )

        alert = make_alert()

        # When the pipeline runs with a failing Holmes adapter
        result = await sre_investigation.investigate_alert(
            alert=alert,
            config=config,
            holmes=FailingHolmes(),
            post_to_slack=False,
        )

        # Then the pipeline completes with degraded results instead of crashing
        assert result.alert_id == alert.id
        assert result.root_cause is not None


class TestAnalyseRootCauseErrorHandling:
    @pytest.mark.asyncio
    async def test_continues_with_fallback_when_analyser_raises(self) -> None:
        # Given a working classifier but a failing analyser
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

        async def failing_analyse(*, user_prompt, deps, **kwargs):
            raise RuntimeError("LLM returned malformed response")

        config = _build_fake_config(
            {
                "alert_classifier": _make_fake_agent(fake_classify),
                "root_cause_analyser": _make_fake_agent(failing_analyse),
            }
        )

        alert = make_alert()

        # When the pipeline runs
        result = await sre_investigation.investigate_alert(
            alert=alert,
            config=config,
            holmes=factories.MockHolmesAdapter(),
            post_to_slack=False,
        )

        # Then the pipeline completes with fallback root cause text
        assert result.alert_id == alert.id
        assert result.root_cause is not None
        assert "unavailable" in result.root_cause.lower() or "manual" in result.root_cause.lower()


class TestPublishFindingsErrorHandling:
    @pytest.mark.asyncio
    async def test_slack_failure_does_not_block_persist(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given a Slack posting function that raises
        async def failing_slack(**kwargs):
            raise ConnectionError("Slack API down")

        from sentinel.vendors import slack

        monkeypatch.setattr(slack, "post_investigation_summary", failing_slack)

        # And a persist function that tracks calls
        persisted: list[common.InvestigationReply] = []

        async def track_persist(reply: common.InvestigationReply) -> None:
            persisted.append(reply)

        # And working agents
        async def fake_classify(*, user_prompt, deps, **kwargs):
            return FakeAgentResult(
                alert_classifier.AlertClassification(
                    severity="high",
                    affected_service="api-service",
                    category="infrastructure",
                    summary="Test",
                    requires_immediate_action=True,
                )
            )

        async def fake_analyse(*, user_prompt, deps, **kwargs):
            return FakeAgentResult(
                root_cause_analyser.RootCauseAnalysis(
                    root_cause="Test root cause",
                    confidence=0.8,
                    evidence=["evidence"],
                    remediation_steps=["step"],
                    affected_services=["api-service"],
                    timeline="now",
                )
            )

        config = _build_fake_config(
            {
                "alert_classifier": _make_fake_agent(fake_classify),
                "root_cause_analyser": _make_fake_agent(fake_analyse),
            }
        )

        alert = make_alert()

        # When the pipeline runs with Slack failing
        result = await sre_investigation.investigate_alert(
            alert=alert,
            config=config,
            holmes=factories.MockHolmesAdapter(),
            post_to_slack=True,
            persist_fn=track_persist,
        )

        # Then the pipeline still completes and persist was called
        assert result.root_cause is not None
        assert len(persisted) == 1
