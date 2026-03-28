from __future__ import annotations

import contextlib
import datetime

from sentinel.domain.search.searcher import (
    BaseDocumentSearcher,
    DocumentSearchResult,
    UnableToSearchDocumentsError,
)
from sentinel.domain.vendor_adapters.confluence import ConfluenceClient
from sentinel.utils import logs


class ConfluenceDocumentSearcher(BaseDocumentSearcher):
    """
    Searches Confluence pages using CQL and returns sentinel DocumentSearchResults.

    Fetches full page body for the top 3 results so the response drafter has
    rich context, and falls back to the excerpt for the rest.
    """

    def __init__(
        self,
        *,
        client: ConfluenceClient | None = None,
        space_keys: list[str] | None = None,
    ) -> None:
        self._client = client or ConfluenceClient()
        self._space_keys = space_keys or []

    async def search(
        self,
        *,
        query: str,
        limit: int,
    ) -> list[DocumentSearchResult]:
        if not self._client.is_configured:
            raise UnableToSearchDocumentsError("Confluence client is not configured")

        try:
            if self._space_keys:
                space_filter = " OR ".join(f'space="{k}"' for k in self._space_keys)
                cql = f'type=page AND ({space_filter}) AND text~"{query}"'
            else:
                cql = f'type=page AND text~"{query}"'

            raw_results = await self._client.search(cql=cql, limit=limit)
        except Exception as exc:
            raise UnableToSearchDocumentsError(str(exc)) from exc

        results: list[DocumentSearchResult] = []
        for i, item in enumerate(raw_results):
            # Fetch full body for the top 3 hits to give the drafter richer context
            contents: str | None = None
            if i < 3 and item.get("id"):
                page = await self._client.get_page_content(page_id=item["id"])
                if page:
                    contents = page.get("body_text")

            last_edited_at: datetime.datetime | None = None
            raw_ts = item.get("last_modified", "")
            if raw_ts:
                with contextlib.suppress(ValueError):
                    last_edited_at = datetime.datetime.fromisoformat(raw_ts)

            # Rank-based relevance: first result = 1.0, last ≈ 0.0
            relevance = 1.0 - (i / max(len(raw_results), 1))

            results.append(
                DocumentSearchResult(
                    id=item.get("id", ""),
                    title=item.get("title", ""),
                    excerpt=item.get("excerpt", ""),
                    url=item.get("url", ""),
                    relevance=relevance,
                    contents=contents,
                    source_type="confluence",
                    last_edited_at=last_edited_at,
                )
            )

        logs.log_event(
            "confluence_search_completed",
            params={"query": query, "results_count": len(results)},
        )
        return results
