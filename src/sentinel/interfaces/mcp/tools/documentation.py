"""
MCP server tools wrapping Sentinel's documentation search functions.
"""

from __future__ import annotations

from sentinel.domain.search import searcher
from sentinel.domain.tools import documentation as doc_tools
from sentinel.interfaces.mcp.tools import _helpers


async def search_documentation(
    *,
    document_searcher: searcher.BaseDocumentSearcher | None,
    query: str,
    max_results: int = 5,
) -> str:
    """
    Search documentation across Confluence, Notion, and S3.
    """
    return await _helpers.instrumented_mcp_tool(
        tool_name="search_documentation",
        params={"query": query},
        fn=lambda: doc_tools.search_documentation(
            client=document_searcher,
            query=query,
            limit=max_results,
        ),
    )
