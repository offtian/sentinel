from __future__ import annotations

from sentinel.domain.search.searcher import (
    BaseMetricsSearcher,
    MetricsSearchResult,
    UnableToSearchMetricsError,
)
from sentinel.domain.vendor_adapters.observability import DatadogClient
from sentinel.utils import logs


class DatadogMetricsSearcher(BaseMetricsSearcher):
    """
    Queries Datadog logs and metrics to gather observability context for SRE investigations.

    Runs both a log query and a metric query against the same search term and returns
    a summary of each as MetricsSearchResults so the root cause analyser has raw signal.
    """

    def __init__(self, *, client: DatadogClient | None = None) -> None:
        self._client = client or DatadogClient()

    async def search(
        self,
        *,
        query: str,
        time_range_minutes: int,
        limit: int,
    ) -> list[MetricsSearchResult]:
        if not self._client.is_configured:
            raise UnableToSearchMetricsError("Datadog client is not configured")

        try:
            logs_raw = await self._client.query_logs(
                query=query,
                time_range_minutes=time_range_minutes,
                limit=limit,
            )
            metrics_raw = await self._client.query_metrics(
                query=query,
                time_range_minutes=time_range_minutes,
            )
        except Exception as exc:
            raise UnableToSearchMetricsError(str(exc)) from exc

        results: list[MetricsSearchResult] = []

        if logs_raw:
            log_lines = "\n".join(
                entry.get("message", "") for entry in logs_raw[:10] if entry.get("message")
            )
            results.append(
                MetricsSearchResult(
                    source="datadog_logs",
                    query=query,
                    summary=log_lines
                    or f"{len(logs_raw)} log entries returned (no messages extracted)",
                    raw_data=str(logs_raw),
                    relevance=0.9,
                )
            )

        if metrics_raw:
            series_names = ", ".join(
                s.get("metric", "") for s in metrics_raw[:5] if s.get("metric")
            )
            results.append(
                MetricsSearchResult(
                    source="datadog_metrics",
                    query=query,
                    summary=f"{len(metrics_raw)} metric series: {series_names}",
                    raw_data=str(metrics_raw),
                    relevance=0.8,
                )
            )

        logs.log_event(
            "datadog_metrics_search_completed",
            params={"query": query, "results_count": len(results)},
        )
        return results
