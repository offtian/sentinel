"""
Unit tests for the LangGraph support-review node functions.

Each node is an async function that reads a ``SupportReviewState`` dict
and returns a partial-state update dict. Tests stub the agents,
searchers, and config used inside each node so the contract is
asserted without crossing the network.

Covers tasks T8-T12 of the LangGraph adoption plan:
- T8 ``classify_ticket``
- T9 ``search_documentation``
- T10 ``draft_response``
- T11 ``determine_confidence``
- T12 ``wait_for_human``
"""

from __future__ import annotations

import dataclasses
from typing import Any
from unittest import mock

import pytest

from sentinel.domain.approval import entities as approval_entities
from sentinel.domain.confidence import entities as confidence_entities
from sentinel.domain.search import searcher as search_mod
from sentinel.domain.support import entities as support_entities
from sentinel.interfaces.graphs.agents import response_drafter, ticket_reviewer
from sentinel.interfaces.workflows import support_review as support_review_mod
from sentinel.interfaces.workflows import support_state as support_state_mod
from sentinel.utils import metrics as metrics_mod
from tests import factories


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


class _StubDocumentSearcher(search_mod.BaseDocumentSearcher):
    def __init__(self, results: list[search_mod.DocumentSearchResult] | None = None) -> None:
        self._results = results or [
            search_mod.DocumentSearchResult(
                id="doc-1",
                title="Login Help",
                excerpt="Reset via /account/reset",
                url="https://docs.example.com/login",
                relevance=0.92,
            ),
        ]

    async def search(self, *, query: str, limit: int) -> list[search_mod.DocumentSearchResult]:
        return list(self._results[:limit])


class _StubTicketSearcher(search_mod.BasePastTicketSearcher):
    def __init__(self, results: list[search_mod.TicketSearchResult] | None = None) -> None:
        self._results = results or [
            search_mod.TicketSearchResult(
                id="t-1",
                key="SUPPORT-1",
                summary="Past login issue",
                description="User locked out",
                resolution="Cleared cookies",
                url="https://jira.example.com/SUPPORT-1",
                relevance=0.85,
            ),
        ]

    async def search(self, *, query: str, limit: int) -> list[search_mod.TicketSearchResult]:
        return list(self._results[:limit])


class _FailingDocumentSearcher(search_mod.BaseDocumentSearcher):
    async def search(self, *, query: str, limit: int) -> list[search_mod.DocumentSearchResult]:
        raise search_mod.UnableToSearchDocumentsError("docs offline")


class _FailingTicketSearcher(search_mod.BasePastTicketSearcher):
    async def search(self, *, query: str, limit: int) -> list[search_mod.TicketSearchResult]:
        raise search_mod.UnableToSearchTicketsError("tickets offline")


# ---------------------------------------------------------------------------
# T8 — classify_ticket
# ---------------------------------------------------------------------------


def _make_classification(
    *,
    category: str = "account",
    urgency: str = "high",
    search_queries: list[str] | None = None,
) -> ticket_reviewer.TicketClassification:
    return ticket_reviewer.TicketClassification(
        category=category,
        urgency=urgency,
        required_expertise=["auth"],
        key_questions=["q?"],
        search_queries=search_queries or ["login help", "sso"],
    )


def _build_fake_config(
    *,
    agents: dict[str, Any] | None = None,
    document_searcher: search_mod.BaseDocumentSearcher | None = None,
    ticket_searcher: search_mod.BasePastTicketSearcher | None = None,
    triage_toolset: Any | None = None,
    support_search_toolset: Any | None = None,
    require_approval_below_confidence: float = 0.7,
) -> mock.MagicMock:
    """Build a config-shaped MagicMock with the surface the nodes use."""
    cfg = mock.MagicMock()
    agents = agents or {}
    cfg.agent_for = mock.MagicMock(side_effect=lambda name: agents[name])
    cfg.reviewer_model = "litellm:openai/gpt-4.1-mini"
    cfg.drafter_model = "litellm:openai/gpt-4.1"
    cfg.build_document_searcher = mock.MagicMock(return_value=document_searcher)
    cfg.build_ticket_searcher = mock.MagicMock(return_value=ticket_searcher)
    cfg.build_ticket_triage_toolset = mock.MagicMock(
        return_value=triage_toolset if triage_toolset is not None else mock.MagicMock(),
    )
    cfg.build_support_search_toolset = mock.MagicMock(
        return_value=(
            support_search_toolset if support_search_toolset is not None else mock.MagicMock()
        ),
    )
    cfg.require_approval_below_confidence = require_approval_below_confidence
    return cfg


class TestClassifyTicket:
    @pytest.mark.asyncio
    async def test_returns_classification_in_partial_state(self) -> None:
        # Given a ticket reviewer agent that returns a deterministic classification
        captured_kwargs: dict[str, Any] = {}

        async def fake_run(*, user_prompt: str, deps: Any, **kwargs: Any) -> Any:
            captured_kwargs["user_prompt"] = user_prompt
            captured_kwargs["deps"] = deps
            captured_kwargs.update(kwargs)
            return _FakeAgentResult(_make_classification())

        triage_toolset = mock.MagicMock()
        config = _build_fake_config(
            agents={"ticket_reviewer": _make_fake_agent(fake_run)},
            triage_toolset=triage_toolset,
        )

        ticket = factories.make_ticket(
            summary="Cannot log in", description="SSO error after password change"
        )
        envelope = factories.make_envelope()
        state: support_state_mod.SupportReviewState = {
            "envelope": envelope,
            "ticket": ticket,
        }

        # When classify_ticket runs with the fake config
        with mock.patch.object(support_review_mod, "get_config", return_value=config):
            result = await support_review_mod.classify_ticket(state)

        # Then the partial state contains the classification under "classification"
        classification = result["classification"]
        assert isinstance(classification, ticket_reviewer.TicketClassification)
        assert classification.category == "account"
        # And the user prompt embedded the ticket summary and description
        assert ticket.summary in captured_kwargs["user_prompt"]
        assert ticket.description in captured_kwargs["user_prompt"]
        # And the agent received Dependencies built from the ticket fields
        deps = captured_kwargs["deps"]
        assert isinstance(deps, ticket_reviewer.Dependencies)
        assert deps.ticket_summary == ticket.summary
        assert deps.ticket_description == ticket.description
        # And the toolset built by config was passed through wrapped in a list
        assert captured_kwargs["toolsets"] == [triage_toolset]

    @pytest.mark.asyncio
    async def test_re_raises_agent_failure_so_langgraph_records_it(self) -> None:
        # Given a ticket reviewer agent that raises a TimeoutError
        async def failing_run(*, user_prompt: str, deps: Any, **kwargs: Any) -> Any:
            raise TimeoutError("LLM timeout")

        config = _build_fake_config(
            agents={"ticket_reviewer": _make_fake_agent(failing_run)},
        )
        envelope = factories.make_envelope()
        ticket = factories.make_ticket()
        state: support_state_mod.SupportReviewState = {
            "envelope": envelope,
            "ticket": ticket,
        }

        # When classify_ticket runs and the agent raises
        # Then the exception propagates so LangGraph captures the failure
        with (
            mock.patch.object(support_review_mod, "get_config", return_value=config),
            pytest.raises(TimeoutError, match="LLM timeout"),
        ):
            await support_review_mod.classify_ticket(state)


# ---------------------------------------------------------------------------
# T9 — search_documentation
# ---------------------------------------------------------------------------


class TestSearchDocumentation:
    @pytest.mark.asyncio
    async def test_returns_doc_and_ticket_results_from_parallel_search(self) -> None:
        # Given a config with both searchers configured to return results
        document_searcher = _StubDocumentSearcher()
        ticket_searcher = _StubTicketSearcher()
        config = _build_fake_config(
            document_searcher=document_searcher,
            ticket_searcher=ticket_searcher,
        )

        envelope = factories.make_envelope()
        ticket = factories.make_ticket()
        classification = _make_classification(search_queries=["login", "sso", "auth"])
        state: support_state_mod.SupportReviewState = {
            "envelope": envelope,
            "ticket": ticket,
            "classification": classification,
        }

        # When search_documentation runs
        with mock.patch.object(support_review_mod, "get_config", return_value=config):
            result = await support_review_mod.search_documentation(state)

        # Then both result tuples land in the partial state
        assert len(result["doc_results"]) == 1
        assert isinstance(result["doc_results"][0], search_mod.DocumentSearchResult)
        assert result["doc_results"][0].title == "Login Help"
        assert len(result["ticket_results"]) == 1
        assert isinstance(result["ticket_results"][0], search_mod.TicketSearchResult)

    @pytest.mark.asyncio
    async def test_returns_empty_tuples_when_searchers_are_unconfigured(self) -> None:
        # Given a config where both searcher builders return None
        config = _build_fake_config(document_searcher=None, ticket_searcher=None)
        envelope = factories.make_envelope()
        ticket = factories.make_ticket()
        classification = _make_classification()
        state: support_state_mod.SupportReviewState = {
            "envelope": envelope,
            "ticket": ticket,
            "classification": classification,
        }

        # When search_documentation runs
        with mock.patch.object(support_review_mod, "get_config", return_value=config):
            result = await support_review_mod.search_documentation(state)

        # Then both result tuples are empty (graceful no-op)
        assert result["doc_results"] == ()
        assert result["ticket_results"] == ()

    @pytest.mark.asyncio
    async def test_continues_with_empty_results_when_individual_searcher_fails(
        self,
    ) -> None:
        # Given a working ticket searcher and a failing document searcher
        config = _build_fake_config(
            document_searcher=_FailingDocumentSearcher(),
            ticket_searcher=_StubTicketSearcher(),
        )
        envelope = factories.make_envelope()
        ticket = factories.make_ticket()
        classification = _make_classification()
        state: support_state_mod.SupportReviewState = {
            "envelope": envelope,
            "ticket": ticket,
            "classification": classification,
        }

        # When search_documentation runs
        with mock.patch.object(support_review_mod, "get_config", return_value=config):
            result = await support_review_mod.search_documentation(state)

        # Then the failing searcher contributes no results but the other still does
        assert result["doc_results"] == ()
        assert len(result["ticket_results"]) == 1


# ---------------------------------------------------------------------------
# T10 — draft_response
# ---------------------------------------------------------------------------


def _make_drafted_response(
    *,
    confidence: float = 0.8,
    sources: list[response_drafter.SourceReference] | None = None,
) -> response_drafter.DraftedResponse:
    return response_drafter.DraftedResponse(
        response="Hi Jane, please reset via /account/reset.",
        sources_used=sources
        or [
            response_drafter.SourceReference(
                title="Login Help",
                url="https://docs.example.com/login",
            ),
        ],
        confidence=confidence,
        notes_for_agent="May need IT admin involvement.",
    )


class TestDraftResponse:
    @pytest.mark.asyncio
    async def test_builds_response_suggestion_from_agent_output(self) -> None:
        # Given a drafter agent that returns a deterministic drafted response
        captured_kwargs: dict[str, Any] = {}

        async def fake_run(*, user_prompt: str, deps: Any, **kwargs: Any) -> Any:
            captured_kwargs["deps"] = deps
            captured_kwargs.update(kwargs)
            return _FakeAgentResult(_make_drafted_response())

        support_search_toolset = mock.MagicMock()
        config = _build_fake_config(
            agents={"response_drafter": _make_fake_agent(fake_run)},
            support_search_toolset=support_search_toolset,
        )

        envelope = factories.make_envelope()
        ticket = factories.make_ticket()
        classification = _make_classification(category="account")
        doc_results = (
            search_mod.DocumentSearchResult(
                id="d", title="t", excerpt="e", url="u", relevance=0.9
            ),
        )
        ticket_results = (
            search_mod.TicketSearchResult(
                id="t",
                key="K",
                summary="s",
                description="d",
                resolution="r",
                url="u",
                relevance=0.7,
            ),
        )
        state: support_state_mod.SupportReviewState = {
            "envelope": envelope,
            "ticket": ticket,
            "classification": classification,
            "doc_results": doc_results,
            "ticket_results": ticket_results,
        }

        # When draft_response runs
        with mock.patch.object(support_review_mod, "get_config", return_value=config):
            result = await support_review_mod.draft_response(state)

        # Then the partial state contains a ResponseSuggestion built from the agent output
        suggestion = result["response_suggestion"]
        assert isinstance(suggestion, support_entities.ResponseSuggestion)
        assert suggestion.ticket_id == ticket.id
        assert suggestion.suggested_response == "Hi Jane, please reset via /account/reset."
        assert suggestion.confidence_score == 0.8
        assert suggestion.category == "account"
        # And one DocSource was emitted from the agent's source references
        assert len(suggestion.sources) == 1
        assert suggestion.sources[0].title == "Login Help"
        assert suggestion.sources[0].url == "https://docs.example.com/login"
        # And the support search toolset was passed through to the agent
        assert captured_kwargs["toolsets"] == [support_search_toolset]
        # And Dependencies were built from state
        deps = captured_kwargs["deps"]
        assert isinstance(deps, response_drafter.Dependencies)
        assert deps.ticket_category == "account"
        assert list(deps.document_search_results) == list(doc_results)
        assert list(deps.ticket_search_results) == list(ticket_results)

    @pytest.mark.asyncio
    async def test_re_raises_drafter_failure(self) -> None:
        # Given a drafter agent that raises
        async def failing_run(*, user_prompt: str, deps: Any, **kwargs: Any) -> Any:
            raise RuntimeError("LLM returned malformed response")

        config = _build_fake_config(
            agents={"response_drafter": _make_fake_agent(failing_run)},
        )
        envelope = factories.make_envelope()
        ticket = factories.make_ticket()
        classification = _make_classification()
        state: support_state_mod.SupportReviewState = {
            "envelope": envelope,
            "ticket": ticket,
            "classification": classification,
            "doc_results": (),
            "ticket_results": (),
        }

        # When draft_response runs and the agent raises
        # Then the exception propagates
        with (
            mock.patch.object(support_review_mod, "get_config", return_value=config),
            pytest.raises(RuntimeError, match="malformed"),
        ):
            await support_review_mod.draft_response(state)


# ---------------------------------------------------------------------------
# T11 — determine_confidence
# ---------------------------------------------------------------------------


class TestDetermineConfidence:
    @pytest.mark.asyncio
    async def test_computes_confidence_and_flags_approval_when_below_threshold(
        self,
    ) -> None:
        # Given a low-confidence response suggestion built from one source
        low_confidence_suggestion = support_entities.ResponseSuggestion(
            ticket_id="10001",
            suggested_response="Try resetting your password.",
            sources=[factories.make_doc_source(relevance=0.4)],
            confidence_score=0.2,
            category="account",
        )
        config = _build_fake_config(require_approval_below_confidence=0.7)
        envelope = factories.make_envelope()
        ticket = factories.make_ticket()
        state: support_state_mod.SupportReviewState = {
            "envelope": envelope,
            "ticket": ticket,
            "response_suggestion": low_confidence_suggestion,
            "doc_results": (),
            "ticket_results": (),
        }

        # When determine_confidence runs
        with mock.patch.object(support_review_mod, "get_config", return_value=config):
            result = await support_review_mod.determine_confidence(state)

        # Then confidence is set, total below threshold, and needs_approval is True
        confidence = result["confidence"]
        assert isinstance(confidence, confidence_entities.ConfidenceScore)
        assert confidence.total < 0.7
        assert result["needs_approval"] is True

    @pytest.mark.asyncio
    async def test_does_not_require_approval_when_at_or_above_threshold(self) -> None:
        # Given a high-confidence suggestion with multiple strong sources
        strong_sources = [factories.make_doc_source(relevance=0.95) for _ in range(5)]
        high_confidence_suggestion = support_entities.ResponseSuggestion(
            ticket_id="10001",
            suggested_response="Definitive resolution from runbook.",
            sources=strong_sources,
            confidence_score=0.95,
            category="account",
        )
        config = _build_fake_config(require_approval_below_confidence=0.7)
        envelope = factories.make_envelope()
        ticket = factories.make_ticket()
        state: support_state_mod.SupportReviewState = {
            "envelope": envelope,
            "ticket": ticket,
            "response_suggestion": high_confidence_suggestion,
            "doc_results": (),
            "ticket_results": (),
        }

        # When determine_confidence runs
        with mock.patch.object(support_review_mod, "get_config", return_value=config):
            result = await support_review_mod.determine_confidence(state)

        # Then needs_approval is False because total >= threshold
        assert result["needs_approval"] is False
        assert result["confidence"].total >= 0.7

    @pytest.mark.asyncio
    async def test_records_metrics_for_completed_review(self) -> None:
        # Given a config and stubbed metrics module
        suggestion = support_entities.ResponseSuggestion(
            ticket_id="10001",
            suggested_response="ok",
            sources=[factories.make_doc_source()],
            confidence_score=0.5,
            category="account",
        )
        config = _build_fake_config(require_approval_below_confidence=0.7)
        envelope = factories.make_envelope()
        ticket = factories.make_ticket()
        state: support_state_mod.SupportReviewState = {
            "envelope": envelope,
            "ticket": ticket,
            "response_suggestion": suggestion,
            "doc_results": (),
            "ticket_results": (),
        }

        # When determine_confidence runs with metrics patched
        with (
            mock.patch.object(support_review_mod, "get_config", return_value=config),
            mock.patch.object(metrics_mod, "record_confidence_score") as record_score,
            mock.patch.object(metrics_mod, "record_review_completed") as record_done,
        ):
            await support_review_mod.determine_confidence(state)

        # Then both pipeline metrics were recorded
        record_score.assert_called_once()
        record_done.assert_called_once()
        assert record_score.call_args.kwargs["pipeline"] == "support"
        assert record_done.call_args.kwargs["outcome"] == "completed"


# ---------------------------------------------------------------------------
# T12 — wait_for_human
# ---------------------------------------------------------------------------


class TestWaitForHuman:
    @pytest.mark.asyncio
    async def test_calls_interrupt_with_canonical_payload(self) -> None:
        # Given a state ready for the approval gate
        suggestion = factories.make_response_suggestion(confidence_score=0.4)
        confidence = factories.make_confidence_score(total=0.4)
        envelope = factories.make_envelope()
        ticket = factories.make_ticket()
        state: support_state_mod.SupportReviewState = {
            "envelope": envelope,
            "ticket": ticket,
            "response_suggestion": suggestion,
            "confidence": confidence,
            "needs_approval": True,
            "doc_results": (),
            "ticket_results": (),
        }

        # And an interrupt() stub that returns an "approved" resume payload
        captured_payload: dict[str, Any] = {}

        def fake_interrupt(value: dict[str, Any]) -> dict[str, Any]:
            captured_payload.update(value)
            return {"approved": True}

        # When wait_for_human runs with interrupt() stubbed
        with mock.patch.object(support_review_mod, "interrupt", side_effect=fake_interrupt):
            result = await support_review_mod.wait_for_human(state)

        # Then interrupt was called with the canonical approval payload
        assert captured_payload["action"] == "approve_response_suggestion"
        assert captured_payload["request_id"] == str(envelope.request_id)
        assert captured_payload["suggestion_id"] == str(suggestion.id)
        assert captured_payload["confidence_total"] == confidence.total
        assert captured_payload["confidence_label"] == confidence.label.value
        # And the resume payload was mapped to ApprovalDecision.APPROVED
        assert result == {"approval_decision": approval_entities.ApprovalDecision.APPROVED}

    @pytest.mark.asyncio
    async def test_maps_rejected_resume_payload_to_rejected_enum(self) -> None:
        # Given a state ready for approval and an interrupt() stub returning rejection
        suggestion = factories.make_response_suggestion()
        confidence = factories.make_confidence_score(total=0.4)
        envelope = factories.make_envelope()
        ticket = factories.make_ticket()
        state: support_state_mod.SupportReviewState = {
            "envelope": envelope,
            "ticket": ticket,
            "response_suggestion": suggestion,
            "confidence": confidence,
            "needs_approval": True,
            "doc_results": (),
            "ticket_results": (),
        }

        # When wait_for_human resumes with a rejection payload
        with mock.patch.object(support_review_mod, "interrupt", return_value={"approved": False}):
            result = await support_review_mod.wait_for_human(state)

        # Then the approval decision is REJECTED
        assert result == {"approval_decision": approval_entities.ApprovalDecision.REJECTED}
