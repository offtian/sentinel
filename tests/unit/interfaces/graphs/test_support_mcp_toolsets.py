"""
Unit tests for shared MCP toolset wiring in the support review pipeline.

Covers:
- ClassifyTicket passes reviewer_toolsets to the agent.
- DraftResponse passes drafter_toolsets to the agent.
- Toolset ordering: per-agent first, shared MCP second.
"""

from __future__ import annotations

from unittest import mock

import pytest

from sentinel.interfaces.graphs import support_review
from sentinel.interfaces.graphs.agents import response_drafter, ticket_reviewer
from tests.factories import make_ticket
from tests.functional.conftest import (
    FakeAgentResult,
    StubDocumentSearcher,
    _build_fake_config,
    _make_fake_agent,
)


class TestClassifyTicketToolsets:
    @pytest.mark.asyncio
    async def test_passes_shared_mcp_toolsets_to_reviewer_agent(self) -> None:
        # Given shared MCP toolsets
        shared_toolset = mock.Mock()
        captured_kwargs: dict[str, object] = {}

        async def spy_review(*, user_prompt, deps, **kwargs):
            captured_kwargs.update(kwargs)
            return FakeAgentResult(
                ticket_reviewer.TicketClassification(
                    category="account",
                    urgency="medium",
                    required_expertise=["auth"],
                    key_questions=["Is SSO configured?"],
                    search_queries=["SSO setup"],
                )
            )

        async def fake_draft(*, user_prompt, deps, **kwargs):
            return FakeAgentResult(
                response_drafter.DraftedResponse(
                    response="Please check SSO settings.",
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

        ticket = make_ticket()

        # When the pipeline runs with reviewer_toolsets
        await support_review.review_ticket(
            ticket=ticket,
            agent_for=config.agent_for,
            document_searcher=StubDocumentSearcher(),
            reviewer_toolsets=(shared_toolset,),
        )

        # Then the reviewer agent received the shared toolsets
        assert captured_kwargs.get("toolsets") == [shared_toolset]


class TestDraftResponseToolsetOrdering:
    @pytest.mark.asyncio
    async def test_composes_per_agent_then_shared_toolsets_in_order(self) -> None:
        # Given per-agent and shared MCP toolsets
        per_agent_toolset = mock.Mock(name="support-search")
        shared_mcp_toolset = mock.Mock(name="confluence-mcp")
        captured_kwargs: dict[str, object] = {}

        async def fake_review(*, user_prompt, deps, **kwargs):
            return FakeAgentResult(
                ticket_reviewer.TicketClassification(
                    category="api",
                    urgency="high",
                    required_expertise=["api"],
                    key_questions=["What endpoint?"],
                    search_queries=["API docs"],
                )
            )

        async def spy_draft(*, user_prompt, deps, **kwargs):
            captured_kwargs.update(kwargs)
            return FakeAgentResult(
                response_drafter.DraftedResponse(
                    response="Try increasing rate limit.",
                    confidence=0.85,
                    sources_used=[],
                    notes_for_agent="",
                )
            )

        config = _build_fake_config(
            {
                "ticket_reviewer": _make_fake_agent(fake_review),
                "response_drafter": _make_fake_agent(spy_draft),
            }
        )

        ticket = make_ticket()

        # When the pipeline runs with per-agent first, shared MCP second
        await support_review.review_ticket(
            ticket=ticket,
            agent_for=config.agent_for,
            document_searcher=StubDocumentSearcher(),
            drafter_toolsets=(per_agent_toolset, shared_mcp_toolset),
        )

        # Then the drafter received toolsets in declaration order
        assert captured_kwargs.get("toolsets") == [per_agent_toolset, shared_mcp_toolset]
