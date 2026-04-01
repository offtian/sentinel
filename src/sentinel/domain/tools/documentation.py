"""
Read-only documentation and ticket search tools for support agents.

Each function queries a search backend (Confluence, Jira, S3, etc.)
and returns a human-readable summary string.  Functions are
framework-agnostic — testable without PydanticAI.
"""

from __future__ import annotations

from sentinel.domain.search import searcher
from sentinel.utils import logs


async def search_documentation(
    *,
    client: searcher.BaseDocumentSearcher | None,
    query: str,
    limit: int = 5,
) -> str:
    """
    Search documentation across Confluence, Notion, and the S3 knowledge base.

    Return a formatted list of matching documents, or a fallback message
    when the client is unavailable.
    """
    if client is None:
        return "Document searcher not available. Use the documents already provided."

    try:
        results = await client.search(query=query, limit=limit)
    except Exception as exc:
        logs.log_exception(exc, params={"tool": "search_documentation", "query": query})
        return f"Document search failed: {type(exc).__name__} — {exc}"

    if not results:
        return f"No documents found matching: '{query}'"

    lines = [f"Found {len(results)} document(s):"]
    for doc in results:
        lines.append(f"  - **{doc.title}** ({doc.url})")
        lines.append(f"    {doc.excerpt[:200]}")

    return "\n".join(lines)


async def search_past_tickets(
    *,
    client: searcher.BasePastTicketSearcher | None,
    query: str,
    limit: int = 3,
) -> str:
    """
    Search past resolved tickets for similar issues and their resolutions.

    Return a formatted list of past tickets, or a fallback message
    when the client is unavailable.
    """
    if client is None:
        return "Past ticket searcher not available. Use the tickets already provided."

    try:
        results = await client.search(query=query, limit=limit)
    except Exception as exc:
        logs.log_exception(exc, params={"tool": "search_past_tickets", "query": query})
        return f"Ticket search failed: {type(exc).__name__} — {exc}"

    if not results:
        return f"No past tickets found matching: '{query}'"

    lines = [f"Found {len(results)} resolved ticket(s):"]
    for ticket in results:
        lines.append(f"  - **{ticket.key}**: {ticket.summary}")
        if ticket.resolution:
            lines.append(f"    Resolution: {ticket.resolution[:200]}")
        lines.append(f"    URL: {ticket.url}")

    return "\n".join(lines)


async def check_similar_tickets(
    *,
    client: searcher.BasePastTicketSearcher | None,
    query: str,
    limit: int = 3,
) -> str:
    """
    Check if a similar ticket has been recently submitted or resolved.

    Return a formatted list of similar tickets with resolution status,
    or a fallback message when the client is unavailable.
    """
    if client is None:
        return "Past ticket searcher not available. Classify based on ticket content alone."

    try:
        results = await client.search(query=query, limit=limit)
    except Exception as exc:
        logs.log_exception(exc, params={"tool": "check_similar_tickets", "query": query})
        return f"Ticket search failed: {type(exc).__name__} — {exc}"

    if not results:
        return f"No similar past tickets found for: '{query}'"

    lines = [f"Found {len(results)} similar ticket(s):"]
    for ticket in results:
        resolution_note = (
            f" (resolved: {ticket.resolution[:100]})" if ticket.resolution else " (unresolved)"
        )
        lines.append(f"  - {ticket.key}: {ticket.summary}{resolution_note}")

    return "\n".join(lines)
