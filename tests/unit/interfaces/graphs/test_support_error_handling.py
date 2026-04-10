from __future__ import annotations

import pytest

from sentinel.domain.search import searcher
from sentinel.interfaces.graphs import support_review
from tests.factories import make_ticket
from tests.functional.conftest import (
    StubDocumentSearcher,
    _build_fake_config,
    _fake_ticket_reviewer_run,
    _make_fake_agent,
)


class TestClassifyTicketErrorHandling:
    @pytest.mark.asyncio
    async def test_returns_error_reply_when_agent_raises(self) -> None:
        # Given a ticket reviewer agent that raises
        async def failing_run(*, user_prompt, deps, **kwargs):
            raise TimeoutError("LLM timeout")

        config = _build_fake_config(
            {
                "ticket_reviewer": _make_fake_agent(failing_run),
            }
        )

        ticket = make_ticket()

        # When the pipeline runs
        result = await support_review.review_ticket(
            ticket=ticket,
            agent_for=config.agent_for,
        )

        # Then the reply indicates failure instead of crashing
        assert result.ticket_id == ticket.id
        assert (
            "failed" in result.suggested_response.lower()
            or "error" in result.suggested_response.lower()
        )


class TestSearchDocumentationErrorHandling:
    @pytest.mark.asyncio
    async def test_continues_when_search_raises(self) -> None:
        # Given a working classifier
        config = _build_fake_config(
            {
                "ticket_reviewer": _make_fake_agent(_fake_ticket_reviewer_run),
            }
        )

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
            agent_for=config.agent_for,
            document_searcher=FailingSearcher(),
        )

        # Then the pipeline completes with a fallback response instead of crashing
        assert result.ticket_id == ticket.id
        assert result.suggested_response != ""


class TestDraftResponseErrorHandling:
    @pytest.mark.asyncio
    async def test_returns_fallback_when_drafter_raises(self) -> None:
        # Given a working classifier and searcher, but a failing drafter
        async def failing_draft(*, user_prompt, deps, **kwargs):
            raise RuntimeError("LLM returned malformed response")

        config = _build_fake_config(
            {
                "ticket_reviewer": _make_fake_agent(_fake_ticket_reviewer_run),
                "response_drafter": _make_fake_agent(failing_draft),
            }
        )

        ticket = make_ticket()

        # When the pipeline runs
        result = await support_review.review_ticket(
            ticket=ticket,
            agent_for=config.agent_for,
            document_searcher=StubDocumentSearcher(),
        )

        # Then the reply has a fallback response instead of crashing
        assert result.ticket_id == ticket.id
        assert result.suggested_response != ""
