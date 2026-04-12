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

from sentinel.domain.sre import entities, investigation
from sentinel.utils import logs


logger = logs.get_logger()

_CRD_GROUP = "kagent.dev"
_CRD_VERSION = "v1alpha1"
_CRD_PLURAL = "investigations"

_POLL_INTERVAL_INITIAL = 1.0
_POLL_INTERVAL_MAX = 10.0
_POLL_BACKOFF_FACTOR = 2.0

_TERMINAL_PHASES = frozenset({"Completed", "Failed"})


def _build_crd_body(
    *,
    alert: entities.Alert,
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
) -> tuple[entities.Finding, ...]:
    """
    Map kagent CRD findings to Sentinel Finding entities.
    """
    return tuple(
        entities.Finding(
            source=f.get("source", "unknown"),
            summary=f.get("summary", ""),
            raw_data=f.get("raw_data"),
            relevance=f.get("relevance", 0.0),
        )
        for f in raw_findings
    )


def _make_audit_entry(
    *,
    started_at: datetime,
    action: str,
    status: str,
    duration_ms: int,
    payload: Mapping[str, Any],
    error_code: str | None = None,
) -> investigation.AuditEntry:
    """Create an AuditEntry for the kagent adapter."""
    return investigation.AuditEntry(
        timestamp=started_at,
        adapter_name="kagent",
        action=action,
        tool_name="kagent-operator",
        status=status,
        duration_ms=duration_ms,
        error_code=error_code,
        payload=payload,
    )


class KagentAdapter(investigation.K8sInvestigationAdapter):
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
        alert: entities.Alert,
        context: investigation.InvestigationContext | None = None,
    ) -> investigation.InvestigationResult:
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

        audit_entries: list[investigation.AuditEntry] = []

        # -- Step 1: Create CRD -----------------------------------------------
        crd_name = await self._create_crd(
            alert=alert,
            started_at=started_at,
            start_time=start_time,
            audit_entries=audit_entries,
        )
        if crd_name is None:
            # CRD creation failed — return degraded result
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return investigation.InvestigationResult(
                findings=(),
                sources_queried=(),
                duration_ms=duration_ms,
                adapter_name="kagent",
                audit_trail=tuple(audit_entries),
            )

        # -- Step 2: Poll CRD status -------------------------------------------
        crd_response = await self._poll_crd_status(
            crd_name=crd_name,
            started_at=started_at,
            start_time=start_time,
            audit_entries=audit_entries,
        )

        # -- Step 3: Parse results or return degraded --------------------------
        if crd_response is None:
            # Timeout or poll error — audit entry already recorded
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return investigation.InvestigationResult(
                findings=(),
                sources_queried=(),
                duration_ms=duration_ms,
                adapter_name="kagent",
                audit_trail=tuple(audit_entries),
            )

        phase = crd_response.get("status", {}).get("phase", "Unknown")
        if phase == "Failed":
            duration_ms = int((time.monotonic() - start_time) * 1000)
            audit_entries.append(
                _make_audit_entry(
                    started_at=datetime.now(tz=UTC),
                    action="crd_failed",
                    status="error",
                    duration_ms=duration_ms,
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
            return investigation.InvestigationResult(
                findings=(),
                sources_queried=(),
                duration_ms=duration_ms,
                adapter_name="kagent",
                audit_trail=tuple(audit_entries),
            )

        # Completed — parse findings
        return self._parse_crd_result(
            crd_response=crd_response,
            crd_name=crd_name,
            start_time=start_time,
            audit_entries=audit_entries,
        )

    async def _create_crd(
        self,
        *,
        alert: entities.Alert,
        started_at: datetime,
        start_time: float,
        audit_entries: list[investigation.AuditEntry],
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
            duration_ms = int((time.monotonic() - create_start) * 1000)
            audit_entries.append(
                _make_audit_entry(
                    started_at=datetime.now(tz=UTC),
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
            duration_ms = int((time.monotonic() - create_start) * 1000)
            audit_entries.append(
                _make_audit_entry(
                    started_at=datetime.now(tz=UTC),
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
        started_at: datetime,
        start_time: float,
        audit_entries: list[investigation.AuditEntry],
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
                        started_at=datetime.now(tz=UTC),
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
            crd_response: dict[str, Any] = await api_client.get_namespaced_custom_object_status(
                group=_CRD_GROUP,
                version=_CRD_VERSION,
                namespace=self._kagent_namespace,
                plural=_CRD_PLURAL,
                name=crd_name,
            )
            poll_count += 1
            poll_duration = int((time.monotonic() - poll_start) * 1000)

            phase = crd_response.get("status", {}).get("phase", "Unknown")
            audit_entries.append(
                _make_audit_entry(
                    started_at=datetime.now(tz=UTC),
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

            await asyncio.sleep(interval)
            interval = min(interval * _POLL_BACKOFF_FACTOR, _POLL_INTERVAL_MAX)

    def _parse_crd_result(
        self,
        *,
        crd_response: dict[str, Any],
        crd_name: str,
        start_time: float,
        audit_entries: list[investigation.AuditEntry],
    ) -> investigation.InvestigationResult:
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

        duration_ms = int((time.monotonic() - start_time) * 1000)

        audit_entries.append(
            _make_audit_entry(
                started_at=datetime.now(tz=UTC),
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

        return investigation.InvestigationResult(
            findings=findings,
            sources_queried=sources_queried,
            duration_ms=duration_ms,
            adapter_name="kagent",
            audit_trail=tuple(audit_entries),
        )

    @staticmethod
    def _unconfigured_result(
        *,
        started_at: datetime,
    ) -> investigation.InvestigationResult:
        """Return a degraded result when the adapter is not configured."""
        return investigation.InvestigationResult(
            findings=(),
            sources_queried=(),
            duration_ms=0,
            adapter_name="kagent",
            audit_trail=(
                investigation.AuditEntry(
                    timestamp=started_at,
                    adapter_name="kagent",
                    action="configuration_check",
                    tool_name=None,
                    status="error",
                    duration_ms=0,
                    error_code=None,
                    payload={"reason": "Kagent K8s API client not configured"},
                ),
            ),
        )
