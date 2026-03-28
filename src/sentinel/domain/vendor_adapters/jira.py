from __future__ import annotations

from typing import Any

from jira import JIRA

from sentinel.settings import get_settings
from sentinel.utils import logs


class JiraClient:
    """
    Wraps the Jira SDK for interacting with Jira Service Desk.

    Used by the support pipeline to fetch tickets, post comments,
    and transition issue statuses.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_token: str | None = None,
        user_email: str | None = None,
    ) -> None:
        self._base_url = base_url if base_url is not None else get_settings().jira_base_url
        self._api_token = api_token if api_token is not None else get_settings().jira_api_token
        self._user_email = user_email if user_email is not None else get_settings().jira_user_email

    @property
    def base_url(self) -> str | None:
        return self._base_url

    @property
    def is_configured(self) -> bool:
        return bool(self._base_url and self._api_token and self._user_email)

    def _get_client(self) -> JIRA:
        return JIRA(
            server=self._base_url,
            basic_auth=(self._user_email, self._api_token),
        )

    async def get_issue(self, *, issue_key: str) -> dict[str, Any] | None:
        """
        Fetch a Jira issue by its key (e.g. SUPPORT-42).

        Returns:
            Dict with keys: id, key, summary, description, status, priority,
            reporter, assignee, labels, created, updated.
        """
        if not self.is_configured:
            logs.log_event("jira_get_issue_skipped", params={"reason": "Not configured"})
            return None

        try:
            client = self._get_client()
            issue = client.issue(issue_key)

            return {
                "id": issue.id,
                "key": issue.key,
                "summary": issue.fields.summary,
                "description": issue.fields.description or "",
                "status": str(issue.fields.status),
                "priority": str(issue.fields.priority) if issue.fields.priority else "None",
                "reporter": str(issue.fields.reporter) if issue.fields.reporter else "Unknown",
                "assignee": str(issue.fields.assignee) if issue.fields.assignee else None,
                "labels": issue.fields.labels or [],
                "created": str(issue.fields.created),
                "updated": str(issue.fields.updated),
            }

        except Exception as e:
            logs.log_exception(e, params={"issue_key": issue_key})
            return None

    async def search_issues(
        self,
        *,
        jql: str,
        max_results: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Search for Jira issues using JQL.

        Args:
            jql: JQL query string (e.g. "project = SUPPORT AND status = Resolved")
            max_results: Maximum number of results.

        Returns:
            List of issue dicts (same structure as get_issue).
        """
        if not self.is_configured:
            logs.log_event("jira_search_skipped", params={"reason": "Not configured"})
            return []

        try:
            client = self._get_client()
            issues = client.search_issues(jql, maxResults=max_results)

            results: list[dict[str, Any]] = []
            for issue in issues:
                results.append(
                    {
                        "id": issue.id,
                        "key": issue.key,
                        "summary": issue.fields.summary,
                        "description": issue.fields.description or "",
                        "status": str(issue.fields.status),
                        "priority": str(issue.fields.priority)
                        if issue.fields.priority
                        else "None",
                        "reporter": str(issue.fields.reporter)
                        if issue.fields.reporter
                        else "Unknown",
                        "labels": issue.fields.labels or [],
                        "created": str(issue.fields.created),
                    }
                )

            logs.log_event(
                "jira_search_completed",
                params={"jql": jql, "results_count": len(results)},
            )
            return results

        except Exception as e:
            logs.log_exception(e, params={"jql": jql})
            return []

    async def add_internal_comment(
        self,
        *,
        issue_key: str,
        body: str,
    ) -> bool:
        """
        Post an internal comment on a Jira issue.

        For Jira Service Desk, this posts a comment visible only to agents.

        Args:
            issue_key: The Jira issue key (e.g. "SUPPORT-42").
            body: Comment body text.

        Returns:
            True if successful, False otherwise.
        """
        if not self.is_configured:
            logs.log_event(
                "jira_comment_skipped",
                params={"reason": "Not configured", "issue_key": issue_key},
            )
            return False

        try:
            client = self._get_client()
            # Use the visibility property for Service Desk internal comments
            client.add_comment(
                issue_key,
                body,
                visibility={"type": "role", "value": "Service Desk Team"},
            )

            logs.log_event(
                "jira_comment_added",
                params={"issue_key": issue_key},
            )
            return True

        except Exception as e:
            logs.log_exception(e, params={"issue_key": issue_key})
            return False

    async def transition_issue(
        self,
        *,
        issue_key: str,
        transition_name: str,
    ) -> bool:
        """
        Transition a Jira issue to a new status.

        Args:
            issue_key: The Jira issue key.
            transition_name: The name of the transition (e.g. "In Progress", "Done").

        Returns:
            True if successful, False otherwise.
        """
        if not self.is_configured:
            logs.log_event(
                "jira_transition_skipped",
                params={"reason": "Not configured", "issue_key": issue_key},
            )
            return False

        try:
            client = self._get_client()
            transitions = client.transitions(issue_key)

            target = next(
                (t for t in transitions if t["name"].lower() == transition_name.lower()),
                None,
            )

            if not target:
                logs.log_event(
                    "jira_transition_not_found",
                    params={
                        "issue_key": issue_key,
                        "transition_name": transition_name,
                        "available": [t["name"] for t in transitions],
                    },
                )
                return False

            client.transition_issue(issue_key, target["id"])
            logs.log_event(
                "jira_issue_transitioned",
                params={"issue_key": issue_key, "transition": transition_name},
            )
            return True

        except Exception as e:
            logs.log_exception(e, params={"issue_key": issue_key})
            return False

    def format_suggestion_comment(
        self,
        *,
        suggested_response: str,
        confidence_label: str | None,
        category: str | None,
        sources: list[dict[str, str]] | None = None,
    ) -> str:
        """Format a support suggestion into a Jira internal comment."""
        parts = ["h3. Sentinel Response Suggestion\n"]

        if category:
            parts.append(f"*Category:* {category}")
        if confidence_label:
            parts.append(f"*Confidence:* {confidence_label}")

        parts.append(f"\nh3. Suggested Response\n{suggested_response}")

        if sources:
            parts.append("\nh3. Sources")
            for source in sources:
                title = source.get("title", "Unknown")
                url = source.get("url", "")
                if url:
                    parts.append(f"* [{title}|{url}]")
                else:
                    parts.append(f"* {title}")

        return "\n".join(parts)
