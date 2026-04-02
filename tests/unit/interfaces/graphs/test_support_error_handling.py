from __future__ import annotations

import pytest

from sentinel.domain.search import searcher
from sentinel.interfaces.graphs import support_review
from sentinel.interfaces.graphs.agents import response_drafter, ticket_reviewer
from tests.factories import make_ticket
from tests.functional.conftest import FakeAgentResult, StubDocumentSearcher


class TestClassifyTicketErrorHandling:
    @pytest.mark.asyncio
    async def test_returns_error_reply_when_agent_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given a ticket reviewer agent that raises
        async def failing_run(*, user_prompt, model, deps, **kwargs):
            raise TimeoutError("LLM timeout")

        monkeypatch.setattr(ticket_reviewer.agent, "run", failing_run)

        ticket = make_ticket()

        # When the pipeline runs
        result = await support_review.review_ticket(
            ticket=ticket,
            reviewer_model="test-model",
            drafter_model="test-model",
        )

        # Then the reply indicates failure instead of crashing
        assert result.ticket_id == ticket.id
        assert (
            "failed" in result.suggested_response.lower()
            or "error" in result.suggested_response.lower()
        )


class TestSearchDocumentationErrorHandling:
    @pytest.mark.asyncio
    async def test_continues_when_search_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given a working classifier
        async def fake_classify(*, user_prompt, model, deps):
            return FakeAgentResult(
                ticket_reviewer.TicketClassification(
                    category="account",
                    urgency="high",
                    required_expertise=["auth"],
                    key_questions=["Is SSO expired?"],
                    search_queries=["SSO troubleshooting"],
                )
            )

        monkeypatch.setattr(ticket_reviewer.agent, "run", fake_classify)

        # And a document searcher that raises
        class FailingSearcher(searcher.BaseDocumentSearcher):
            async def search(
                self, *, query: str, limit: int
            ) -> list[searcher.DocumentSearchResult]:
                raise ConnectionError("Search service down")

        ticket = make_ticket()

        # When the pipeline runs with a failing searcher
        result = await support_review.review_ticket(
            ticket=ticket,
            document_searcher=FailingSearcher(),
            reviewer_model="test-model",
            drafter_model="test-model",
        )

        # Then the pipeline completes with a fallback response instead of crashing
        assert result.ticket_id == ticket.id
        assert result.suggested_response != ""


class TestDraftResponseErrorHandling:
    @pytest.mark.asyncio
    async def test_returns_fallback_when_drafter_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given a working classifier and searcher, but a failing drafter
        async def fake_classify(*, user_prompt, model, deps):
            return FakeAgentResult(
                ticket_reviewer.TicketClassification(
                    category="account",
                    urgency="high",
                    required_expertise=["auth"],
                    key_questions=["Is SSO expired?"],
                    search_queries=["SSO troubleshooting"],
                )
            )

        async def failing_draft(*, user_prompt, model, deps):
            raise RuntimeError("LLM returned malformed response")

        monkeypatch.setattr(ticket_reviewer.agent, "run", fake_classify)
        monkeypatch.setattr(response_drafter.agent, "run", failing_draft)

        ticket = make_ticket()

        # When the pipeline runs
        result = await support_review.review_ticket(
            ticket=ticket,
            document_searcher=StubDocumentSearcher(),
            reviewer_model="test-model",
            drafter_model="test-model",
        )

        # Then the reply has a fallback response instead of crashing
        assert result.ticket_id == ticket.id
        assert result.suggested_response != ""
