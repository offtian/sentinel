from __future__ import annotations

from sentinel.domain.search.searcher import (
    BasePastTicketSearcher,
    TicketSearchResult,
    UnableToSearchTicketsError,
)
from sentinel.domain.vendor_adapters.jira import JiraClient
from sentinel.utils import logs


class JiraPastTicketSearcher(BasePastTicketSearcher):
    """
    Searches resolved Jira tickets to find past solutions for similar issues.

    Only queries tickets in Done/Resolved statusCategory so the drafter
    only sees tickets that were actually fixed, not open noise.
    """

    def __init__(
        self,
        *,
        client: JiraClient | None = None,
        project_keys: list[str] | None = None,
    ) -> None:
        self._client = client or JiraClient()
        self._project_keys = project_keys or []

    async def search(
        self,
        *,
        query: str,
        limit: int,
    ) -> list[TicketSearchResult]:
        if not self._client.is_configured:
            raise UnableToSearchTicketsError("Jira client is not configured")

        try:
            project_filter = ""
            if self._project_keys:
                keys = ", ".join(f'"{k}"' for k in self._project_keys)
                project_filter = f"project in ({keys}) AND "

            jql = (
                f'{project_filter}text ~ "{query}" AND statusCategory = Done ORDER BY updated DESC'
            )
            raw_results = await self._client.search_issues(jql=jql, max_results=limit)
        except Exception as exc:
            raise UnableToSearchTicketsError(str(exc)) from exc

        results = [
            TicketSearchResult(
                id=item["id"],
                key=item["key"],
                summary=item["summary"],
                description=item.get("description", ""),
                resolution=item.get("status"),
                url=f"{self._client.base_url}/browse/{item['key']}",
                relevance=1.0 - (i / max(len(raw_results), 1)),
            )
            for i, item in enumerate(raw_results)
        ]

        logs.log_event(
            "jira_past_ticket_search_completed",
            params={"query": query, "results_count": len(results)},
        )
        return results
