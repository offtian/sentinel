from __future__ import annotations

from unittest import mock

import pytest

from sentinel.domain.investigations import adapters, k8s_native_agent
from tests import factories


class TestNativeK8sAgent:
    def test_is_configured_when_k8s_client_available(self) -> None:
        # Given a mock K8s client that is configured
        mock_client = mock.AsyncMock()
        mock_client.is_configured = True

        # When creating the agent adapter
        adapter = k8s_native_agent.NativeK8sAgent(k8s_client=mock_client)

        # Then it reports as configured
        assert adapter.is_configured is True

    def test_is_not_configured_when_client_is_none(self) -> None:
        # Given no K8s client

        # When creating the agent adapter
        adapter = k8s_native_agent.NativeK8sAgent(k8s_client=None)

        # Then it reports as not configured
        assert adapter.is_configured is False

    def test_is_not_configured_when_client_reports_unconfigured(self) -> None:
        # Given a K8s client that reports itself as not configured
        mock_client = mock.AsyncMock()
        mock_client.is_configured = False

        # When creating the agent adapter
        adapter = k8s_native_agent.NativeK8sAgent(k8s_client=mock_client)

        # Then it reports as not configured
        assert adapter.is_configured is False

    @pytest.mark.asyncio
    async def test_returns_unconfigured_result_when_client_missing(self) -> None:
        # Given an unconfigured adapter
        adapter = k8s_native_agent.NativeK8sAgent(k8s_client=None)
        alert = factories.make_alert(title="Pod CrashLoopBackOff", service="payments")

        # When investigating
        result = await adapter.investigate(alert=alert)

        # Then a degraded result with audit trail is returned
        assert result.adapter_name == "native_k8s"
        assert result.findings == ()
        assert len(result.audit_trail) == 1
        assert result.audit_trail[0].status == "error"
        assert result.audit_trail[0].action == "configuration_check"

    @pytest.mark.asyncio
    async def test_returns_investigation_result_with_findings(self) -> None:
        # Given a configured adapter with a mock agent runner
        mock_client = mock.AsyncMock()
        mock_client.is_configured = True

        mock_runner = mock.AsyncMock(
            return_value=k8s_native_agent.AgentResult(
                root_cause="OOM killed due to memory leak",
                confidence=0.85,
                evidence=["Pod restart count: 12", "OOMKilled in events"],
                remediation_steps=["Increase memory limit"],
                affected_resources=["payments-service-abc123"],
                timeline="Pod started crashing at 14:32 UTC",
                audit_entries=[],
            ),
        )

        adapter = k8s_native_agent.NativeK8sAgent(
            k8s_client=mock_client,
            model_name="openai:gpt-4.1",
            agent_runner=mock_runner,
        )
        alert = factories.make_alert(
            title="Pod CrashLoopBackOff",
            service="payments-service",
        )
        context = adapters.InvestigationContext(
            cluster_name="prod-eu-west-1",
            namespace="payments",
        )

        # When investigating
        result = await adapter.investigate(alert=alert, context=context)

        # Then findings are mapped from evidence
        assert result.adapter_name == "native_k8s"
        assert len(result.findings) == 2
        assert result.findings[0].source == "kubernetes"
        assert result.findings[0].summary == "Pod restart count: 12"
        assert result.findings[1].summary == "OOMKilled in events"
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_sets_relevance_from_agent_confidence(self) -> None:
        # Given a configured adapter returning 0.92 confidence
        mock_client = mock.AsyncMock()
        mock_client.is_configured = True

        mock_runner = mock.AsyncMock(
            return_value=k8s_native_agent.AgentResult(
                root_cause="Node disk pressure",
                confidence=0.92,
                evidence=["Disk usage at 95%"],
                remediation_steps=["Clean up disk"],
                affected_resources=["node-1"],
                timeline="Disk filled up over 2 hours",
                audit_entries=[],
            ),
        )

        adapter = k8s_native_agent.NativeK8sAgent(
            k8s_client=mock_client,
            agent_runner=mock_runner,
        )
        alert = factories.make_alert()

        # When investigating
        result = await adapter.investigate(alert=alert)

        # Then each finding's relevance matches the agent confidence
        assert result.findings[0].relevance == 0.92

    @pytest.mark.asyncio
    async def test_raises_when_configured_but_no_runner(self) -> None:
        # Given a configured adapter with no agent runner
        mock_client = mock.AsyncMock()
        mock_client.is_configured = True
        adapter = k8s_native_agent.NativeK8sAgent(k8s_client=mock_client)
        alert = factories.make_alert()

        # When investigating without an agent runner
        # Then a RuntimeError is raised
        with pytest.raises(RuntimeError, match="agent_runner must be provided"):
            await adapter.investigate(alert=alert)

    @pytest.mark.asyncio
    async def test_passes_context_to_agent_runner(self) -> None:
        # Given a configured adapter with a spy runner
        mock_client = mock.AsyncMock()
        mock_client.is_configured = True

        mock_runner = mock.AsyncMock(
            return_value=k8s_native_agent.AgentResult(
                root_cause="test",
                confidence=0.5,
                evidence=[],
                remediation_steps=[],
                affected_resources=[],
                timeline="",
                audit_entries=[],
            ),
        )

        adapter = k8s_native_agent.NativeK8sAgent(
            k8s_client=mock_client,
            model_name="openai:gpt-4.1",
            agent_runner=mock_runner,
        )
        alert = factories.make_alert()
        context = adapters.InvestigationContext(
            cluster_name="staging",
            namespace="api",
        )

        # When investigating
        await adapter.investigate(alert=alert, context=context)

        # Then the runner is called with the expected arguments
        mock_runner.assert_awaited_once()
        call_kwargs = mock_runner.call_args.kwargs
        assert call_kwargs["alert"] is alert
        assert call_kwargs["context"] is context
        assert call_kwargs["k8s_client"] is mock_client
        assert call_kwargs["model_name"] == "openai:gpt-4.1"
