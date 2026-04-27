"""
Unit tests for envelope propagation through the SRE investigation pipeline.

Covers F2.5/F2.6/F2.7:
- ``State`` requires an ``envelope``.
- Each node binds the envelope to ``structlog.contextvars`` for the duration
  of its run, and unbinds on exit (success or failure).
- ``investigate_alert`` requires an envelope kwarg and threads it into
  every span via ``instrumented_node_run``.
"""

from __future__ import annotations

from unittest import mock

import pytest
import structlog
from opentelemetry import trace as otel_trace

from sentinel.interfaces.graphs import _node_helpers, investigation
from sentinel.interfaces.graphs.agents import (
    alert_classifier,
    investigator,
    root_cause_analyser,
)
from tests import factories
from tests.functional.conftest import (
    FakeAgentResult,
    _build_fake_config,
    _make_fake_agent,
)


_NULL_SPAN_CONTEXT = otel_trace.SpanContext(
    trace_id=0,
    span_id=0,
    is_remote=False,
    trace_flags=otel_trace.TraceFlags(0),
    trace_state=otel_trace.TraceState(),
)


class TestStateRequiresEnvelope:
    def test_state_construction_without_envelope_raises_type_error(self) -> None:
        # Given an alert but no envelope
        alert = factories.make_alert()

        # When State is constructed without envelope kwarg
        # Then a TypeError is raised
        with pytest.raises(TypeError):
            investigation.State(alert=alert)  # type: ignore[call-arg]

    def test_state_construction_with_envelope_succeeds(self) -> None:
        # Given an alert and an envelope
        alert = factories.make_alert()
        envelope = factories.make_envelope()

        # When State is constructed with both
        state = investigation.State(envelope=envelope, alert=alert)

        # Then the envelope is exposed on state
        assert state.envelope is envelope
        assert state.alert is alert


class TestInvestigateAlertRequiresEnvelope:
    @pytest.mark.asyncio
    async def test_missing_envelope_kwarg_raises_type_error(self) -> None:
        # Given a working set of dependencies but no envelope
        async def fake_classify(*, user_prompt, deps, **kwargs):
            return FakeAgentResult(
                alert_classifier.AlertClassification(
                    severity="medium",
                    affected_service="api-service",
                    category="infrastructure",
                    summary="Test alert",
                    requires_immediate_action=False,
                )
            )

        config = _build_fake_config(
            {"alert_classifier": _make_fake_agent(fake_classify)},
        )
        alert = factories.make_alert()

        # When investigate_alert is called without an envelope kwarg
        # Then a TypeError is raised at the boundary
        with pytest.raises(TypeError):
            await investigation.investigate_alert(
                alert=alert,
                agent_for=config.agent_for,
                post_to_slack=False,
            )  # type: ignore[call-arg]


class TestNodeBindsEnvelopeToStructlogContext:
    @pytest.mark.asyncio
    async def test_classify_alert_binds_envelope_during_run(self) -> None:
        # Given a classifier agent that captures the structlog context vars
        captured_context: dict[str, object] = {}

        async def spy_classify(*, user_prompt, deps, **kwargs):
            # Capture the bound contextvars at the time the agent runs.
            captured_context.update(structlog.contextvars.get_contextvars())
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
                    root_cause="Cause",
                    confidence=0.8,
                    evidence=["evidence"],
                    remediation_steps=["step"],
                    affected_services=["api-service"],
                    timeline="now",
                )
            )

        config = _build_fake_config(
            {
                "alert_classifier": _make_fake_agent(spy_classify),
                "root_cause_analyser": _make_fake_agent(fake_analyse),
            },
        )
        envelope = factories.make_envelope(
            tenant_id="pm-alpha",
            cluster_id="prod-eu-west-1",
            region="eu-west-1",
        )

        # When the pipeline runs with an envelope
        await investigation.investigate_alert(
            alert=factories.make_alert(),
            envelope=envelope,
            agent_for=config.agent_for,
            post_to_slack=False,
        )

        # Then the captured contextvars contain the envelope's log context
        for key, value in envelope.to_log_context().items():
            assert captured_context.get(key) == value

    @pytest.mark.asyncio
    async def test_envelope_binding_is_unbound_after_pipeline_completes(self) -> None:
        # Given a clean structlog context before the pipeline runs
        structlog.contextvars.clear_contextvars()
        envelope = factories.make_envelope(tenant_id="pm-beta")

        async def fake_classify(*, user_prompt, deps, **kwargs):
            return FakeAgentResult(
                alert_classifier.AlertClassification(
                    severity="medium",
                    affected_service="api",
                    category="infrastructure",
                    summary="Test",
                    requires_immediate_action=False,
                )
            )

        async def fake_analyse(*, user_prompt, deps, **kwargs):
            return FakeAgentResult(
                root_cause_analyser.RootCauseAnalysis(
                    root_cause="Cause",
                    confidence=0.8,
                    evidence=[],
                    remediation_steps=[],
                    affected_services=[],
                    timeline="now",
                )
            )

        config = _build_fake_config(
            {
                "alert_classifier": _make_fake_agent(fake_classify),
                "root_cause_analyser": _make_fake_agent(fake_analyse),
            },
        )

        # When the pipeline runs and completes
        await investigation.investigate_alert(
            alert=factories.make_alert(),
            envelope=envelope,
            agent_for=config.agent_for,
            post_to_slack=False,
        )

        # Then the envelope keys are no longer bound to the context
        post_run_context = structlog.contextvars.get_contextvars()
        for key in envelope.to_log_context():
            assert key not in post_run_context


class _SpyingSpan(otel_trace.NonRecordingSpan):
    """Span stub that records calls to ``set_attributes`` on the side."""

    def __init__(self) -> None:
        super().__init__(_NULL_SPAN_CONTEXT)
        self.attribute_calls: list[dict[str, object]] = []

    def set_attributes(self, attributes):  # type: ignore[override]
        self.attribute_calls.append(dict(attributes))


class TestInvestigateAlertSetsSpanAttributesOnEveryNode:
    @pytest.mark.asyncio
    async def test_every_node_invocation_calls_set_attributes_with_envelope(self) -> None:
        # Given a spying span and stubbed agent runs
        spying_span = _SpyingSpan()

        async def fake_classify(*, user_prompt, deps, **kwargs):
            return FakeAgentResult(
                alert_classifier.AlertClassification(
                    severity="high",
                    affected_service="api",
                    category="infrastructure",
                    summary="Test",
                    requires_immediate_action=True,
                )
            )

        async def fake_analyse(*, user_prompt, deps, **kwargs):
            return FakeAgentResult(
                root_cause_analyser.RootCauseAnalysis(
                    root_cause="Cause",
                    confidence=0.85,
                    evidence=["evidence"],
                    remediation_steps=["step"],
                    affected_services=["api"],
                    timeline="now",
                )
            )

        async def fake_investigate(*, user_prompt, deps, **kwargs):
            return FakeAgentResult(
                investigator.InvestigationFindings(
                    summary="No real evidence — pipeline-shape test",
                    sources_queried=[],
                    tool_calls=[],
                )
            )

        config = _build_fake_config(
            {
                "alert_classifier": _make_fake_agent(fake_classify),
                "investigator": _make_fake_agent(fake_investigate),
                "root_cause_analyser": _make_fake_agent(fake_analyse),
            },
        )
        envelope = factories.make_envelope()

        # When the pipeline runs while only the node-helpers' OTel API is stubbed
        with mock.patch.object(
            _node_helpers.otel_trace,
            "get_current_span",
            return_value=spying_span,
        ):
            await investigation.investigate_alert(
                alert=factories.make_alert(),
                envelope=envelope,
                agent_for=config.agent_for,
                post_to_slack=False,
            )

        # Then every node set the seven envelope-plus-team-profile attributes,
        # and each agent-invocation site additionally set the agent-context attrs.
        # Six envelope-binding nodes: ClassifyAlert, MatchRunbook (F6.F.1),
        # Investigate, AnalyseRootCause, DetermineConfidence,
        # PublishFindings. F7 added the investigator agent invocation, so
        # the agent-call count is now 3 (classifier + investigator + analyser)
        # rather than the pre-F7 count of 2.
        envelope_calls = [attrs for attrs in spying_span.attribute_calls if "request_id" in attrs]
        agent_calls = [
            attrs for attrs in spying_span.attribute_calls if "prompt_version_sha" in attrs
        ]
        assert len(envelope_calls) == 6
        assert len(agent_calls) == 3
        expected_keys = {
            "request_id",
            "tenant_id",
            "cluster_id",
            "region",
            "pii_class",
            "received_at",
            "team_profile",
            "langfuse.observation.type",
            "langfuse.session.id",
            "langfuse.user.id",
        }
        for attrs in envelope_calls:
            assert set(attrs.keys()) == expected_keys
            assert attrs["request_id"] == str(envelope.request_id)
