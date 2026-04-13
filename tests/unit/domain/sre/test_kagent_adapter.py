from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from sentinel.domain.sre import entities, investigation, kagent_adapter
from tests import factories


# ---------------------------------------------------------------------------
# CRD constants (mirrored from adapter for assertion clarity)
# ---------------------------------------------------------------------------

_CRD_GROUP = "kagent.dev"
_CRD_VERSION = "v1alpha1"
_CRD_PLURAL = "investigations"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_crd_status(
    *,
    phase: str = "Completed",
    findings: list[dict[str, Any]] | None = None,
    sources_queried: list[str] | None = None,
) -> dict[str, Any]:
    """Build a minimal CRD response dict with .status populated."""
    result: dict[str, Any] = {}
    if findings is not None:
        result["findings"] = findings
    if sources_queried is not None:
        result["sources_queried"] = sources_queried
    return {
        "metadata": {"name": "inv-test", "namespace": "kagent-system"},
        "status": {"phase": phase, "result": result},
    }


def _make_custom_objects_api(
    *,
    create_return: dict[str, Any] | None = None,
    get_status_side_effect: list[dict[str, Any]] | None = None,
) -> mock.AsyncMock:
    """Build an AsyncMock CustomObjectsApi with sensible defaults."""
    api = mock.AsyncMock()
    api.create_namespaced_custom_object.return_value = create_return or {
        "metadata": {"name": "inv-test", "namespace": "kagent-system"},
    }
    if get_status_side_effect is not None:
        api.get_namespaced_custom_object_status.side_effect = get_status_side_effect
    else:
        api.get_namespaced_custom_object_status.return_value = _make_crd_status()
    return api


class TestKagentAdapterConfiguration:
    def test_is_not_configured_without_client(self) -> None:
        # Given no K8s API client
        adapter = kagent_adapter.KagentAdapter(k8s_api_client=None)

        # Then it reports as not configured
        assert adapter.is_configured is False

    def test_is_configured_with_client(self) -> None:
        # Given a mock K8s API client
        mock_client = mock.MagicMock()

        # When creating the adapter
        adapter = kagent_adapter.KagentAdapter(k8s_api_client=mock_client)

        # Then it reports as configured
        assert adapter.is_configured is True

    @pytest.mark.asyncio
    async def test_returns_degraded_result_when_not_configured(self) -> None:
        # Given an unconfigured adapter
        adapter = kagent_adapter.KagentAdapter(k8s_api_client=None)
        alert = factories.make_alert(title="Pod CrashLoopBackOff")
        context = investigation.InvestigationContext(cluster_name="prod")

        # When investigating
        result = await adapter.investigate(alert=alert, context=context)

        # Then a degraded result is returned with audit trail
        assert result.adapter_name == "kagent"
        assert result.findings == ()
        assert len(result.audit_trail) == 1
        assert result.audit_trail[0].status == "error"
        assert "not configured" in result.audit_trail[0].payload["reason"].lower()


class TestKagentCrdCreation:
    @pytest.mark.asyncio
    async def test_creates_crd_with_correct_spec(self) -> None:
        # Given a configured adapter with a mock CustomObjectsApi
        api = _make_custom_objects_api()
        adapter = kagent_adapter.KagentAdapter(
            k8s_api_client=api,
            kagent_namespace="kagent-system",
            timeout_seconds=30,
        )
        alert = factories.make_alert(
            alert_id="ALERT-99",
            title="Pod OOMKilled",
            service="payment-svc",
            severity=entities.AlertSeverity.CRITICAL,
            description="Container exceeded memory limit",
        )

        # When investigating
        await adapter.investigate(alert=alert)

        # Then a CRD is created with the correct group, version, plural, and spec
        api.create_namespaced_custom_object.assert_called_once()
        call_kwargs = api.create_namespaced_custom_object.call_args[1]
        assert call_kwargs["group"] == _CRD_GROUP
        assert call_kwargs["version"] == _CRD_VERSION
        assert call_kwargs["plural"] == _CRD_PLURAL
        assert call_kwargs["namespace"] == "kagent-system"

        body = call_kwargs["body"]
        spec = body["spec"]
        assert spec["alert_id"] == "ALERT-99"
        assert spec["service"] == "payment-svc"
        assert spec["severity"] == "critical"
        assert spec["description"] == "Container exceeded memory limit"

    @pytest.mark.asyncio
    async def test_crd_creation_audit_entry(self) -> None:
        # Given a configured adapter
        api = _make_custom_objects_api()
        adapter = kagent_adapter.KagentAdapter(k8s_api_client=api, timeout_seconds=30)
        alert = factories.make_alert(alert_id="ALERT-AUDIT")

        # When investigating
        result = await adapter.investigate(alert=alert)

        # Then the audit trail contains a crd_create entry
        actions = [e.action for e in result.audit_trail]
        assert "crd_create" in actions
        create_entry = next(e for e in result.audit_trail if e.action == "crd_create")
        assert create_entry.status == "success"
        assert create_entry.adapter_name == "kagent"


class TestKagentCrdPolling:
    @pytest.mark.asyncio
    async def test_polling_completes_on_completed_status(self) -> None:
        # Given a CRD that transitions Pending -> Running -> Completed
        pending = _make_crd_status(phase="Pending")
        running = _make_crd_status(phase="Running")
        completed = _make_crd_status(
            phase="Completed",
            findings=[{"source": "k8s-logs", "summary": "OOMKilled detected"}],
            sources_queried=["pod-logs", "events"],
        )
        api = _make_custom_objects_api(
            get_status_side_effect=[pending, running, completed],
        )
        adapter = kagent_adapter.KagentAdapter(k8s_api_client=api, timeout_seconds=60)
        alert = factories.make_alert()

        # When investigating
        result = await adapter.investigate(alert=alert)

        # Then the result contains the parsed findings
        assert len(result.findings) == 1
        assert result.findings[0].source == "k8s-logs"
        assert result.findings[0].summary == "OOMKilled detected"
        assert "pod-logs" in result.sources_queried
        assert "events" in result.sources_queried

    @pytest.mark.asyncio
    async def test_polling_returns_degraded_on_failed_status(self) -> None:
        # Given a CRD that transitions to Failed
        failed = _make_crd_status(phase="Failed")
        api = _make_custom_objects_api(
            get_status_side_effect=[failed],
        )
        adapter = kagent_adapter.KagentAdapter(k8s_api_client=api, timeout_seconds=60)
        alert = factories.make_alert()

        # When investigating
        result = await adapter.investigate(alert=alert)

        # Then a degraded result with crd_failed audit entry is returned
        assert result.findings == ()
        actions = [e.action for e in result.audit_trail]
        assert "crd_failed" in actions

    @pytest.mark.asyncio
    async def test_polling_audit_entries_recorded(self) -> None:
        # Given a CRD that needs two poll cycles
        pending = _make_crd_status(phase="Pending")
        completed = _make_crd_status(phase="Completed", findings=[], sources_queried=[])
        api = _make_custom_objects_api(
            get_status_side_effect=[pending, completed],
        )
        adapter = kagent_adapter.KagentAdapter(k8s_api_client=api, timeout_seconds=60)
        alert = factories.make_alert()

        # When investigating
        result = await adapter.investigate(alert=alert)

        # Then the audit trail contains crd_poll entries
        actions = [e.action for e in result.audit_trail]
        assert "crd_poll" in actions


class TestKagentTimeout:
    @pytest.mark.asyncio
    async def test_timeout_returns_degraded_result(self) -> None:
        # Given a CRD that never completes (always Pending)
        pending = _make_crd_status(phase="Pending")
        api = _make_custom_objects_api()
        # Make every poll return Pending
        api.get_namespaced_custom_object_status.return_value = pending
        api.get_namespaced_custom_object_status.side_effect = None

        adapter = kagent_adapter.KagentAdapter(
            k8s_api_client=api,
            timeout_seconds=0,  # immediate timeout
        )
        alert = factories.make_alert()

        # When investigating
        result = await adapter.investigate(alert=alert)

        # Then a degraded result with timeout audit entry is returned
        assert result.findings == ()
        actions = [e.action for e in result.audit_trail]
        assert "crd_timeout" in actions
        timeout_entry = next(e for e in result.audit_trail if e.action == "crd_timeout")
        assert timeout_entry.status == "error"


class TestKagentResultParsing:
    @pytest.mark.asyncio
    async def test_maps_multiple_findings_correctly(self) -> None:
        # Given a CRD with multiple findings
        completed = _make_crd_status(
            phase="Completed",
            findings=[
                {"source": "pod-logs", "summary": "OOMKilled in container web"},
                {"source": "k8s-events", "summary": "Node pressure detected"},
            ],
            sources_queried=["pod-logs", "k8s-events", "metrics"],
        )
        api = _make_custom_objects_api(
            get_status_side_effect=[completed],
        )
        adapter = kagent_adapter.KagentAdapter(k8s_api_client=api, timeout_seconds=60)
        alert = factories.make_alert()

        # When investigating
        result = await adapter.investigate(alert=alert)

        # Then all findings are mapped
        assert len(result.findings) == 2
        assert result.findings[0].source == "pod-logs"
        assert result.findings[1].source == "k8s-events"
        assert len(result.sources_queried) == 3

    @pytest.mark.asyncio
    async def test_maps_empty_findings_correctly(self) -> None:
        # Given a CRD with no findings
        completed = _make_crd_status(phase="Completed", findings=[], sources_queried=[])
        api = _make_custom_objects_api(
            get_status_side_effect=[completed],
        )
        adapter = kagent_adapter.KagentAdapter(k8s_api_client=api, timeout_seconds=60)
        alert = factories.make_alert()

        # When investigating
        result = await adapter.investigate(alert=alert)

        # Then result has empty findings
        assert result.findings == ()
        assert result.sources_queried == ()

    @pytest.mark.asyncio
    async def test_parse_audit_entry_includes_raw_payload(self) -> None:
        # Given a CRD with findings
        completed = _make_crd_status(
            phase="Completed",
            findings=[{"source": "logs", "summary": "error found"}],
            sources_queried=["logs"],
        )
        api = _make_custom_objects_api(
            get_status_side_effect=[completed],
        )
        adapter = kagent_adapter.KagentAdapter(k8s_api_client=api, timeout_seconds=60)
        alert = factories.make_alert()

        # When investigating
        result = await adapter.investigate(alert=alert)

        # Then crd_parse audit entry stores raw CRD output in payload
        parse_entry = next((e for e in result.audit_trail if e.action == "crd_parse"), None)
        assert parse_entry is not None
        assert parse_entry.status == "success"
        assert "raw_status" in parse_entry.payload


class TestKagentAuditTrail:
    @pytest.mark.asyncio
    async def test_full_audit_trail_on_success(self) -> None:
        # Given a CRD that completes immediately
        completed = _make_crd_status(phase="Completed", findings=[], sources_queried=[])
        api = _make_custom_objects_api(
            get_status_side_effect=[completed],
        )
        adapter = kagent_adapter.KagentAdapter(k8s_api_client=api, timeout_seconds=60)
        alert = factories.make_alert()

        # When investigating
        result = await adapter.investigate(alert=alert)

        # Then audit trail has create, poll, and parse entries
        actions = [e.action for e in result.audit_trail]
        assert "crd_create" in actions
        assert "crd_poll" in actions
        assert "crd_parse" in actions

    @pytest.mark.asyncio
    async def test_stores_alert_id_in_create_audit_payload(self) -> None:
        # Given a configured adapter
        api = _make_custom_objects_api()
        adapter = kagent_adapter.KagentAdapter(k8s_api_client=api, timeout_seconds=60)
        alert = factories.make_alert(alert_id="ALERT-42")

        # When investigating
        result = await adapter.investigate(alert=alert)

        # Then the alert ID is in the create audit payload
        create_entry = next(e for e in result.audit_trail if e.action == "crd_create")
        assert create_entry.payload["alert_id"] == "ALERT-42"


class TestKagentCrdCreationFailure:
    @pytest.mark.asyncio
    async def test_crd_creation_error_returns_degraded(self) -> None:
        # Given a CustomObjectsApi that raises on create
        api = mock.AsyncMock()
        api.create_namespaced_custom_object.side_effect = Exception("API server unavailable")
        adapter = kagent_adapter.KagentAdapter(k8s_api_client=api, timeout_seconds=60)
        alert = factories.make_alert()

        # When investigating
        result = await adapter.investigate(alert=alert)

        # Then a degraded result is returned with error audit entry
        assert result.findings == ()
        assert result.adapter_name == "kagent"
        create_entry = next(e for e in result.audit_trail if e.action == "crd_create")
        assert create_entry.status == "error"
        assert "API server unavailable" in str(create_entry.payload.get("error", ""))
