from __future__ import annotations

import datetime

from sentinel.domain.search.searcher import (
    BaseDocumentSearcher,
    BaseMetricsSearcher,
    BasePastTicketSearcher,
    DocumentSearchResult,
    MetricsSearchResult,
    TicketSearchResult,
)


class MockDocumentSearcher(BaseDocumentSearcher):
    """
    Returns static canned docs — no external calls.
    Used when DOCUMENT_SEARCHER=mock or in tests that don't need real Confluence.
    Pass custom results to override the defaults.
    """

    def __init__(self, *, results: list[DocumentSearchResult] | None = None) -> None:
        self._results = results or [
            DocumentSearchResult(
                id="mock-doc-1",
                title="How to reset your account password",
                excerpt=(
                    "Navigate to the login page and click 'Forgot password' "
                    "to receive a reset link by email."
                ),
                url="https://docs.example.com/account/password-reset",
                relevance=0.95,
                contents=(
                    "## Password Reset\n\n"
                    "1. Go to the login page.\n"
                    "2. Click **Forgot password**.\n"
                    "3. Enter your email address.\n"
                    "4. Check your inbox for a reset link (valid for 24 hours).\n"
                    "5. If the link returns a 404, ensure you are not using a cached or "
                    "expired URL — request a fresh link."
                ),
                source_type="confluence",
                last_edited_at=datetime.datetime(2025, 11, 1, tzinfo=datetime.UTC),
            ),
            DocumentSearchResult(
                id="mock-doc-2",
                title="Account troubleshooting guide",
                excerpt="Common account issues and how to resolve them for end users.",
                url="https://docs.example.com/account/troubleshooting",
                relevance=0.75,
                contents=(
                    "## Common Account Issues\n\n"
                    "- **Cannot log in**: Check caps-lock, try password reset.\n"
                    "- **Reset link 404**: Request a new link; links expire after 24 hours.\n"
                    "- **MFA not working**: Use backup codes or contact support.\n"
                ),
                source_type="confluence",
                last_edited_at=datetime.datetime(2025, 10, 15, tzinfo=datetime.UTC),
            ),
        ]

    async def search(self, *, query: str, limit: int) -> list[DocumentSearchResult]:
        return self._results[:limit]


class MockPastTicketSearcher(BasePastTicketSearcher):
    """
    Returns static canned resolved tickets — no external calls.
    Used when DOCUMENT_SEARCHER=mock or in tests.
    """

    def __init__(self, *, results: list[TicketSearchResult] | None = None) -> None:
        self._results = results or [
            TicketSearchResult(
                id="mock-ticket-1",
                key="SUPPORT-88",
                summary="Password reset link returns 404",
                description=(
                    "User reported that clicking the reset link sent by email "
                    "returns a 404 error page. Issue was reproducible on all browsers."
                ),
                resolution=(
                    "Fixed — reset links now include the correct base URL "
                    "after the domain migration in Nov 2025."
                ),
                url="https://jira.example.com/browse/SUPPORT-88",
                relevance=0.9,
            ),
        ]

    async def search(self, *, query: str, limit: int) -> list[TicketSearchResult]:
        return self._results[:limit]


class MockMetricsSearcher(BaseMetricsSearcher):
    """
    Returns static canned observability data — no external calls.
    Used when Datadog is not available locally.
    """

    def __init__(self, *, results: list[MetricsSearchResult] | None = None) -> None:
        self._results = results or [
            MetricsSearchResult(
                source="mock_datadog_logs",
                query="",
                summary="No anomalies detected in the last 30 minutes. All services healthy.",
                raw_data='{"status": "ok", "entries": []}',
                relevance=0.5,
            ),
        ]

    async def search(
        self,
        *,
        query: str,
        time_range_minutes: int,
        limit: int,
    ) -> list[MetricsSearchResult]:
        return self._results[:limit]
