"""
Documentation and ticket search toolsets for support agents.

Wraps the domain tool functions from ``sentinel.domain.tools.documentation``
into PydanticAI ``FunctionToolset`` instances.

Two toolsets are provided:

- **Support search toolset** — for response drafter agents.  Includes
  both document search and past-ticket resolution lookup.
- **Ticket triage toolset** — for ticket reviewer/classifier agents.
  Includes only duplicate/similar ticket detection.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import AbstractToolset, FunctionToolset

from sentinel.domain.search import searcher
from sentinel.domain.tools import documentation as doc_tools
from sentinel.plugins.toolsets import _runtime as runtime_mod


def build_support_search_toolset(
    *,
    document_searcher: searcher.BaseDocumentSearcher | None,
    ticket_searcher: searcher.BasePastTicketSearcher | None,
) -> AbstractToolset[Any]:
    """
    Build a read-only toolset for searching documentation and past tickets.

    Intended for the response drafter agent so it can pull additional
    context beyond the initial search results provided in the prompt.

    :param document_searcher: Confluence/Notion/S3 searcher, or None.
    :param ticket_searcher: Jira past-ticket searcher, or None.
    """
    toolset: FunctionToolset[Any] = FunctionToolset()

    @toolset.tool
    async def search_documentation(
        ctx: RunContext[Any],
        query: str,
        limit: int = 5,
    ) -> str:
        """
        Search documentation across Confluence, Notion, and the S3 knowledge base.

        Use this when the initial search results don't fully answer the
        customer's question and you need supplementary documentation.

        Args:
            ctx: PydanticAI run context (injected automatically).
            query: Natural language search query.
            limit: Maximum number of results to return.
        """
        return await doc_tools.search_documentation(
            client=document_searcher, query=query, limit=limit
        )

    @toolset.tool
    async def get_ticket_resolution(
        ctx: RunContext[Any],
        query: str,
        limit: int = 3,
    ) -> str:
        """
        Search past resolved tickets for similar issues and their resolutions.

        Use this to check how a similar issue was resolved before, or to
        find precedent for the recommended response.

        Args:
            ctx: PydanticAI run context (injected automatically).
            query: Search query describing the issue.
            limit: Maximum number of results to return.
        """
        return await doc_tools.search_past_tickets(
            client=ticket_searcher, query=query, limit=limit
        )

    return runtime_mod.wrap_for_replay(toolset, label="support_search")  # type: ignore[return-value]


def build_ticket_triage_toolset(
    *,
    ticket_searcher: searcher.BasePastTicketSearcher | None,
) -> AbstractToolset[Any]:
    """
    Build a read-only toolset for duplicate/similar ticket detection.

    Intended for the ticket reviewer agent so it can check whether a
    ticket has already been filed or recently resolved.

    :param ticket_searcher: Jira past-ticket searcher, or None.
    """
    toolset: FunctionToolset[Any] = FunctionToolset()

    @toolset.tool
    async def check_similar_tickets(
        ctx: RunContext[Any],
        query: str,
        limit: int = 3,
    ) -> str:
        """
        Check if a similar ticket has been recently submitted or resolved.

        Use this before classifying to detect duplicates or find relevant
        context from past incidents.

        Args:
            ctx: PydanticAI run context (injected automatically).
            query: Search query describing the ticket issue.
            limit: Maximum number of results to return.
        """
        return await doc_tools.check_similar_tickets(
            client=ticket_searcher, query=query, limit=limit
        )

    return runtime_mod.wrap_for_replay(toolset, label="ticket_triage")  # type: ignore[return-value]
