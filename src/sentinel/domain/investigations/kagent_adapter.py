"""
Kagent investigation adapter.

Delegates Kubernetes investigation to a kagent operator running
in the cluster.  Creates a kagent CRD, polls for completion,
and maps the results to Sentinel's InvestigationResult.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sentinel.domain.alerts import entities as alert_entities
from sentinel.domain.investigations import adapters
from sentinel.domain.investigations import entities as investigation_entities
from sentinel.utils import logs


logger = logs.get_logger()

_ADAPTER_NAME = "kagent"
_TOOL_NAME = "kagent-operator"

_CRD_GROUP = "kagent.dev"
_CRD_VERSION = "v1alpha1"
_CRD_PLURAL = "investigations"

_POLL_INTERVAL_INITIAL = 1.0
_POLL_INTERVAL_MAX = 10.0
_POLL_BACKOFF_FACTOR = 2.0

_TERMINAL_PHASES = frozenset({"Completed", "Failed"})


def _elapsed_ms(start: float) -> int:
    """Return milliseconds elapsed since ``start`` (monotonic clock)."""
    return int((time.monotonic() - start) * 1000)


def _build_crd_body(
    *,
    alert: alert_entities.Alert,
    namespace: str,
) -> dict[str, Any]:
    """
    Build the kagent investigation CRD manifest.

    Returns an immutable-safe dict (caller should not mutate).
    """
    return {
        "apiVersion": f"{_CRD_GROUP}/{_CRD_VERSION}",
        "kind": "Investigation",
        "metadata": {
            "name": f"inv-{alert.id}-{uuid.uuid4().hex[:8]}",
            "namespace": namespace,
        },
        "spec": {
            "alert_id": alert.id,
            "service": alert.service,
            "severity": alert.severity.value,
            "description": alert.description,
            "namespace": namespace,
        },
    }


def _parse_findings(
    raw_findings: list[dict[str, Any]],
) -> tuple[investigation_entities.Finding, ...]:
    """
    Map kagent CRD findings to Sentinel Finding entities.
    """
    return tuple(
        investigation_entities.Finding(
            source=f.get("source", "unknown"),
            summary=f.get("summary", ""),
            raw_data=f.get("raw_data"),
            relevance=f.get("relevance", 0.0),
        )
        for f in raw_findings
    )


def _make_audit_entry(
    *,
    action: str,
    status: str,
    duration_ms: int,
    payload: Mapping[str, Any],
    error_code: str | None = None,
) -> adapters.AuditEntry:
    """Create an AuditEntry for the kagent adapter."""
    return adapters.AuditEntry(
        timestamp=datetime.now(tz=UTC),
        adapter_name=_ADAPTER_NAME,
        action=action,
        tool_name=_TOOL_NAME,
        status=status,
        duration_ms=duration_ms,
        error_code=error_code,
        payload=payload,
    )


class KagentAdapter(adapters.K8sInvestigationAdapter):
    """
    Investigate alerts by delegating to the kagent K8s operator.

    Creates a kagent CRD with alert context, polls for completion,
    and maps kagent's findings to Sentinel's InvestigationResult.
    """

    def __init__(
        self,
        *,
        k8s_api_client: Any | None = None,
        kagent_namespace: str = "kagent-system",
        timeout_seconds: int = 120,
    ) -> None:
        self._k8s_api_client = k8s_api_client
        self._kagent_namespace = kagent_namespace
        self._timeout_seconds = timeout_seconds

    @property
    def is_configured(self) -> bool:
        return self._k8s_api_client is not None

    async def investigate(
        self,
        *,
        alert: alert_entities.Alert,
        context: adapters.InvestigationContext | None = None,
    ) -> adapters.InvestigationResult:
        start_time = time.monotonic()
        started_at = datetime.now(tz=UTC)

        if not self.is_configured:
            return self._unconfigured_result(started_at=started_at)

        logs.log_event(
            "kagent_investigation_started",
            params={
                "alert_id": alert.id,
                "service": alert.service,
                "kagent_namespace": self._kagent_namespace,
                "timeout_seconds": self._timeout_seconds,
            },
        )

        audit_entries: list[adapters.AuditEntry] = []

        crd_name = await self._create_crd(
            alert=alert,
            audit_entries=audit_entries,
        )
        if crd_name is None:
            return self._degraded_result(start_time=start_time, audit_entries=audit_entries)

        crd_response = await self._poll_crd_status(
            crd_name=crd_name,
            start_time=start_time,
            audit_entries=audit_entries,
        )

        if crd_response is None:
            return self._degraded_result(start_time=start_time, audit_entries=audit_entries)

        phase = crd_response.get("status", {}).get("phase", "Unknown")
        if phase == "Failed":
            audit_entries.append(
                _make_audit_entry(
                    action="crd_failed",
                    status="error",
                    duration_ms=_elapsed_ms(start_time),
                    payload={
                        "crd_name": crd_name,
                        "phase": phase,
                        "raw_status": crd_response.get("status", {}),
                    },
                )
            )
            logs.log_event(
                "kagent_investigation_failed",
                params={"crd_name": crd_name, "phase": phase},
            )
            return self._degraded_result(start_time=start_time, audit_entries=audit_entries)

        return self._parse_crd_result(
            crd_response=crd_response,
            crd_name=crd_name,
            start_time=start_time,
            audit_entries=audit_entries,
        )

    async def _create_crd(
        self,
        *,
        alert: alert_entities.Alert,
        audit_entries: list[adapters.AuditEntry],
    ) -> str | None:
        """
        Create the kagent investigation CRD.

        Return the CRD name on success, None on failure.
        Appends an audit entry in both cases.
        """
        create_start = time.monotonic()
        body = _build_crd_body(alert=alert, namespace=self._kagent_namespace)
        api_client = self._k8s_api_client
        if api_client is None:
            msg = "KagentAdapter._create_crd called without configured API client"
            raise RuntimeError(msg)
        try:
            result = await api_client.create_namespaced_custom_object(
                group=_CRD_GROUP,
                version=_CRD_VERSION,
                namespace=self._kagent_namespace,
                plural=_CRD_PLURAL,
                body=body,
            )
            crd_name: str = result["metadata"]["name"]
            duration_ms = _elapsed_ms(create_start)
            audit_entries.append(
                _make_audit_entry(
                    action="crd_create",
                    status="success",
                    duration_ms=duration_ms,
                    payload={
                        "crd_name": crd_name,
                        "alert_id": alert.id,
                        "namespace": self._kagent_namespace,
                    },
                )
            )
            logs.log_event(
                "kagent_crd_created",
                params={"crd_name": crd_name, "alert_id": alert.id},
            )
            return crd_name
        except Exception as exc:
            duration_ms = _elapsed_ms(create_start)
            audit_entries.append(
                _make_audit_entry(
                    action="crd_create",
                    status="error",
                    duration_ms=duration_ms,
                    payload={
                        "alert_id": alert.id,
                        "error": str(exc),
                    },
                )
            )
            logs.log_exception(
                exc,
                params={"alert_id": alert.id, "action": "crd_create"},
            )
            return None

    async def _poll_crd_status(
        self,
        *,
        crd_name: str,
        start_time: float,
        audit_entries: list[adapters.AuditEntry],
    ) -> dict[str, Any] | None:
        """
        Poll the CRD status with exponential backoff.

        Return the CRD dict when a terminal phase is reached, or None on timeout.
        Appends audit entries for each poll cycle and on timeout.
        """
        api_client = self._k8s_api_client
        if api_client is None:
            msg = "KagentAdapter._poll_crd_status called without configured API client"
            raise RuntimeError(msg)
        interval = _POLL_INTERVAL_INITIAL
        poll_count = 0

        while True:
            elapsed = time.monotonic() - start_time
            if elapsed >= self._timeout_seconds:
                audit_entries.append(
                    _make_audit_entry(
                        action="crd_timeout",
                        status="error",
                        duration_ms=int(elapsed * 1000),
                        payload={
                            "crd_name": crd_name,
                            "timeout_seconds": self._timeout_seconds,
                            "poll_count": poll_count,
                        },
                    )
                )
                logs.log_event(
                    "kagent_investigation_timeout",
                    params={
                        "crd_name": crd_name,
                        "timeout_seconds": self._timeout_seconds,
                        "poll_count": poll_count,
                    },
                )
                return None

            poll_start = time.monotonic()
            try:
                crd_response: dict[
                    str, Any
                ] = await api_client.get_namespaced_custom_object_status(
                    group=_CRD_GROUP,
                    version=_CRD_VERSION,
                    namespace=self._kagent_namespace,
                    plural=_CRD_PLURAL,
                    name=crd_name,
                )
            except Exception as exc:
                poll_count += 1
                poll_duration = _elapsed_ms(poll_start)
                audit_entries.append(
                    _make_audit_entry(
                        action="crd_poll",
                        status="error",
                        duration_ms=poll_duration,
                        error_code=type(exc).__name__,
                        payload={
                            "crd_name": crd_name,
                            "error": str(exc),
                            "poll_count": poll_count,
                        },
                    )
                )
                logs.log_exception(
                    exc,
                    params={"crd_name": crd_name, "action": "crd_poll"},
                )
                # Continue retrying — transient K8s API errors are expected
                remaining = self._timeout_seconds - (time.monotonic() - start_time)
                await asyncio.sleep(min(interval, max(remaining, 0)))
                interval = min(interval * _POLL_BACKOFF_FACTOR, _POLL_INTERVAL_MAX)
                continue

            poll_count += 1
            poll_duration = _elapsed_ms(poll_start)

            phase = crd_response.get("status", {}).get("phase", "Unknown")
            audit_entries.append(
                _make_audit_entry(
                    action="crd_poll",
                    status="success",
                    duration_ms=poll_duration,
                    payload={
                        "crd_name": crd_name,
                        "phase": phase,
                        "poll_count": poll_count,
                    },
                )
            )

            if phase in _TERMINAL_PHASES:
                return crd_response

            # Cap sleep to remaining timeout to avoid overshooting deadline
            remaining = self._timeout_seconds - (time.monotonic() - start_time)
            await asyncio.sleep(min(interval, max(remaining, 0)))
            interval = min(interval * _POLL_BACKOFF_FACTOR, _POLL_INTERVAL_MAX)

    def _parse_crd_result(
        self,
        *,
        crd_response: dict[str, Any],
        crd_name: str,
        start_time: float,
        audit_entries: list[adapters.AuditEntry],
    ) -> adapters.InvestigationResult:
        """
        Parse a completed CRD response into an InvestigationResult.

        Appends a crd_parse audit entry with the raw status for traceability.
        """
        status = crd_response.get("status", {})
        result_data = status.get("result", {})
        raw_findings = result_data.get("findings", [])
        raw_sources = result_data.get("sources_queried", [])

        findings = _parse_findings(raw_findings)
        sources_queried = tuple(raw_sources)

        duration_ms = _elapsed_ms(start_time)

        audit_entries.append(
            _make_audit_entry(
                action="crd_parse",
                status="success",
                duration_ms=duration_ms,
                payload={
                    "crd_name": crd_name,
                    "findings_count": len(findings),
                    "sources_count": len(sources_queried),
                    "raw_status": status,
                },
            )
        )

        logs.log_event(
            "kagent_investigation_completed",
            params={
                "crd_name": crd_name,
                "findings_count": len(findings),
                "sources_count": len(sources_queried),
                "duration_ms": duration_ms,
            },
        )

        return adapters.InvestigationResult(
            findings=findings,
            sources_queried=sources_queried,
            duration_ms=duration_ms,
            adapter_name=_ADAPTER_NAME,
            audit_trail=tuple(audit_entries),
        )

    @staticmethod
    def _degraded_result(
        *,
        start_time: float,
        audit_entries: list[adapters.AuditEntry],
    ) -> adapters.InvestigationResult:
        """Return a degraded result with the audit trail collected so far."""
        return adapters.InvestigationResult(
            findings=(),
            sources_queried=(),
            duration_ms=_elapsed_ms(start_time),
            adapter_name=_ADAPTER_NAME,
            audit_trail=tuple(audit_entries),
        )

    @staticmethod
    def _unconfigured_result(
        *,
        started_at: datetime,
    ) -> adapters.InvestigationResult:
        """Return a degraded result when the adapter is not configured."""
        return adapters.InvestigationResult(
            findings=(),
            sources_queried=(),
            duration_ms=0,
            adapter_name=_ADAPTER_NAME,
            audit_trail=(
                adapters.AuditEntry(
                    timestamp=started_at,
                    adapter_name=_ADAPTER_NAME,
                    action="configuration_check",
                    tool_name=None,
                    status="error",
                    duration_ms=0,
                    error_code=None,
                    payload={"reason": "Kagent K8s API client not configured"},
                ),
            ),
        )
