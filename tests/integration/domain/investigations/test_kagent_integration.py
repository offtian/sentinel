"""
Integration tests for the kagent CRD lifecycle.

Tests the adapter against a mock K8s API (AsyncMock for CustomObjectsApi)
to verify the full create-poll-parse flow without a real cluster.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

from sentinel.domain.alerts import entities as alert_entities
from sentinel.domain.investigations import kagent_adapter
from tests import factories


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_crd_response(
    *,
    name: str = "inv-test-abc12345",
    namespace: str = "kagent-system",
    phase: str = "Completed",
    findings: list[dict[str, Any]] | None = None,
    sources_queried: list[str] | None = None,
) -> dict[str, Any]:
    """Build a full CRD response dict."""
    result: dict[str, Any] = {}
    if findings is not None:
        result["findings"] = findings
    if sources_queried is not None:
        result["sources_queried"] = sources_queried
    return {
        "apiVersion": "kagent.dev/v1alpha1",
        "kind": "Investigation",
        "metadata": {"name": name, "namespace": namespace},
        "status": {"phase": phase, "result": result},
    }


def _build_mock_api(
    *,
    create_response: dict[str, Any] | None = None,
    poll_responses: list[dict[str, Any]] | None = None,
) -> mock.AsyncMock:
    """Build a mock CustomObjectsApi with configurable responses."""
    api = mock.AsyncMock()
    api.create_namespaced_custom_object.return_value = create_response or {
        "metadata": {"name": "inv-test-abc12345", "namespace": "kagent-system"},
    }
    if poll_responses is not None:
        api.get_namespaced_custom_object_status.side_effect = poll_responses
    else:
        api.get_namespaced_custom_object_status.return_value = _make_crd_response()
    return api


# ---------------------------------------------------------------------------
# CRD lifecycle: create -> poll -> parse
# ---------------------------------------------------------------------------


class TestKagentCrdLifecycleSuccess:
    """Full lifecycle: CRD created, polled through phases, results parsed."""

    async def test_lifecycle_create_poll_complete(self) -> None:
        # Given a CRD that transitions Pending -> Running -> Completed
        pending = _make_crd_response(
            phase="Pending",
            findings=None,
            sources_queried=None,
        )
        running = _make_crd_response(
            phase="Running",
            findings=None,
            sources_queried=None,
        )
        completed = _make_crd_response(
            phase="Completed",
            findings=[
                {"source": "pod-logs", "summary": "OOMKilled in web container", "relevance": 0.9},
                {"source": "k8s-events", "summary": "Eviction notice", "relevance": 0.7},
            ],
            sources_queried=["pod-logs", "k8s-events", "node-metrics"],
        )
        api = _build_mock_api(poll_responses=[pending, running, completed])
        adapter = kagent_adapter.KagentAdapter(
            k8s_api_client=api,
            kagent_namespace="kagent-system",
            timeout_seconds=60,
        )
        alert = factories.make_alert(
            alert_id="ALERT-INT-1",
            title="OOMKilled",
            service="web-api",
            severity=alert_entities.AlertSeverity.CRITICAL,
            description="Container OOMKilled repeatedly",
        )

        # When running a full investigation
        result = await adapter.investigate(alert=alert)

        # Then CRD was created once
        api.create_namespaced_custom_object.assert_called_once()

        # Then polling was called 3 times (Pending, Running, Completed)
        assert api.get_namespaced_custom_object_status.call_count == 3

        # Then the result contains parsed findings
        assert len(result.findings) == 2
        assert result.findings[0].source == "pod-logs"
        assert result.findings[0].relevance == 0.9
        assert result.findings[1].source == "k8s-events"
        assert len(result.sources_queried) == 3
        assert "node-metrics" in result.sources_queried

        # Then the adapter name is correct
        assert result.adapter_name == "kagent"
        assert result.duration_ms >= 0

    async def test_lifecycle_audit_trail_completeness(self) -> None:
        # Given a CRD that goes Pending -> Completed
        pending = _make_crd_response(phase="Pending")
        completed = _make_crd_response(
            phase="Completed",
            findings=[{"source": "metrics", "summary": "CPU spike"}],
            sources_queried=["metrics"],
        )
        api = _build_mock_api(poll_responses=[pending, completed])
        adapter = kagent_adapter.KagentAdapter(
            k8s_api_client=api,
            timeout_seconds=60,
        )
        alert = factories.make_alert()

        # When running a full investigation
        result = await adapter.investigate(alert=alert)

        # Then the audit trail contains create, poll(s), and parse entries
        actions = [entry.action for entry in result.audit_trail]
        assert actions.count("crd_create") == 1
        assert actions.count("crd_poll") == 2  # Pending + Completed
        assert actions.count("crd_parse") == 1

        # Then every audit entry has adapter_name = kagent
        for entry in result.audit_trail:
            assert entry.adapter_name == "kagent"

        # Then every audit entry has a non-negative duration
        for entry in result.audit_trail:
            assert entry.duration_ms >= 0


# ---------------------------------------------------------------------------
# CRD lifecycle: failure paths
# ---------------------------------------------------------------------------


class TestKagentCrdLifecycleFailure:
    """Lifecycle when the CRD reports failure or the API errors."""

    async def test_lifecycle_crd_fails(self) -> None:
        # Given a CRD that transitions to Failed
        failed = _make_crd_response(
            phase="Failed",
            findings=None,
            sources_queried=None,
        )
        api = _build_mock_api(poll_responses=[failed])
        adapter = kagent_adapter.KagentAdapter(
            k8s_api_client=api,
            timeout_seconds=60,
        )
        alert = factories.make_alert(alert_id="ALERT-FAIL")

        # When running a full investigation
        result = await adapter.investigate(alert=alert)

        # Then the result is degraded (no findings)
        assert result.findings == ()
        assert result.sources_queried == ()

        # Then the audit trail records the failure
        actions = [entry.action for entry in result.audit_trail]
        assert "crd_failed" in actions
        failed_entry = next(e for e in result.audit_trail if e.action == "crd_failed")
        assert failed_entry.status == "error"
        assert failed_entry.payload["phase"] == "Failed"

    async def test_lifecycle_create_api_error(self) -> None:
        # Given a CustomObjectsApi that raises on create
        api = mock.AsyncMock()
        api.create_namespaced_custom_object.side_effect = RuntimeError(
            "etcd connection refused",
        )
        adapter = kagent_adapter.KagentAdapter(
            k8s_api_client=api,
            timeout_seconds=60,
        )
        alert = factories.make_alert()

        # When running a full investigation
        result = await adapter.investigate(alert=alert)

        # Then the result is degraded
        assert result.findings == ()

        # Then the audit trail records the creation error
        create_entry = next(e for e in result.audit_trail if e.action == "crd_create")
        assert create_entry.status == "error"
        assert "etcd connection refused" in str(create_entry.payload["error"])


# ---------------------------------------------------------------------------
# Timeout behavior
# ---------------------------------------------------------------------------


class TestKagentCrdTimeout:
    """Adapter behavior when the CRD never reaches a terminal phase."""

    async def test_timeout_produces_degraded_result(self) -> None:
        # Given a CRD that stays in Pending forever
        pending = _make_crd_response(phase="Pending")
        api = _build_mock_api()
        api.get_namespaced_custom_object_status.return_value = pending
        api.get_namespaced_custom_object_status.side_effect = None

        adapter = kagent_adapter.KagentAdapter(
            k8s_api_client=api,
            timeout_seconds=0,  # immediate timeout
        )
        alert = factories.make_alert()

        # When running a full investigation
        result = await adapter.investigate(alert=alert)

        # Then the result is degraded with empty findings
        assert result.findings == ()
        assert result.sources_queried == ()

        # Then the audit trail records the timeout
        actions = [entry.action for entry in result.audit_trail]
        assert "crd_timeout" in actions
        timeout_entry = next(e for e in result.audit_trail if e.action == "crd_timeout")
        assert timeout_entry.status == "error"
        assert timeout_entry.payload["timeout_seconds"] == 0

    async def test_timeout_records_poll_count(self) -> None:
        # Given an adapter with zero timeout (times out before any poll)
        pending = _make_crd_response(phase="Pending")
        api = _build_mock_api()
        api.get_namespaced_custom_object_status.return_value = pending
        api.get_namespaced_custom_object_status.side_effect = None

        adapter = kagent_adapter.KagentAdapter(
            k8s_api_client=api,
            timeout_seconds=0,
        )
        alert = factories.make_alert()

        # When running a full investigation
        result = await adapter.investigate(alert=alert)

        # Then the timeout audit entry records the poll count
        timeout_entry = next(e for e in result.audit_trail if e.action == "crd_timeout")
        assert "poll_count" in timeout_entry.payload


# ---------------------------------------------------------------------------
# Result parsing from CRD status
# ---------------------------------------------------------------------------


class TestKagentResultParsing:
    """Verify mapping from CRD .status.result to InvestigationResult."""

    async def test_parses_findings_with_all_fields(self) -> None:
        # Given a CRD with fully populated findings
        completed = _make_crd_response(
            phase="Completed",
            findings=[
                {
                    "source": "prometheus",
                    "summary": "Memory usage at 98%",
                    "raw_data": "mem_usage_bytes{pod='web-1'} 1073741824",
                    "relevance": 0.95,
                },
            ],
            sources_queried=["prometheus"],
        )
        api = _build_mock_api(poll_responses=[completed])
        adapter = kagent_adapter.KagentAdapter(
            k8s_api_client=api,
            timeout_seconds=60,
        )
        alert = factories.make_alert()

        # When running a full investigation
        result = await adapter.investigate(alert=alert)

        # Then the finding has all fields mapped correctly
        assert len(result.findings) == 1
        finding = result.findings[0]
        assert finding.source == "prometheus"
        assert finding.summary == "Memory usage at 98%"
        assert finding.raw_data == "mem_usage_bytes{pod='web-1'} 1073741824"
        assert finding.relevance == 0.95

    async def test_parses_findings_with_missing_optional_fields(self) -> None:
        # Given a CRD with findings missing optional fields
        completed = _make_crd_response(
            phase="Completed",
            findings=[
                {"source": "logs", "summary": "Error found"},
            ],
            sources_queried=["logs"],
        )
        api = _build_mock_api(poll_responses=[completed])
        adapter = kagent_adapter.KagentAdapter(
            k8s_api_client=api,
            timeout_seconds=60,
        )
        alert = factories.make_alert()

        # When running a full investigation
        result = await adapter.investigate(alert=alert)

        # Then the finding uses defaults for missing fields
        finding = result.findings[0]
        assert finding.raw_data is None
        assert finding.relevance == 0.0

    async def test_parse_audit_entry_stores_raw_crd_output(self) -> None:
        # Given a CRD with findings
        completed = _make_crd_response(
            phase="Completed",
            findings=[{"source": "events", "summary": "Restart detected"}],
            sources_queried=["events"],
        )
        api = _build_mock_api(poll_responses=[completed])
        adapter = kagent_adapter.KagentAdapter(
            k8s_api_client=api,
            timeout_seconds=60,
        )
        alert = factories.make_alert()

        # When running a full investigation
        result = await adapter.investigate(alert=alert)

        # Then the crd_parse audit entry stores the raw CRD status
        parse_entry = next(e for e in result.audit_trail if e.action == "crd_parse")
        assert "raw_status" in parse_entry.payload
        assert parse_entry.payload["raw_status"]["phase"] == "Completed"
        assert parse_entry.payload["findings_count"] == 1
        assert parse_entry.payload["sources_count"] == 1
