"""
Abstract base class for observability backends.

All observability clients (Datadog, Grafana/Prometheus/Loki/Tempo, etc.)
implement this interface so the investigation pipeline is backend-agnostic.
"""

from __future__ import annotations

import abc
from typing import Any


class BaseObservabilityClient(abc.ABC):
    """
    Uniform interface for querying logs, metrics, and traces.

    Each backend translates its native query language and response format
    into the standardised dict shapes described below.
    """

    @property
    @abc.abstractmethod
    def is_configured(self) -> bool:
        """Return True when credentials and endpoints are present."""

    @abc.abstractmethod
    async def query_logs(
        self,
        *,
        query: str,
        time_range_minutes: int = 60,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Search logs for the given query.

        Return a list of dicts, each with at least:
        ``timestamp``, ``message``, ``service``, ``status``.
        """

    @abc.abstractmethod
    async def query_metrics(
        self,
        *,
        query: str,
        time_range_minutes: int = 60,
    ) -> list[dict[str, Any]]:
        """
        Query time-series metrics.

        Return a list of dicts, each with at least:
        ``metric``, ``scope``, ``points`` (list of [timestamp, value] pairs).
        """

    @abc.abstractmethod
    async def query_traces(
        self,
        *,
        query: str,
        time_range_minutes: int = 60,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Search distributed traces / spans.

        Return a list of dicts, each with at least:
        ``trace_id``, ``span_id``, ``service``, ``resource``, ``status``,
        ``duration_ns``.
        """

    def log_query_template(self, *, service: str) -> str:
        """Return the backend-native log query string for a service."""
        return f"service:{service} error"

    def metrics_query_template(self, *, service: str) -> str:
        """Return the backend-native metrics query string for a service."""
        return f"cpu{{service={service}}}"

    def trace_query_template(self, *, service: str) -> str:
        """Return the backend-native trace query string for a service."""
        return f"service:{service} status:error"
