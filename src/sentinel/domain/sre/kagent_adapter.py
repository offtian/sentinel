"""
Kagent investigation adapter.

Delegates Kubernetes investigation to a kagent operator running
in the cluster.  Creates a kagent CRD, polls for completion,
and maps the results to Sentinel's InvestigationResult.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from sentinel.domain.sre import entities, investigation
from sentinel.utils import logs


logger = logs.get_logger()


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

        logs.log_event(
            "kagent_investigation_started",
            params={
                "alert_id": alert.id,
                "service": alert.service,
                "kagent_namespace": self._kagent_namespace,
                "timeout_seconds": self._timeout_seconds,
            },
        )

        # CRD creation, polling, and result mapping will be implemented
        # when the kagent operator is deployed to the cluster.
        #
        # Flow:
        # 1. Create kagent investigation CRD with alert context
        # 2. Poll CRD status until completed/failed/timeout
        # 3. Parse kagent findings and map to InvestigationResult
        duration_ms = int((time.monotonic() - start_time) * 1000)

        return investigation.InvestigationResult(
            findings=(),
            sources_queried=(),
            duration_ms=duration_ms,
            adapter_name="kagent",
            audit_trail=(
                investigation.AuditEntry(
                    timestamp=started_at,
                    adapter_name="kagent",
                    action="crd_operation",
                    tool_name=None,
                    status="error",
                    duration_ms=duration_ms,
                    error_code=None,
                    payload={
                        "reason": "Kagent CRD integration pending — operator not yet deployed",
                        "alert_id": alert.id,
                    },
                ),
            ),
        )
