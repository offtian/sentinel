from __future__ import annotations

from sentinel.domain.search import searcher as searcher_module
from sentinel.settings import get_settings


def build_document_searcher() -> searcher_module.BaseDocumentSearcher | None:
    """
    Return the configured document searcher, or None if unconfigured.

    Controlled by the DOCUMENT_SEARCHER env var:
      "confluence"  — uses ConfluenceClient (requires CONFLUENCE_BASE_URL + JIRA_API_TOKEN)
      "mock"        — returns static canned results; safe for local/offline dev
      anything else — returns None (document search is skipped in the pipeline)
    """
    backend = get_settings().document_searcher

    if backend == "mock":
        from sentinel.domain.search.mock_searchers import MockDocumentSearcher

        return MockDocumentSearcher()

    if backend == "confluence":
        from sentinel.domain.search.confluence_searcher import ConfluenceDocumentSearcher
        from sentinel.domain.vendor_adapters.confluence import ConfluenceClient

        client = ConfluenceClient()
        if not client.is_configured:
            return None

        space_keys = [
            k.strip() for k in get_settings().confluence_space_keys.split(",") if k.strip()
        ]
        return ConfluenceDocumentSearcher(client=client, space_keys=space_keys or None)

    # "bedrock_knowledge_base" or any unrecognised value: skip doc search
    return None


def build_ticket_searcher() -> searcher_module.BasePastTicketSearcher | None:
    """
    Return the configured past-ticket searcher, or None if Jira is unconfigured.

    When DOCUMENT_SEARCHER=mock the ticket searcher is also mocked so local dev
    gets a fully self-contained pipeline without needing real Jira credentials.
    """
    if get_settings().document_searcher == "mock":
        from sentinel.domain.search.mock_searchers import MockPastTicketSearcher

        return MockPastTicketSearcher()

    from sentinel.domain.vendor_adapters.jira import JiraClient

    client = JiraClient()
    if not client.is_configured:
        return None

    from sentinel.domain.search.jira_ticket_searcher import JiraPastTicketSearcher

    project_keys = [k.strip() for k in get_settings().jira_project_keys.split(",") if k.strip()]
    return JiraPastTicketSearcher(client=client, project_keys=project_keys or None)


def build_metrics_searcher() -> searcher_module.BaseMetricsSearcher | None:
    """
    Return a DatadogMetricsSearcher if Datadog is configured, a mock if in mock mode,
    otherwise None.
    """
    if get_settings().document_searcher == "mock":
        from sentinel.domain.search.mock_searchers import MockMetricsSearcher

        return MockMetricsSearcher()

    from sentinel.domain.vendor_adapters.datadog_client import DatadogClient

    client = DatadogClient()
    if not client.is_configured:
        return None

    from sentinel.domain.search.datadog_metrics_searcher import DatadogMetricsSearcher

    return DatadogMetricsSearcher(client=client)
