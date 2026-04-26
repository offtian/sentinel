"""
Grafana unified observability client (open-source).

Queries Prometheus (metrics), Loki (logs), and Tempo (traces) through
Grafana's Datasource HTTP API — available in both Grafana OSS (free,
self-hosted) and Grafana Cloud.

Requires:
  - ``GRAFANA_URL`` — e.g. ``http://grafana:3000`` (local) or ``https://grafana.internal``
  - Datasource UIDs for Prometheus, Loki, and Tempo

Optional:
  - ``GRAFANA_API_TOKEN`` — a Service Account token with Viewer role.
    Not required when Grafana has anonymous auth enabled (local dev default).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from sentinel.domain.vendor_adapters.observability.base import BaseObservabilityClient
from sentinel.settings import settings
from sentinel.utils import logs


class GrafanaClient(BaseObservabilityClient):
    """
    Query Prometheus, Loki, and Tempo via Grafana's unified HTTP API.

    This is the standard open-source alternative to DatadogClient.
    Most teams running the Prometheus/Loki/Tempo stack already have
    Grafana as their query frontend.
    """

    def __init__(
        self,
        *,
        grafana_url: str | None = None,
        api_token: str | None = None,
        prometheus_datasource_uid: str | None = None,
        loki_datasource_uid: str | None = None,
        tempo_datasource_uid: str | None = None,
    ) -> None:
        self._grafana_url = (grafana_url or settings.grafana_url).rstrip("/")
        self._api_token = api_token or settings.grafana_api_token
        self._prometheus_uid = (
            prometheus_datasource_uid or settings.grafana_prometheus_datasource_uid
        )
        self._loki_uid = loki_datasource_uid or settings.grafana_loki_datasource_uid
        self._tempo_uid = tempo_datasource_uid or settings.grafana_tempo_datasource_uid

    @property
    def is_configured(self) -> bool:
        return bool(self._grafana_url)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"
        return headers

    # -- Logs (Loki via Grafana) -----------------------------------------------

    async def query_logs(
        self,
        *,
        query: str,
        time_range_minutes: int = 60,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Query Loki logs through Grafana's datasource proxy.

        Uses ``/api/ds/query`` with the Loki datasource UID.
        """
        if not self.is_configured or not self._loki_uid:
            logs.log_event("grafana_logs_skipped", params={"reason": "Not configured"})
            return []

        now = datetime.now(tz=UTC)
        time_from = now - timedelta(minutes=time_range_minutes)

        payload = {
            "queries": [
                {
                    "refId": "A",
                    "datasource": {"uid": self._loki_uid, "type": "loki"},
                    "expr": query,
                    "maxLines": limit,
                },
            ],
            "from": str(int(time_from.timestamp() * 1000)),
            "to": str(int(now.timestamp() * 1000)),
        }

        raw_frames = await self._ds_query(payload, label="logs")
        return self._parse_log_frames(raw_frames)

    # -- Metrics (Prometheus via Grafana) --------------------------------------

    async def query_metrics(
        self,
        *,
        query: str,
        time_range_minutes: int = 60,
    ) -> list[dict[str, Any]]:
        """
        Query Prometheus metrics through Grafana's datasource proxy.

        Uses ``/api/ds/query`` with the Prometheus datasource UID.
        """
        if not self.is_configured or not self._prometheus_uid:
            logs.log_event("grafana_metrics_skipped", params={"reason": "Not configured"})
            return []

        now = datetime.now(tz=UTC)
        time_from = now - timedelta(minutes=time_range_minutes)
        step_seconds = max(time_range_minutes * 60 // 200, 15)

        payload = {
            "queries": [
                {
                    "refId": "A",
                    "datasource": {"uid": self._prometheus_uid, "type": "prometheus"},
                    "expr": query,
                    "intervalMs": step_seconds * 1000,
                    "maxDataPoints": 200,
                },
            ],
            "from": str(int(time_from.timestamp() * 1000)),
            "to": str(int(now.timestamp() * 1000)),
        }

        raw_frames = await self._ds_query(payload, label="metrics")
        return self._parse_metric_frames(raw_frames)

    # -- Traces (Tempo via Grafana) --------------------------------------------

    async def query_traces(
        self,
        *,
        query: str,
        time_range_minutes: int = 60,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Query Tempo traces through Grafana's datasource proxy.

        Uses ``/api/ds/query`` with the Tempo datasource UID and TraceQL.
        """
        if not self.is_configured or not self._tempo_uid:
            logs.log_event("grafana_traces_skipped", params={"reason": "Not configured"})
            return []

        now = datetime.now(tz=UTC)
        time_from = now - timedelta(minutes=time_range_minutes)

        payload = {
            "queries": [
                {
                    "refId": "A",
                    "datasource": {"uid": self._tempo_uid, "type": "tempo"},
                    "query": query,
                    "limit": limit,
                },
            ],
            "from": str(int(time_from.timestamp() * 1000)),
            "to": str(int(now.timestamp() * 1000)),
        }

        raw_frames = await self._ds_query(payload, label="traces")
        return self._parse_trace_frames(raw_frames)

    # -- Query templates (LogQL / PromQL / TraceQL) ----------------------------

    def log_query_template(self, *, service: str) -> str:
        """LogQL query for error logs from a service."""
        return f'{{service="{service}"}} |= "error" | logfmt'

    def metrics_query_template(self, *, service: str) -> str:
        """PromQL query for CPU usage by service."""
        return f'avg(rate(process_cpu_seconds_total{{service="{service}"}}[5m]))'

    def trace_query_template(self, *, service: str) -> str:
        """TraceQL query for error spans from a service."""
        return f'{{resource.service.name="{service}" && status=error}}'

    # -- Grafana Datasource API ------------------------------------------------

    async def _ds_query(
        self,
        payload: dict[str, Any],
        *,
        label: str,
    ) -> list[dict[str, Any]]:
        """
        Execute a query against Grafana's unified ``/api/ds/query`` endpoint.

        Return the list of ``results`` frames from the response, or an empty
        list on failure.
        """
        url = f"{self._grafana_url}/api/ds/query"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    url,
                    headers=self._headers(),
                    content=json.dumps(payload),
                )
                response.raise_for_status()

            data = response.json()
            frames: list[dict[str, Any]] = data.get("results", {}).get("A", {}).get("frames", [])

            logs.log_event(
                f"grafana_{label}_queried",
                params={"frame_count": len(frames)},
            )
            return frames

        except Exception as e:
            logs.log_exception(e, params={"label": label})
            return []

    # -- Response parsers ------------------------------------------------------

    @staticmethod
    def _parse_log_frames(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Parse Loki log frames into the standardised log entry shape.

        Grafana returns log data as data frames with ``values`` arrays.
        """
        results: list[dict[str, Any]] = []
        for frame in frames:
            data = frame.get("data", {})
            values_list = data.get("values", [])
            labels = frame.get("schema", {}).get("fields", [{}])[0].get("labels", {})
            service = labels.get("service", labels.get("app", ""))

            # Loki frames: values[0] = timestamps, values[1] = log lines
            if len(values_list) >= 2:
                timestamps = values_list[0]
                messages = values_list[1]
                for ts, msg in zip(timestamps, messages, strict=False):
                    results.append(
                        {
                            "timestamp": str(ts),
                            "message": str(msg)[:500],
                            "service": service,
                            "status": "error",
                        }
                    )
        return results

    @staticmethod
    def _parse_metric_frames(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Parse Prometheus metric frames into the standardised metric shape.

        Grafana returns time-series as data frames with ``values`` arrays.
        """
        results: list[dict[str, Any]] = []
        for frame in frames:
            schema = frame.get("schema", {})
            data = frame.get("data", {})
            values_list = data.get("values", [])

            name = schema.get("name", "unknown")
            labels = {}
            for field in schema.get("fields", []):
                labels.update(field.get("labels", {}))

            points: list[list[float]] = []
            if len(values_list) >= 2:
                timestamps = values_list[0]
                values = values_list[1]
                points = [
                    [float(t), float(v)]
                    for t, v in zip(timestamps, values, strict=False)
                    if v is not None
                ]

            results.append(
                {
                    "metric": name,
                    "scope": ", ".join(f"{k}={v}" for k, v in labels.items()),
                    "points": points,
                }
            )
        return results

    @staticmethod
    def _parse_trace_frames(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Parse Tempo trace frames into the standardised span shape.

        Tempo search results come as table-format data frames.
        """
        results: list[dict[str, Any]] = []
        for frame in frames:
            data = frame.get("data", {})
            schema_fields = frame.get("schema", {}).get("fields", [])
            values_list = data.get("values", [])

            if not schema_fields or not values_list:
                continue

            field_names = [f.get("name", "") for f in schema_fields]
            row_count = len(values_list[0]) if values_list else 0

            for row_idx in range(row_count):
                row = {
                    field_names[col_idx]: values_list[col_idx][row_idx]
                    for col_idx in range(len(field_names))
                    if col_idx < len(values_list)
                }
                results.append(
                    {
                        "trace_id": row.get("traceID", ""),
                        "span_id": row.get("spanID", ""),
                        "service": row.get("serviceName", row.get("rootServiceName", "")),
                        "resource": row.get("rootTraceName", ""),
                        "status": row.get("status", ""),
                        "duration_ns": int(row.get("duration", 0)) * 1_000_000,
                    }
                )
        return results
