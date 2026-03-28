from __future__ import annotations

import html
import re
from typing import Any

from atlassian import Confluence

from sentinel.settings import get_settings
from sentinel.utils import logs


class ConfluenceClient:
    """
    Wraps the atlassian-python-api SDK for searching and retrieving Confluence pages.

    Used by the support pipeline's document search to find relevant articles.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_token: str | None = None,
        user_email: str | None = None,
    ) -> None:
        self._base_url = base_url if base_url is not None else get_settings().confluence_base_url
        self._api_token = api_token if api_token is not None else get_settings().jira_api_token
        self._user_email = user_email if user_email is not None else get_settings().jira_user_email

    @property
    def is_configured(self) -> bool:
        return bool(self._base_url and self._api_token and self._user_email)

    def _get_client(self) -> Confluence:
        return Confluence(  # type: ignore[no-untyped-call]
            url=self._base_url,
            username=self._user_email,
            password=self._api_token,
        )

    async def search(
        self,
        cql: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Search Confluence using CQL (Confluence Query Language).

        Args:
            cql: CQL query (e.g. 'type=page AND text~"login issue"')
            limit: Maximum number of results.

        Returns:
            List of page dicts with keys: id, title, url, space, excerpt.
        """
        if not self.is_configured:
            logs.log_event("confluence_search_skipped", params={"reason": "Not configured"})
            return []

        try:
            client = self._get_client()
            response = client.cql(cql, limit=limit)  # type: ignore[no-untyped-call]
            results_data = response.get("results", [])

            results: list[dict[str, Any]] = []
            for item in results_data:
                content = item.get("content", {}) or item
                space = content.get("space", {}) or {}
                links = content.get("_links", {}) or {}

                page_url = ""
                if links.get("webui"):
                    page_url = f"{self._base_url}{links['webui']}"

                results.append(
                    {
                        "id": content.get("id", ""),
                        "title": content.get("title", ""),
                        "url": page_url,
                        "space": space.get("name", ""),
                        "space_key": space.get("key", ""),
                        "excerpt": item.get("excerpt", ""),
                        "last_modified": item.get("lastModified", ""),
                    }
                )

            logs.log_event(
                "confluence_search_completed",
                params={"cql": cql, "results_count": len(results)},
            )
            return results

        except Exception as e:
            logs.log_exception(e, params={"cql": cql})
            return []

    async def get_page_content(
        self,
        *,
        page_id: str,
    ) -> dict[str, Any] | None:
        """
        Fetch a Confluence page and return its content as plain text.

        Args:
            page_id: The Confluence page ID.

        Returns:
            Dict with keys: id, title, url, space, body_text, last_modified.
        """
        if not self.is_configured:
            return None

        try:
            client = self._get_client()
            page = client.get_page_by_id(page_id, expand="body.storage,space,version")  # type: ignore[no-untyped-call]

            body_html = page.get("body", {}).get("storage", {}).get("value", "")
            body_text = _html_to_plain_text(body_html)

            links = page.get("_links", {}) or {}
            page_url = ""
            if links.get("webui"):
                page_url = f"{self._base_url}{links['webui']}"

            space = page.get("space", {}) or {}
            version = page.get("version", {}) or {}

            return {
                "id": page.get("id", ""),
                "title": page.get("title", ""),
                "url": page_url,
                "space": space.get("name", ""),
                "body_text": body_text,
                "last_modified": version.get("when", ""),
            }

        except Exception as e:
            logs.log_exception(e, params={"page_id": page_id})
            return None

    async def search_in_space(
        self,
        *,
        space_key: str,
        text: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Search for pages containing text within a specific Confluence space.

        Args:
            space_key: The space key (e.g. "DOCS", "KB").
            text: The search text.
            limit: Maximum results.

        Returns:
            List of page dicts (same structure as search).
        """
        cql = f'type=page AND space="{space_key}" AND text~"{text}"'
        return await self.search(cql=cql, limit=limit)


def _html_to_plain_text(html_content: str) -> str:
    """Convert Confluence storage format HTML to plain text."""
    # Remove common Confluence macros
    text = re.sub(
        r"<ac:structured-macro[^>]*>.*?</ac:structured-macro>", "", html_content, flags=re.DOTALL
    )

    # Replace <br/> and block-level elements with newlines
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"</?(p|div|h[1-6]|li|tr|td|th|table|ul|ol|blockquote)[^>]*>", "\n", text)

    # Remove remaining HTML tags
    text = re.sub(r"<[^>]+>", "", text)

    # Decode HTML entities
    text = html.unescape(text)

    # Normalise whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
