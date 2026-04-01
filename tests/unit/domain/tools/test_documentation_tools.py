"""
Unit tests for domain documentation tool functions.

These functions are framework-agnostic (no PydanticAI dependency) so
tests validate raw input/output behaviour with fake clients.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from sentinel.domain.search import searcher
from sentinel.domain.tools import documentation as doc_tools


class _FakeDocSearcher(searcher.BaseDocumentSearcher):
    def __init__(self, *, results: list[searcher.DocumentSearchResult] | None = None) -> None:
        self._results = results or []

    async def search(self, *, query: str, limit: int) -> list[searcher.DocumentSearchResult]:
        return self._results


class _FakeTicketSearcher(searcher.BasePastTicketSearcher):
    def __init__(self, *, results: list[searcher.TicketSearchResult] | None = None) -> None:
        self._results = results or []

    async def search(self, *, query: str, limit: int) -> list[searcher.TicketSearchResult]:
        return self._results


class TestSearchDocumentation:
    async def test_returns_unavailable_when_no_client(self) -> None:
        # Given no client
        result = await doc_tools.search_documentation(client=None, query="billing")
        # Then unavailability message returned
        assert "not available" in result.lower()

    async def test_returns_formatted_results(self) -> None:
        # Given a client with results
        doc = searcher.DocumentSearchResult(
            id="d1",
            title="Billing Guide",
            excerpt="How to resolve billing errors",
            url="https://docs.example.com/billing",
            relevance=0.9,
        )
        client = _FakeDocSearcher(results=[doc])
        # When searching
        result = await doc_tools.search_documentation(client=client, query="billing")
        # Then formatted results returned
        assert "1 document(s)" in result
        assert "Billing Guide" in result

    async def test_returns_no_match_when_empty(self) -> None:
        # Given a client with no results
        client = _FakeDocSearcher(results=[])
        result = await doc_tools.search_documentation(client=client, query="xyz")
        # Then no-match message returned
        assert "no documents found" in result.lower()

    async def test_handles_failure_gracefully(self) -> None:
        # Given a client that raises
        client = _FakeDocSearcher()
        client.search = AsyncMock(side_effect=TimeoutError("API timeout"))
        result = await doc_tools.search_documentation(client=client, query="billing")
        # Then failure message returned
        assert "failed" in result.lower()
        assert "TimeoutError" in result

    async def test_truncates_long_excerpts(self) -> None:
        # Given a document with a very long excerpt
        doc = searcher.DocumentSearchResult(
            id="d2",
            title="Long",
            excerpt="A" * 500,
            url="https://docs.example.com/long",
            relevance=0.5,
        )
        client = _FakeDocSearcher(results=[doc])
        result = await doc_tools.search_documentation(client=client, query="test")
        # Then excerpt truncated to 200 chars
        assert "A" * 200 in result
        assert "A" * 201 not in result


class TestSearchPastTickets:
    async def test_returns_unavailable_when_no_client(self) -> None:
        result = await doc_tools.search_past_tickets(client=None, query="billing")
        assert "not available" in result.lower()

    async def test_returns_ticket_with_resolution(self) -> None:
        # Given a resolved ticket
        ticket = searcher.TicketSearchResult(
            id="t1",
            key="SUP-100",
            summary="Billing error",
            description="User charged twice",
            resolution="Refund issued",
            url="https://jira.example.com/SUP-100",
            relevance=0.85,
        )
        client = _FakeTicketSearcher(results=[ticket])
        result = await doc_tools.search_past_tickets(client=client, query="billing")
        assert "SUP-100" in result
        assert "Refund issued" in result

    async def test_returns_ticket_without_resolution(self) -> None:
        # Given an unresolved ticket
        ticket = searcher.TicketSearchResult(
            id="t2",
            key="SUP-200",
            summary="Intermittent error",
            description="Sporadic failures",
            resolution=None,
            url="https://jira.example.com/SUP-200",
            relevance=0.65,
        )
        client = _FakeTicketSearcher(results=[ticket])
        result = await doc_tools.search_past_tickets(client=client, query="error")
        assert "SUP-200" in result
        assert "Resolution:" not in result

    async def test_returns_no_match_when_empty(self) -> None:
        client = _FakeTicketSearcher(results=[])
        result = await doc_tools.search_past_tickets(client=client, query="xyz")
        assert "no past tickets found" in result.lower()


class TestCheckSimilarTickets:
    async def test_returns_unavailable_when_no_client(self) -> None:
        result = await doc_tools.check_similar_tickets(client=None, query="login error")
        assert "not available" in result.lower()

    async def test_returns_resolved_ticket(self) -> None:
        ticket = searcher.TicketSearchResult(
            id="t10",
            key="SUP-999",
            summary="Login 500 error",
            description="500 on /login",
            resolution="Rolled back deploy",
            url="https://jira.example.com/SUP-999",
            relevance=0.9,
        )
        client = _FakeTicketSearcher(results=[ticket])
        result = await doc_tools.check_similar_tickets(client=client, query="login 500")
        assert "SUP-999" in result
        assert "Rolled back" in result

    async def test_returns_unresolved_ticket(self) -> None:
        ticket = searcher.TicketSearchResult(
            id="t11",
            key="SUP-1000",
            summary="Login flaky",
            description="Intermittent 500",
            resolution=None,
            url="https://jira.example.com/SUP-1000",
            relevance=0.7,
        )
        client = _FakeTicketSearcher(results=[ticket])
        result = await doc_tools.check_similar_tickets(client=client, query="login")
        assert "(unresolved)" in result

    async def test_returns_no_match_when_empty(self) -> None:
        client = _FakeTicketSearcher(results=[])
        result = await doc_tools.check_similar_tickets(client=client, query="unique")
        assert "no similar past tickets" in result.lower()

    async def test_handles_failure_gracefully(self) -> None:
        client = _FakeTicketSearcher()
        client.search = AsyncMock(side_effect=ConnectionError("Jira unreachable"))
        result = await doc_tools.check_similar_tickets(client=client, query="error")
        assert "failed" in result.lower()
        assert "ConnectionError" in result
