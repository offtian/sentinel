from __future__ import annotations

from unittest import mock

import pytest

from sentinel.domain.sre import investigation, kagent_adapter
from tests import factories


class TestKagentAdapter:
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

    @pytest.mark.asyncio
    async def test_returns_placeholder_result_when_configured(self) -> None:
        # Given a configured adapter (operator not yet deployed)
        mock_client = mock.MagicMock()
        adapter = kagent_adapter.KagentAdapter(k8s_api_client=mock_client)
        alert = factories.make_alert(title="Node NotReady")

        # When investigating
        result = await adapter.investigate(alert=alert)

        # Then a placeholder result with audit trail is returned
        assert result.adapter_name == "kagent"
        assert len(result.audit_trail) == 1
        assert result.audit_trail[0].action == "crd_operation"
        assert "pending" in result.audit_trail[0].payload["reason"].lower()

    @pytest.mark.asyncio
    async def test_stores_alert_id_in_audit_payload(self) -> None:
        # Given a configured adapter
        mock_client = mock.MagicMock()
        adapter = kagent_adapter.KagentAdapter(k8s_api_client=mock_client)
        alert = factories.make_alert(alert_id="ALERT-42")

        # When investigating
        result = await adapter.investigate(alert=alert)

        # Then the alert ID is in the audit payload
        assert result.audit_trail[0].payload["alert_id"] == "ALERT-42"
