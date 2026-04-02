from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sentinel.domain.sre import investigation


class TestAuditEntry:
    def test_creates_immutable_audit_entry(self) -> None:
        # Given a complete set of audit entry fields
        now = datetime(2026, 4, 2, 12, 0, tzinfo=UTC)

        # When an AuditEntry is created
        entry = investigation.AuditEntry(
            timestamp=now,
            adapter_name="native_k8s",
            action="tool_call",
            tool_name="get_pod_status",
            status="success",
            duration_ms=42,
            error_code=None,
            payload={"namespace": "production", "pod": "api-service-abc123"},
        )

        # Then all fields are accessible
        assert entry.adapter_name == "native_k8s"
        assert entry.action == "tool_call"
        assert entry.tool_name == "get_pod_status"
        assert entry.status == "success"
        assert entry.duration_ms == 42
        assert entry.error_code is None
        assert entry.payload["namespace"] == "production"

    def test_creates_audit_entry_with_error(self) -> None:
        # Given an error scenario
        now = datetime(2026, 4, 2, 12, 0, tzinfo=UTC)

        # When an AuditEntry is created with an error code
        entry = investigation.AuditEntry(
            timestamp=now,
            adapter_name="kagent",
            action="crd_operation",
            tool_name=None,
            status="error",
            duration_ms=5000,
            error_code="408",
            payload={"reason": "timeout waiting for CRD completion"},
        )

        # Then the error code is set
        assert entry.status == "error"
        assert entry.error_code == "408"


class TestInvestigationContext:
    def test_creates_context_with_defaults(self) -> None:
        # Given minimal context
        # When an InvestigationContext is created
        ctx = investigation.InvestigationContext(
            cluster_name="prod-eu-west-1",
        )

        # Then namespace defaults to None and additional_sources is empty
        assert ctx.cluster_name == "prod-eu-west-1"
        assert ctx.namespace is None
        assert ctx.additional_sources == ()

    def test_creates_context_with_namespace(self) -> None:
        # Given a namespace-scoped context
        # When an InvestigationContext is created with namespace
        ctx = investigation.InvestigationContext(
            cluster_name="prod-eu-west-1",
            namespace="payments",
            additional_sources=("prometheus", "alertmanager"),
        )

        # Then all fields are set
        assert ctx.namespace == "payments"
        assert ctx.additional_sources == ("prometheus", "alertmanager")


class TestInvestigationResult:
    def test_creates_result_with_audit_trail(self) -> None:
        # Given findings and audit entries
        from tests.factories import make_finding

        finding = make_finding(source="kubernetes", summary="Pod restarting")
        now = datetime(2026, 4, 2, 12, 0, tzinfo=UTC)
        audit_entry = investigation.AuditEntry(
            timestamp=now,
            adapter_name="native_k8s",
            action="tool_call",
            tool_name="get_pod_status",
            status="success",
            duration_ms=42,
            error_code=None,
            payload={},
        )

        # When an InvestigationResult is created
        result = investigation.InvestigationResult(
            findings=(finding,),
            sources_queried=("kubernetes_pods", "kubernetes_events"),
            duration_ms=1500,
            adapter_name="native_k8s",
            audit_trail=(audit_entry,),
        )

        # Then the audit trail is attached
        assert len(result.audit_trail) == 1
        assert result.audit_trail[0].tool_name == "get_pod_status"
        assert result.adapter_name == "native_k8s"
        assert result.duration_ms == 1500
