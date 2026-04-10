from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import patch

import pytest

from sentinel.domain.sre import holmes_adapter
from sentinel.interfaces.graphs import sre_investigation
from sentinel.interfaces.graphs.agents import root_cause_analyser
from tests import factories
from tests.functional.conftest import (
    FakeAgentResult,
    _build_fake_config,
    _fake_alert_classifier_run,
    _make_fake_agent,
)


async def _noop_slack(**kwargs: object) -> None:
    pass


@dataclass
class CallTracker:
    """Lightweight async callable that records every invocation."""

    calls: list[dict] = field(default_factory=list)

    async def __call__(self, *args: object, **kwargs: object) -> None:
        self.calls.append(kwargs)

    @property
    def called_once(self) -> bool:
        return len(self.calls) == 1

    @property
    def call_kwargs(self) -> dict:
        return self.calls[-1]


@pytest.fixture
def _disable_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sentinel.vendors.slack.post_investigation_summary", _noop_slack)


class TestSreInvestigationPipeline:
    @pytest.fixture(autouse=True)
    def _setup(self, _disable_side_effects, fake_sre_config):
        self._config = fake_sre_config

    async def test_full_pipeline_returns_populated_reply(self, mock_holmes, sample_alert):
        # Given a triggered alert with a Holmes adapter that returns findings
        # When running the full investigation pipeline
        reply = await sre_investigation.investigate_alert(
            alert=sample_alert,
            agent_for=self._config.agent_for,
            holmes=mock_holmes,
            post_to_slack=False,
        )

        # Then the reply contains root cause, remediation, and confidence
        assert reply.alert_id == sample_alert.id
        assert reply.root_cause is not None
        assert "OOMKill" in reply.root_cause
        assert reply.remediation is not None
        assert "memory" in reply.remediation.lower()
        assert reply.confidence is not None
        # Multi-factor scoring: 0.3*(2/5) + 0.5*0.85 + 0.2*0.8 = 0.705
        assert reply.confidence.total == pytest.approx(0.705, abs=0.01)
        assert reply.confidence.label.value == "High"

    async def test_pipeline_populates_findings_summary(self, mock_holmes, sample_alert):
        # Given a normal alert investigation
        # When running the pipeline end-to-end
        reply = await sre_investigation.investigate_alert(
            alert=sample_alert,
            agent_for=self._config.agent_for,
            holmes=mock_holmes,
            post_to_slack=False,
        )

        # Then findings_summary is populated with source data
        assert reply.findings_summary != ""
        assert "datadog_logs" in reply.findings_summary

    async def test_pipeline_calls_slack_when_enabled(self, mock_holmes, sample_alert):
        # Given Slack posting is enabled
        tracker = CallTracker()

        # When running the investigation pipeline
        with patch("sentinel.vendors.slack.post_investigation_summary", tracker):
            await sre_investigation.investigate_alert(
                alert=sample_alert,
                agent_for=self._config.agent_for,
                holmes=mock_holmes,
                post_to_slack=True,
            )

        # Then Slack is called with the investigation results
        assert tracker.called_once  # noqa: PGH005
        assert tracker.call_kwargs["alert_id"] == sample_alert.id
        assert tracker.call_kwargs["alert_title"] == sample_alert.title

    async def test_pipeline_skips_slack_when_disabled(self, mock_holmes, sample_alert):
        # Given Slack posting is disabled
        tracker = CallTracker()

        # When running the investigation pipeline
        with patch("sentinel.vendors.slack.post_investigation_summary", tracker):
            await sre_investigation.investigate_alert(
                alert=sample_alert,
                agent_for=self._config.agent_for,
                holmes=mock_holmes,
                post_to_slack=False,
            )

        # Then Slack is never called
        assert tracker.calls == []

    async def test_pipeline_invokes_persist_callback(self, mock_holmes, sample_alert):
        # Given a persist function is provided
        tracker = CallTracker()

        # When running the pipeline
        await sre_investigation.investigate_alert(
            alert=sample_alert,
            agent_for=self._config.agent_for,
            holmes=mock_holmes,
            post_to_slack=False,
            persist_fn=tracker,
        )

        # Then the persist callback is invoked with the investigation reply
        assert tracker.called_once  # noqa: PGH005

    async def test_pipeline_writes_pagerduty_note_for_pd_alerts(self, mock_holmes):
        # Given a PagerDuty-sourced alert and a PagerDuty client
        alert = factories.make_alert(source="pagerduty", alert_id="PD-INCIDENT-1")
        pd_notes: list[dict] = []

        class FakePagerDutyClient:
            def format_investigation_note(self, **kwargs: object) -> str:
                return "Investigation note"

            async def add_incident_note(self, **kwargs: object) -> None:
                pd_notes.append(kwargs)

        # When running the pipeline
        await sre_investigation.investigate_alert(
            alert=alert,
            agent_for=self._config.agent_for,
            holmes=mock_holmes,
            post_to_slack=False,
            pagerduty_client=FakePagerDutyClient(),
        )

        # Then a PagerDuty note is added to the incident
        assert len(pd_notes) == 1
        assert pd_notes[0]["incident_id"] == "PD-INCIDENT-1"

    async def test_critical_alert_flows_through_pipeline(self, mock_holmes, critical_alert):
        # Given a critical-severity alert
        # When running the full investigation
        reply = await sre_investigation.investigate_alert(
            alert=critical_alert,
            agent_for=self._config.agent_for,
            holmes=mock_holmes,
            post_to_slack=False,
        )

        # Then the pipeline completes with a valid reply
        assert reply.alert_id == critical_alert.id
        assert reply.root_cause is not None
        assert reply.confidence is not None


class TestSrePipelineWithLowConfidence:
    @pytest.fixture(autouse=True)
    def _setup(self, _disable_side_effects):
        pass

    async def test_low_confidence_holmes_produces_low_label(self, sample_alert):
        # Given Holmes returns minimal findings
        sparse_holmes = factories.MockHolmesAdapter(
            result=holmes_adapter.HolmesInvestigationResult(
                analysis="Insufficient data to determine root cause.",
                tool_calls=[],
                sources_queried=[],
            )
        )

        # And the root cause analyser reports low confidence
        async def low_confidence_run(*, user_prompt, deps, **kwargs):
            return FakeAgentResult(
                root_cause_analyser.RootCauseAnalysis(
                    root_cause="Unable to determine root cause with available data",
                    confidence=0.2,
                    evidence=[],
                    remediation_steps=["Gather additional logs", "Check recent deploys"],
                    affected_services=["unknown"],
                    timeline="Insufficient data",
                )
            )

        # Given a config with a low-confidence analyser
        config = _build_fake_config(
            {
                "alert_classifier": _make_fake_agent(_fake_alert_classifier_run),
                "root_cause_analyser": _make_fake_agent(low_confidence_run),
            }
        )

        # When running the pipeline
        reply = await sre_investigation.investigate_alert(
            alert=sample_alert,
            agent_for=config.agent_for,
            holmes=sparse_holmes,
            post_to_slack=False,
        )

        # Then confidence is low
        assert reply.confidence is not None
        assert reply.confidence.label.value == "Low"
        # Multi-factor scoring: 0.3*(0/5) + 0.5*0.2 + 0.2*0.8 = 0.26
        assert reply.confidence.total == pytest.approx(0.26, abs=0.01)
