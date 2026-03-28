from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sentinel.settings import get_settings
from sentinel.utils import logs


class DatadogClient:
    """
    Wraps the datadog-api-client SDK for querying logs, metrics, and traces.

    Used by the SRE investigation pipeline to gather observability context.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        app_key: str | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else get_settings().datadog_api_key
        self._app_key = app_key if app_key is not None else get_settings().datadog_app_key

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key and self._app_key)

    def _get_configuration(self) -> Any:
        from datadog_api_client import Configuration

        configuration = Configuration()  # type: ignore[no-untyped-call]
        configuration.api_key["apiKeyAuth"] = self._api_key
        configuration.api_key["appKeyAuth"] = self._app_key
        return configuration

    async def query_logs(
        self,
        *,
        query: str,
        time_range_minutes: int = 60,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Search Datadog logs using the v2 API.

        Args:
            query: Datadog log query string (e.g. "service:api-gateway status:error")
            time_range_minutes: How far back to search.
            limit: Maximum number of log entries to return.

        Returns:
            List of log entry dicts with keys: timestamp, message, service, status, attributes.
        """
        if not self.is_configured:
            logs.log_event("datadog_logs_skipped", params={"reason": "Not configured"})
            return []

        from datadog_api_client import ApiClient
        from datadog_api_client.v2.api.logs_api import LogsApi
        from datadog_api_client.v2.model.logs_list_request import LogsListRequest
        from datadog_api_client.v2.model.logs_list_request_page import LogsListRequestPage
        from datadog_api_client.v2.model.logs_query_filter import LogsQueryFilter
        from datadog_api_client.v2.model.logs_sort import LogsSort

        now = datetime.now(tz=UTC)
        time_from = now - timedelta(minutes=time_range_minutes)

        body = LogsListRequest(
            filter=LogsQueryFilter(
                query=query,
                _from=time_from.isoformat(),
                to=now.isoformat(),
            ),
            sort=LogsSort.TIMESTAMP_DESCENDING,
            page=LogsListRequestPage(limit=limit),
        )

        try:
            with ApiClient(self._get_configuration()) as api_client:
                api = LogsApi(api_client)  # type: ignore[no-untyped-call]
                response = api.list_logs(body=body)

            results: list[dict[str, Any]] = []
            for log_entry in response.data or []:
                attrs = log_entry.attributes
                results.append(
                    {
                        "timestamp": str(attrs.get("timestamp", "")),
                        "message": attrs.get("message", ""),
                        "service": attrs.get("service", ""),
                        "status": attrs.get("status", ""),
                        "attributes": attrs.get("attributes", {}),
                    }
                )

            logs.log_event(
                "datadog_logs_queried",
                params={"query": query, "results_count": len(results)},
            )
            return results

        except Exception as e:
            logs.log_exception(e, params={"query": query})
            return []

    async def query_metrics(
        self,
        *,
        query: str,
        time_range_minutes: int = 60,
    ) -> list[dict[str, Any]]:
        """
        Query Datadog metrics using the v1 timeseries API.

        Args:
            query: Datadog metric query (e.g. "avg:system.cpu.user{service:api-gateway}")
            time_range_minutes: How far back to query.

        Returns:
            List of metric series dicts with keys: metric, scope, points.
        """
        if not self.is_configured:
            logs.log_event("datadog_metrics_skipped", params={"reason": "Not configured"})
            return []

        from datadog_api_client import ApiClient
        from datadog_api_client.v1.api.metrics_api import MetricsApi

        now = datetime.now(tz=UTC)
        time_from = now - timedelta(minutes=time_range_minutes)

        try:
            with ApiClient(self._get_configuration()) as api_client:
                api = MetricsApi(api_client)  # type: ignore[no-untyped-call]
                response = api.query_metrics(
                    _from=int(time_from.timestamp()),
                    to=int(now.timestamp()),
                    query=query,
                )

            results: list[dict[str, Any]] = []
            for series in response.series or []:
                results.append(
                    {
                        "metric": series.get("metric", ""),
                        "scope": series.get("scope", ""),
                        "points": series.get("pointlist", []),
                    }
                )

            logs.log_event(
                "datadog_metrics_queried",
                params={"query": query, "series_count": len(results)},
            )
            return results

        except Exception as e:
            logs.log_exception(e, params={"query": query})
            return []

    async def query_traces(
        self,
        *,
        query: str,
        time_range_minutes: int = 60,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Search Datadog APM spans/traces using the v2 API.

        Args:
            query: Span search query (e.g. "service:api-gateway @http.status_code:500")
            time_range_minutes: How far back to search.
            limit: Maximum number of spans to return.

        Returns:
            List of span dicts with keys: trace_id, span_id, service, resource, status, duration_ns.
        """
        if not self.is_configured:
            logs.log_event("datadog_traces_skipped", params={"reason": "Not configured"})
            return []

        from datadog_api_client import ApiClient
        from datadog_api_client.v2.api.spans_api import SpansApi
        from datadog_api_client.v2.model.spans_list_request import SpansListRequest
        from datadog_api_client.v2.model.spans_list_request_page import SpansListRequestPage
        from datadog_api_client.v2.model.spans_query_filter import SpansQueryFilter
        from datadog_api_client.v2.model.spans_sort import SpansSort

        now = datetime.now(tz=UTC)
        time_from = now - timedelta(minutes=time_range_minutes)

        body = SpansListRequest(
            data={  # type: ignore[arg-type]
                "type": "search_request",
                "attributes": {
                    "filter": SpansQueryFilter(
                        query=query,
                        _from=time_from.isoformat(),
                        to=now.isoformat(),
                    ),
                    "sort": SpansSort.TIMESTAMP_DESCENDING,
                    "page": SpansListRequestPage(limit=limit),
                },
            },
        )

        try:
            with ApiClient(self._get_configuration()) as api_client:
                api = SpansApi(api_client)  # type: ignore[no-untyped-call]
                response = api.list_spans(body=body)

            results: list[dict[str, Any]] = []
            for span in response.data or []:
                attrs = span.attributes
                results.append(
                    {
                        "trace_id": attrs.get("trace_id", ""),
                        "span_id": attrs.get("span_id", ""),
                        "service": attrs.get("service", ""),
                        "resource": attrs.get("resource_name", ""),
                        "status": attrs.get("status", ""),
                        "duration_ns": attrs.get("duration", 0),
                    }
                )

            logs.log_event(
                "datadog_traces_queried",
                params={"query": query, "results_count": len(results)},
            )
            return results

        except Exception as e:
            logs.log_exception(e, params={"query": query})
            return []

    async def get_monitor(self, *, monitor_id: int) -> dict[str, Any] | None:
        """Fetch details for a specific Datadog monitor."""
        if not self.is_configured:
            return None

        from datadog_api_client import ApiClient
        from datadog_api_client.v1.api.monitors_api import MonitorsApi

        try:
            with ApiClient(self._get_configuration()) as api_client:
                api = MonitorsApi(api_client)  # type: ignore[no-untyped-call]
                response = api.get_monitor(monitor_id=monitor_id)

            return {
                "id": response.get("id"),  # type: ignore[no-untyped-call]
                "name": response.get("name", ""),  # type: ignore[no-untyped-call]
                "type": response.get("type", ""),  # type: ignore[no-untyped-call]
                "query": response.get("query", ""),  # type: ignore[no-untyped-call]
                "message": response.get("message", ""),  # type: ignore[no-untyped-call]
                "tags": response.get("tags", []),  # type: ignore[no-untyped-call]
                "overall_state": response.get("overall_state", ""),  # type: ignore[no-untyped-call]
            }

        except Exception as e:
            logs.log_exception(e, params={"monitor_id": monitor_id})
            return None
