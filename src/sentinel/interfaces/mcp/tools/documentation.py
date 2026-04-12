"""
MCP server tools wrapping Sentinel's documentation search functions.
"""

from __future__ import annotations

import time

from sentinel.domain.search import searcher
from sentinel.domain.tools import documentation as doc_tools
from sentinel.utils import logs


async def search_documentation(
    *,
    document_searcher: searcher.BaseDocumentSearcher | None,
    query: str,
    max_results: int = 5,
) -> str:
    """
    Search documentation across Confluence, Notion, and S3.
    """
    logs.log_event(
        "mcp_tool_invoked",
        params={"tool": "search_documentation", "query": query},
    )
    start = time.monotonic()
    try:
        result = await doc_tools.search_documentation(
            client=document_searcher,
            query=query,
            limit=max_results,
        )
    except Exception as exc:
        logs.log_exception(exc, params={"tool": "search_documentation", "query": query})
        raise
    duration_ms = (time.monotonic() - start) * 1000
    logs.log_event(
        "mcp_tool_completed",
        params={"tool": "search_documentation", "duration_ms": duration_ms},
    )
    return result
