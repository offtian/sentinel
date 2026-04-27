"""
Unit tests for envelope propagation through the support review pipeline.

Mirrors the SRE envelope tests for the support pipeline (F2.5/F2.6/F2.7).
"""

from __future__ import annotations

import contextlib
from unittest import mock

import pytest
import structlog
from opentelemetry import trace as otel_trace

from sentinel.interfaces.graphs import _node_helpers, support_review
from sentinel.interfaces.graphs.agents import response_drafter, ticket_reviewer
from tests import factories
from tests.functional.conftest import (
    FakeAgentResult,
    StubDocumentSearcher,
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


class _SpyingSpan(otel_trace.NonRecordingSpan):
    """Span stub that records calls to ``set_attributes`` on the side."""

    def __init__(self) -> None:
        super().__init__(_NULL_SPAN_CONTEXT)
        self.attribute_calls: list[dict[str, object]] = []

    def set_attributes(self, attributes):  # type: ignore[override]
        self.attribute_calls.append(dict(attributes))


class _SpyingTracer:
    """Tracer stub that yields a single shared :class:`_SpyingSpan`.

    The node helper resolves the tracer via :func:`_get_node_tracer` and
    sets attributes on the span returned by ``start_as_current_span``;
    routing every node through the same spy span lets the test count
    attribute-setting calls across the whole pipeline run.
    """

    def __init__(self, span: _SpyingSpan) -> None:
        self._span = span

    @contextlib.contextmanager
    def start_as_current_span(self, name, **kwargs):
        yield self._span


class TestSupportStateRequiresEnvelope:
    def test_state_construction_without_envelope_raises_type_error(self) -> None:
        # Given a ticket but no envelope
        ticket = factories.make_ticket()

        # When State is constructed without an envelope kwarg
        # Then a TypeError is raised
        with pytest.raises(TypeError):
            support_review.State(ticket=ticket)  # type: ignore[call-arg]


class TestReviewTicketRequiresEnvelope:
    @pytest.mark.asyncio
    async def test_missing_envelope_kwarg_raises_type_error(self) -> None:
        # Given a working set of dependencies but no envelope
        config = _build_fake_config({})
        ticket = factories.make_ticket()

        # When review_ticket is called without an envelope kwarg
        # Then a TypeError is raised at the boundary
        with pytest.raises(TypeError):
            await support_review.review_ticket(
                ticket=ticket,
                agent_for=config.agent_for,
            )  # type: ignore[call-arg]


class TestSupportNodeBindsEnvelopeToStructlogContext:
    @pytest.mark.asyncio
    async def test_classify_ticket_binds_envelope_during_run(self) -> None:
        # Given a ticket reviewer that captures the structlog contextvars
        captured_context: dict[str, object] = {}

        async def spy_review(*, user_prompt, deps, **kwargs):
            captured_context.update(structlog.contextvars.get_contextvars())
            return FakeAgentResult(
                ticket_reviewer.TicketClassification(
                    category="account",
                    urgency="high",
                    required_expertise=["auth"],
                    key_questions=["q?"],
                    search_queries=["q"],
                )
            )

        async def fake_draft(*, user_prompt, deps, **kwargs):
            return FakeAgentResult(
                response_drafter.DraftedResponse(
                    response="resp",
                    confidence=0.8,
                    sources_used=[],
                    notes_for_agent="",
                )
            )

        config = _build_fake_config(
            {
                "ticket_reviewer": _make_fake_agent(spy_review),
                "response_drafter": _make_fake_agent(fake_draft),
            }
        )
        envelope = factories.make_envelope(tenant_id="pm-gamma")

        # When the pipeline runs with an envelope
        await support_review.review_ticket(
            ticket=factories.make_ticket(),
            envelope=envelope,
            agent_for=config.agent_for,
            document_searcher=StubDocumentSearcher(),
        )

        # Then the captured contextvars contain the envelope's log context
        for key, value in envelope.to_log_context().items():
            assert captured_context.get(key) == value


class TestReviewTicketSetsSpanAttributesOnEveryNode:
    @pytest.mark.asyncio
    async def test_every_node_invocation_calls_set_attributes_with_envelope(self) -> None:
        # Given a spying span and stubbed agent runs
        spying_span = _SpyingSpan()

        async def fake_review(*, user_prompt, deps, **kwargs):
            return FakeAgentResult(
                ticket_reviewer.TicketClassification(
                    category="api",
                    urgency="high",
                    required_expertise=["api"],
                    key_questions=["q?"],
                    search_queries=["q"],
                )
            )

        async def fake_draft(*, user_prompt, deps, **kwargs):
            return FakeAgentResult(
                response_drafter.DraftedResponse(
                    response="resp",
                    confidence=0.85,
                    sources_used=[],
                    notes_for_agent="",
                )
            )

        config = _build_fake_config(
            {
                "ticket_reviewer": _make_fake_agent(fake_review),
                "response_drafter": _make_fake_agent(fake_draft),
            }
        )
        envelope = factories.make_envelope()

        # When the pipeline runs while the node-helpers' tracer is stubbed
        # to yield the spying span (so envelope attributes flow there) and
        # the OTel ``get_current_span`` is stubbed for the agent-invocation
        # call sites that still write through the active span
        with (
            mock.patch.object(
                _node_helpers,
                "_NODE_TRACER",
                _SpyingTracer(spying_span),
            ),
            mock.patch.object(
                _node_helpers.otel_trace,
                "get_current_span",
                return_value=spying_span,
            ),
        ):
            await support_review.review_ticket(
                ticket=factories.make_ticket(),
                envelope=envelope,
                agent_for=config.agent_for,
                document_searcher=StubDocumentSearcher(),
            )

        # Then every node set the seven envelope-plus-team-profile attributes,
        # and each agent-invocation site additionally set the agent-context attrs
        envelope_calls = [attrs for attrs in spying_span.attribute_calls if "request_id" in attrs]
        agent_calls = [
            attrs for attrs in spying_span.attribute_calls if "prompt_version_sha" in attrs
        ]
        assert len(envelope_calls) == 4
        assert len(agent_calls) == 2
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
