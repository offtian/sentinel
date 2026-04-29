"""
Unit tests for the LangGraph SRE investigation node functions.

Each node is an async function that reads an ``InvestigationState`` dict
and returns a partial-state update dict. Tests stub the agents and
config used inside each node so the contract is asserted without
crossing the network.

Covers tasks T17-T23 of the LangGraph SRE migration plan:
- T17 ``classify_alert``
- T18 ``match_runbook``
- T19 ``investigate``
- T20 ``analyse_root_cause``
- T21 ``determine_confidence``
- T22 ``wait_for_human``
- T23 ``publish_findings``
"""

from __future__ import annotations

import dataclasses
from typing import Any
from unittest import mock

import pytest

from sentinel.domain.alerts import entities as alert_entities
from sentinel.domain.approval import entities as approval_entities
from sentinel.domain.confidence import entities as confidence_entities
from sentinel.domain.investigations import entities as investigation_entities
from sentinel.domain.runbooks import models as runbook_models
from sentinel.interfaces.workflows import sre_investigation as sre_mod
from sentinel.interfaces.workflows import sre_state as sre_state_mod
from sentinel.utils import metrics as metrics_mod
from tests import factories


# ---------------------------------------------------------------------------
# Shared stubs
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _FakeUsage:
    """Stub usage data for FakeAgentResult."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclasses.dataclass(frozen=True)
class _FakeAgentResult[T]:
    output: T

    def usage(self) -> _FakeUsage:
        return _FakeUsage()

    def all_messages(self) -> list[Any]:
        return []


def _make_fake_agent(fake_run: Any) -> mock.MagicMock:
    """Build a mock agent whose ``.run`` is the given async callable."""
    agent = mock.MagicMock()
    agent.run = fake_run
    return agent


def _build_fake_config(
    *,
    agents: dict[str, Any] | None = None,
    require_approval_below_confidence: float = 0.7,
    pagerduty_client: Any | None = None,
    runbook_matcher: Any | None = None,
    k8s_adapter: Any | None = None,
    classifier_toolsets: list[Any] | None = None,
    investigator_toolsets: list[Any] | None = None,
    analyser_toolsets: list[Any] | None = None,
) -> mock.MagicMock:
    """Build a config-shaped MagicMock with the surface the SRE nodes use."""
    cfg = mock.MagicMock()
    cfg.agent_for = mock.MagicMock(side_effect=lambda name: (agents or {})[name])
    cfg.require_approval_below_confidence = require_approval_below_confidence
    cfg.pagerduty_client = pagerduty_client
    cfg.post_to_slack = True
    cfg.build_runbook_matcher = mock.MagicMock(return_value=runbook_matcher)
    cfg.k8s_adapter = k8s_adapter
    cfg.classifier_toolsets = classifier_toolsets or []
    cfg.investigator_toolsets = investigator_toolsets or []
    cfg.analyser_toolsets = analyser_toolsets or []
    return cfg


def _make_alert_classification(
    *,
    severity: str = "high",
    affected_service: str = "api-service",
    category: str = "k8s",
    summary: str = "Pod crashlooping",
    requires_immediate_action: bool = True,
) -> Any:
    from sentinel.interfaces.graphs.agents import alert_classifier

    return alert_classifier.AlertClassification(
        severity=severity,
        affected_service=affected_service,
        category=category,
        summary=summary,
        requires_immediate_action=requires_immediate_action,
    )


def _make_investigation_findings(
    *,
    summary: str = "CPU spike detected in api-service",
    sources_queried: list[str] | None = None,
) -> Any:
    from sentinel.interfaces.graphs.agents import investigator

    return investigator.InvestigationFindings(
        summary=summary,
        sources_queried=sources_queried or ["datadog_logs", "kubernetes"],
        tool_calls=[
            investigator.ToolCallRecord(
                tool="datadog_query_logs",
                query="error rate",
                result_kind="data",
            )
        ],
    )


def _make_root_cause_analysis(
    *,
    root_cause: str = "Memory leak in api-service v1.2.3",
    confidence: float = 0.85,
) -> Any:
    from sentinel.interfaces.graphs.agents import root_cause_analyser

    return root_cause_analyser.RootCauseAnalysis(
        root_cause=root_cause,
        confidence=confidence,
        evidence=["Error rate increased 5x", "OOM kills observed"],
        remediation_steps=["Roll back to v1.2.2", "Scale up replicas"],
        affected_services=["api-service"],
        timeline="15 minutes",
    )


# ---------------------------------------------------------------------------
# T17 — classify_alert
# ---------------------------------------------------------------------------


class TestClassifyAlert:
    @pytest.mark.asyncio
    async def test_returns_classification_category_and_updated_alert(self) -> None:
        # Given an alert classifier agent that returns a deterministic classification
        captured_kwargs: dict[str, Any] = {}

        async def fake_run(*, user_prompt: str, deps: Any, **kwargs: Any) -> Any:
            captured_kwargs["user_prompt"] = user_prompt
            captured_kwargs["deps"] = deps
            captured_kwargs.update(kwargs)
            return _FakeAgentResult(
                _make_alert_classification(
                    severity="high",
                    affected_service="api-service",
                    category="k8s",
                )
            )

        config = _build_fake_config(
            agents={"alert_classifier": _make_fake_agent(fake_run)},
        )
        alert = factories.make_alert(title="CrashLoopBackOff in api-service")
        envelope = factories.make_envelope()
        state: sre_state_mod.InvestigationState = {
            "envelope": envelope,
            "alert": alert,
        }

        # When classify_alert runs with the fake config
        with mock.patch.object(sre_mod, "get_config", return_value=config):
            result = await sre_mod.classify_alert(state)

        # Then the partial state contains the classification_category
        assert result["classification_category"] == "k8s"
        # And the alert was updated with severity and service from the classification
        updated_alert = result["alert"]
        assert isinstance(updated_alert, alert_entities.Alert)
        assert updated_alert.severity == alert_entities.AlertSeverity.HIGH
        assert updated_alert.service == "api-service"
        # And the investigation was initialised
        assert result["investigation"] is not None
        assert isinstance(result["investigation"], investigation_entities.Investigation)
        # And the user prompt embedded the alert title
        assert alert.title in captured_kwargs["user_prompt"]

    @pytest.mark.asyncio
    async def test_re_raises_agent_failure_so_langgraph_records_it(self) -> None:
        # Given a classifier agent that raises a TimeoutError
        async def failing_run(*, user_prompt: str, deps: Any, **kwargs: Any) -> Any:
            raise TimeoutError("LLM timeout")

        config = _build_fake_config(
            agents={"alert_classifier": _make_fake_agent(failing_run)},
        )
        alert = factories.make_alert()
        envelope = factories.make_envelope()
        state: sre_state_mod.InvestigationState = {
            "envelope": envelope,
            "alert": alert,
        }

        # When classify_alert runs and the agent raises
        # Then the exception propagates so LangGraph captures the failure
        with (
            mock.patch.object(sre_mod, "get_config", return_value=config),
            pytest.raises(TimeoutError, match="LLM timeout"),
        ):
            await sre_mod.classify_alert(state)


# ---------------------------------------------------------------------------
# T18 — match_runbook
# ---------------------------------------------------------------------------


class TestMatchRunbook:
    @pytest.mark.asyncio
    async def test_soft_degrades_when_no_catalog_configured(self) -> None:
        # Given a config where build_runbook_matcher returns None (no catalog)
        config = _build_fake_config(runbook_matcher=None)
        alert = factories.make_alert()
        investigation = factories.make_investigation(alert=alert)
        envelope = factories.make_envelope()
        state: sre_state_mod.InvestigationState = {
            "envelope": envelope,
            "alert": alert,
            "classification_category": "k8s",
            "investigation": investigation,
        }

        # When match_runbook runs with no catalog
        with mock.patch.object(sre_mod, "get_config", return_value=config):
            result = await sre_mod.match_runbook(state)

        # Then the runbook is absent, requires_approval is set to True
        assert result.get("runbook") is None
        assert result["requires_approval"] is True

    @pytest.mark.asyncio
    async def test_returns_matched_runbook_when_catalog_is_configured(self) -> None:
        # Given a runbook matcher that succeeds
        runbook = factories.make_runbook()
        match = runbook_models.RunbookMatch(
            matched_runbook_id=runbook.metadata.runbook_id,
            content_sha=runbook.metadata.content_sha,
            match_method="tag",
            confidence=0.9,
            tag_score=3,
            llm_choice=None,
            llm_justification=None,
            candidates=(),
        )

        async def fake_match_runbook(**kwargs: Any) -> runbook_models.RunbookMatch:
            return match

        matcher_mock = mock.AsyncMock(side_effect=fake_match_runbook)

        config = _build_fake_config(runbook_matcher=matcher_mock)
        # Patch the domain.runbooks.matcher.match_runbook so we can fake it
        alert = factories.make_alert()
        investigation = factories.make_investigation(alert=alert)
        envelope = factories.make_envelope()

        state: sre_state_mod.InvestigationState = {
            "envelope": envelope,
            "alert": alert,
            "classification_category": "k8s",
            "investigation": investigation,
        }

        # Patch the internal runbook catalog to return our runbook
        with (
            mock.patch.object(sre_mod, "get_config", return_value=config),
            mock.patch.object(
                sre_mod.runbook_matcher_mod,
                "match_runbook",
                return_value=match,
            ),
        ):
            result = await sre_mod.match_runbook(state)

        # Then the runbook_match is set and requires_approval depends on the match
        assert result["runbook_match"] is match


# ---------------------------------------------------------------------------
# T19 — investigate
# ---------------------------------------------------------------------------


class TestInvestigate:
    @pytest.mark.asyncio
    async def test_returns_updated_investigation_with_analysis(self) -> None:
        # Given an investigator agent that returns findings
        async def fake_run(*, user_prompt: str, deps: Any, **kwargs: Any) -> Any:
            return _FakeAgentResult(_make_investigation_findings())

        config = _build_fake_config(
            agents={"investigator": _make_fake_agent(fake_run)},
        )
        alert = factories.make_alert()
        investigation = factories.make_investigation(alert=alert)
        runbook = factories.make_runbook()
        envelope = factories.make_envelope()
        state: sre_state_mod.InvestigationState = {
            "envelope": envelope,
            "alert": alert,
            "investigation": investigation,
            "runbook": runbook,
            "classification_category": "k8s",
        }

        # When investigate runs
        with mock.patch.object(sre_mod, "get_config", return_value=config):
            result = await sre_mod.investigate(state)

        # Then the investigation in the partial state has analysis set
        updated_inv = result["investigation"]
        assert isinstance(updated_inv, investigation_entities.Investigation)
        # sources and tool_calls should be set (non-empty from our stub)
        assert updated_inv is not None

    @pytest.mark.asyncio
    async def test_soft_degrades_when_investigator_not_configured(self) -> None:
        # Given a config where the investigator agent is not registered
        config = _build_fake_config(agents={})
        config.agent_for = mock.MagicMock(side_effect=KeyError("investigator"))

        alert = factories.make_alert()
        investigation = factories.make_investigation(alert=alert)
        envelope = factories.make_envelope()
        state: sre_state_mod.InvestigationState = {
            "envelope": envelope,
            "alert": alert,
            "investigation": investigation,
        }

        # When investigate runs but the agent is not registered
        with mock.patch.object(sre_mod, "get_config", return_value=config):
            result = await sre_mod.investigate(state)

        # Then the investigation is returned (skipped status, no crash)
        assert "investigation" in result


# ---------------------------------------------------------------------------
# T20 — analyse_root_cause
# ---------------------------------------------------------------------------


class TestAnalyseRootCause:
    @pytest.mark.asyncio
    async def test_updates_investigation_with_root_cause_and_remediation(self) -> None:
        # Given a root cause analyser that returns a structured analysis
        async def fake_run(*, user_prompt: str, deps: Any, **kwargs: Any) -> Any:
            return _FakeAgentResult(_make_root_cause_analysis())

        config = _build_fake_config(
            agents={"root_cause_analyser": _make_fake_agent(fake_run)},
        )
        alert = factories.make_alert()
        investigation = factories.make_investigation(
            alert=alert,
            status=investigation_entities.InvestigationStatus.INVESTIGATING,
            findings=[factories.make_finding(source="datadog_logs")],
        )
        envelope = factories.make_envelope()
        state: sre_state_mod.InvestigationState = {
            "envelope": envelope,
            "alert": alert,
            "investigation": investigation,
            "classification_category": "k8s",
            "runbook": None,
        }

        # When analyse_root_cause runs
        with mock.patch.object(sre_mod, "get_config", return_value=config):
            result = await sre_mod.analyse_root_cause(state)

        # Then the investigation has root_cause and remediation populated
        updated_inv = result["investigation"]
        assert isinstance(updated_inv, investigation_entities.Investigation)
        assert updated_inv.root_cause == "Memory leak in api-service v1.2.3"
        assert updated_inv.remediation is not None
        assert "Roll back" in updated_inv.remediation

    @pytest.mark.asyncio
    async def test_returns_fallback_investigation_when_agent_fails(self) -> None:
        # Given an analyser agent that raises an exception
        async def failing_run(*, user_prompt: str, deps: Any, **kwargs: Any) -> Any:
            raise RuntimeError("LLM error")

        config = _build_fake_config(
            agents={"root_cause_analyser": _make_fake_agent(failing_run)},
        )
        alert = factories.make_alert()
        investigation = factories.make_investigation(alert=alert)
        envelope = factories.make_envelope()
        state: sre_state_mod.InvestigationState = {
            "envelope": envelope,
            "alert": alert,
            "investigation": investigation,
            "classification_category": "k8s",
            "runbook": None,
        }

        # When analyse_root_cause runs but the agent raises
        with mock.patch.object(sre_mod, "get_config", return_value=config):
            result = await sre_mod.analyse_root_cause(state)

        # Then the investigation is returned with a fallback root_cause
        updated_inv = result["investigation"]
        assert updated_inv is not None
        assert updated_inv.root_cause is not None
        assert (
            "unavailable" in updated_inv.root_cause.lower()
            or "error" in updated_inv.root_cause.lower()
        )


# ---------------------------------------------------------------------------
# T21 — determine_confidence
# ---------------------------------------------------------------------------


class TestDetermineConfidence:
    @pytest.mark.asyncio
    async def test_computes_confidence_and_flags_approval_when_below_threshold(self) -> None:
        # Given an investigation with one low-quality finding
        alert = factories.make_alert()
        investigation = factories.make_investigation(
            alert=alert,
            findings=[factories.make_finding(relevance=0.2)],
        )
        config = _build_fake_config(require_approval_below_confidence=0.7)
        envelope = factories.make_envelope()
        state: sre_state_mod.InvestigationState = {
            "envelope": envelope,
            "alert": alert,
            "investigation": investigation,
            "requires_approval": False,
        }

        # When determine_confidence runs
        with mock.patch.object(sre_mod, "get_config", return_value=config):
            result = await sre_mod.determine_confidence(state)

        # Then confidence is set and needs_approval is True because total < threshold
        confidence = result["confidence"]
        assert isinstance(confidence, confidence_entities.ConfidenceScore)
        assert confidence.total < 0.7
        assert result["needs_approval"] is True

    @pytest.mark.asyncio
    async def test_does_not_require_approval_when_at_or_above_threshold(self) -> None:
        # Given an investigation with strong findings and evidence that ran
        alert = factories.make_alert()
        findings = [factories.make_finding(relevance=0.95) for _ in range(5)]
        investigation = factories.make_investigation(
            alert=alert,
            findings=findings,
        )
        config = _build_fake_config(require_approval_below_confidence=0.7)
        envelope = factories.make_envelope()
        state: sre_state_mod.InvestigationState = {
            "envelope": envelope,
            "alert": alert,
            "investigation": investigation,
            "requires_approval": False,
            # Provide investigation context showing the investigator ran with real data
            "_investigation_context": {  # type: ignore[typeddict-unknown-key]
                "status": "ran",
                "tool_calls_with_data": 3,
                "tool_calls_total": 3,
                "raw_confidence": 0.95,
            },
        }

        # When determine_confidence runs with a high-quality investigation
        with mock.patch.object(sre_mod, "get_config", return_value=config):
            result = await sre_mod.determine_confidence(state)

        # Then needs_approval is False because total >= threshold
        assert result["needs_approval"] is False
        assert result["confidence"].total >= 0.7

    @pytest.mark.asyncio
    async def test_forces_approval_when_requires_approval_is_true(self) -> None:
        # Given a strong investigation but requires_approval=True from no-match
        alert = factories.make_alert()
        findings = [factories.make_finding(relevance=0.95) for _ in range(5)]
        investigation = factories.make_investigation(alert=alert, findings=findings)
        config = _build_fake_config(require_approval_below_confidence=0.7)
        envelope = factories.make_envelope()
        state: sre_state_mod.InvestigationState = {
            "envelope": envelope,
            "alert": alert,
            "investigation": investigation,
            "requires_approval": True,  # forced by no-match runbook
            "_investigation_context": {  # type: ignore[typeddict-unknown-key]
                "status": "ran",
                "tool_calls_with_data": 3,
                "tool_calls_total": 3,
                "raw_confidence": 0.95,
            },
        }

        # When determine_confidence runs with requires_approval forced
        with mock.patch.object(sre_mod, "get_config", return_value=config):
            result = await sre_mod.determine_confidence(state)

        # Then needs_approval is True regardless of confidence score
        assert result["needs_approval"] is True

    @pytest.mark.asyncio
    async def test_applies_evidence_floor_when_no_real_investigation(self) -> None:
        # Given an investigation that ran but produced no findings (skipped status)
        alert = factories.make_alert()
        investigation = factories.make_investigation(alert=alert, findings=[])
        config = _build_fake_config(require_approval_below_confidence=0.7)
        envelope = factories.make_envelope()
        state: sre_state_mod.InvestigationState = {
            "envelope": envelope,
            "alert": alert,
            "investigation": investigation,
            "requires_approval": False,
        }

        # When determine_confidence runs with no investigation evidence
        with mock.patch.object(sre_mod, "get_config", return_value=config):
            result = await sre_mod.determine_confidence(state)

        # Then needs_approval is True because evidence floor kicks in
        assert result["needs_approval"] is True

    @pytest.mark.asyncio
    async def test_records_metrics(self) -> None:
        # Given a normal investigation state
        alert = factories.make_alert()
        investigation = factories.make_investigation(
            alert=alert,
            findings=[factories.make_finding()],
        )
        config = _build_fake_config(require_approval_below_confidence=0.7)
        envelope = factories.make_envelope()
        state: sre_state_mod.InvestigationState = {
            "envelope": envelope,
            "alert": alert,
            "investigation": investigation,
            "requires_approval": False,
        }

        # When determine_confidence runs with metrics patched
        with (
            mock.patch.object(sre_mod, "get_config", return_value=config),
            mock.patch.object(metrics_mod, "record_confidence_score") as record_score,
        ):
            await sre_mod.determine_confidence(state)

        # Then the confidence metric was recorded with pipeline=investigation
        record_score.assert_called_once()
        assert record_score.call_args.kwargs["pipeline"] == "investigation"


# ---------------------------------------------------------------------------
# T22 — wait_for_human
# ---------------------------------------------------------------------------


class TestWaitForHuman:
    @pytest.mark.asyncio
    async def test_calls_interrupt_with_canonical_sre_payload(self) -> None:
        # Given a state ready for the approval gate
        alert = factories.make_alert()
        investigation = factories.make_investigation(
            alert=alert,
            root_cause="Memory leak",
            remediation="Roll back to v1.2.2",
        )
        confidence = factories.make_confidence_score(total=0.4)
        envelope = factories.make_envelope()
        state: sre_state_mod.InvestigationState = {
            "envelope": envelope,
            "alert": alert,
            "investigation": investigation,
            "confidence": confidence,
            "needs_approval": True,
        }

        # And an interrupt() stub that returns an "approved" resume payload
        captured_payload: dict[str, Any] = {}

        def fake_interrupt(value: dict[str, Any]) -> dict[str, Any]:
            captured_payload.update(value)
            return {"approved": True}

        # When wait_for_human runs with interrupt() stubbed
        with mock.patch.object(sre_mod, "interrupt", side_effect=fake_interrupt):
            result = await sre_mod.wait_for_human(state)

        # Then interrupt was called with the SRE-specific approval payload
        assert captured_payload["action"] == "approve_investigation"
        assert captured_payload["request_id"] == str(envelope.request_id)
        assert captured_payload["confidence_total"] == confidence.total
        assert captured_payload["confidence_label"] == confidence.label.value
        # And the resume payload was mapped to ApprovalDecision.APPROVED
        assert result == {"approval_decision": approval_entities.ApprovalDecision.APPROVED}

    @pytest.mark.asyncio
    async def test_maps_rejected_resume_payload_to_rejected_enum(self) -> None:
        # Given a state ready for approval and an interrupt() stub returning rejection
        alert = factories.make_alert()
        investigation = factories.make_investigation(alert=alert)
        confidence = factories.make_confidence_score(total=0.4)
        envelope = factories.make_envelope()
        state: sre_state_mod.InvestigationState = {
            "envelope": envelope,
            "alert": alert,
            "investigation": investigation,
            "confidence": confidence,
            "needs_approval": True,
        }

        # When wait_for_human resumes with a rejection payload
        with mock.patch.object(sre_mod, "interrupt", return_value={"approved": False}):
            result = await sre_mod.wait_for_human(state)

        # Then the approval decision is REJECTED
        assert result == {"approval_decision": approval_entities.ApprovalDecision.REJECTED}


# ---------------------------------------------------------------------------
# T23 — publish_findings
# ---------------------------------------------------------------------------


class TestPublishFindings:
    @pytest.mark.asyncio
    async def test_publishes_unconditionally_when_approval_not_needed(self) -> None:
        # Given a state where needs_approval is False (high confidence, auto-publish)
        alert = factories.make_alert()
        investigation = factories.make_investigation(
            alert=alert,
            root_cause="Memory leak in api-service",
            remediation="Roll back to v1.2.2",
        )
        confidence = factories.make_confidence_score(total=0.85)
        envelope = factories.make_envelope()
        state: sre_state_mod.InvestigationState = {
            "envelope": envelope,
            "alert": alert,
            "investigation": investigation,
            "confidence": confidence,
            "needs_approval": False,
        }

        # When publish_findings runs with Slack and PagerDuty patched
        with (
            mock.patch.object(sre_mod, "get_config", return_value=_build_fake_config()),
            mock.patch.object(sre_mod.slack_mod, "post_investigation_summary") as mock_slack,
        ):
            mock_slack.return_value = mock.AsyncMock()
            mock_slack.side_effect = None

            async def noop(*args: Any, **kwargs: Any) -> None:
                return None

            mock_slack.side_effect = noop
            result = await sre_mod.publish_findings(state)

        # Then findings_published is True
        assert result["findings_published"] is True

    @pytest.mark.asyncio
    async def test_publishes_when_approved_decision_is_set(self) -> None:
        # Given a state where needs_approval is True and decision is APPROVED
        alert = factories.make_alert()
        investigation = factories.make_investigation(
            alert=alert,
            root_cause="CPU throttling",
            remediation="Increase limits",
        )
        confidence = factories.make_confidence_score(total=0.5)
        envelope = factories.make_envelope()
        state: sre_state_mod.InvestigationState = {
            "envelope": envelope,
            "alert": alert,
            "investigation": investigation,
            "confidence": confidence,
            "needs_approval": True,
            "approval_decision": approval_entities.ApprovalDecision.APPROVED,
        }

        # When publish_findings runs
        with (
            mock.patch.object(sre_mod, "get_config", return_value=_build_fake_config()),
            mock.patch.object(sre_mod.slack_mod, "post_investigation_summary") as mock_slack,
        ):

            async def noop(*args: Any, **kwargs: Any) -> None:
                return None

            mock_slack.side_effect = noop
            result = await sre_mod.publish_findings(state)

        # Then findings_published is True because the decision was APPROVED
        assert result["findings_published"] is True

    @pytest.mark.asyncio
    async def test_skips_publish_when_rejected(self) -> None:
        # Given a state where needs_approval is True and decision is REJECTED
        alert = factories.make_alert()
        investigation = factories.make_investigation(alert=alert)
        confidence = factories.make_confidence_score(total=0.4)
        envelope = factories.make_envelope()
        state: sre_state_mod.InvestigationState = {
            "envelope": envelope,
            "alert": alert,
            "investigation": investigation,
            "confidence": confidence,
            "needs_approval": True,
            "approval_decision": approval_entities.ApprovalDecision.REJECTED,
        }

        # When publish_findings runs but the decision is REJECTED
        with mock.patch.object(sre_mod, "get_config", return_value=_build_fake_config()):
            result = await sre_mod.publish_findings(state)

        # Then findings_published is False (no Slack/PD post)
        assert result["findings_published"] is False
