"""
Unit tests for domain Kubernetes tool functions.

These functions are framework-agnostic (no PydanticAI dependency) so
tests validate raw input/output behaviour with mock clients.
"""

from __future__ import annotations

from unittest import mock

import pytest

from sentinel.domain.tools import kubernetes as k8s_tools


class TestGetPodStatus:
    @pytest.mark.asyncio
    async def test_returns_fallback_when_client_is_none(self) -> None:
        # Given no kubernetes client

        # When querying pod status
        result = await k8s_tools.get_pod_status(
            client=None, namespace="default", pod_name="api-abc123"
        )

        # Then a fallback message is returned
        assert "not available" in result.lower()

    @pytest.mark.asyncio
    async def test_returns_pod_status_summary(self) -> None:
        # Given a configured kubernetes client with pod data
        mock_client = mock.AsyncMock()
        mock_client.is_configured = True
        mock_client.get_pod_status.return_value = {
            "name": "api-abc123",
            "phase": "Running",
            "restart_count": 5,
            "conditions": [
                {"type": "Ready", "status": "False", "reason": "ContainersNotReady"},
            ],
        }

        # When querying pod status
        result = await k8s_tools.get_pod_status(
            client=mock_client, namespace="production", pod_name="api-abc123"
        )

        # Then the summary includes key details
        assert "api-abc123" in result
        assert "Running" in result
        assert "5" in result


class TestGetDeploymentStatus:
    @pytest.mark.asyncio
    async def test_returns_fallback_when_client_is_none(self) -> None:
        # Given no kubernetes client

        # When querying deployment status
        result = await k8s_tools.get_deployment_status(
            client=None, namespace="default", deployment_name="api"
        )

        # Then a fallback message is returned
        assert "not available" in result.lower()

    @pytest.mark.asyncio
    async def test_returns_deployment_status_summary(self) -> None:
        # Given a configured kubernetes client with deployment data
        mock_client = mock.AsyncMock()
        mock_client.is_configured = True
        mock_client.get_deployment_status.return_value = {
            "name": "api",
            "replicas": 3,
            "ready_replicas": 2,
            "available_replicas": 2,
            "unavailable_replicas": 1,
            "conditions": [
                {"type": "Available", "status": "True", "reason": "MinimumReplicasAvailable"},
            ],
        }

        # When querying deployment status
        result = await k8s_tools.get_deployment_status(
            client=mock_client, namespace="production", deployment_name="api"
        )

        # Then the summary includes replica counts
        assert "api" in result
        assert "3" in result
        assert "2" in result


class TestGetRecentEvents:
    @pytest.mark.asyncio
    async def test_returns_fallback_when_client_is_none(self) -> None:
        # Given no kubernetes client

        # When querying recent events
        result = await k8s_tools.get_recent_events(
            client=None, namespace="default", resource_name="api-abc123"
        )

        # Then a fallback message is returned
        assert "not available" in result.lower()

    @pytest.mark.asyncio
    async def test_returns_formatted_events(self) -> None:
        # Given a configured kubernetes client with event data
        mock_client = mock.AsyncMock()
        mock_client.is_configured = True
        mock_client.get_recent_events.return_value = [
            {
                "type": "Warning",
                "reason": "BackOff",
                "message": "Back-off restarting failed container",
                "last_timestamp": "2026-04-01T10:00:00Z",
                "count": 12,
            },
            {
                "type": "Normal",
                "reason": "Pulled",
                "message": "Container image pulled successfully",
                "last_timestamp": "2026-04-01T09:55:00Z",
                "count": 1,
            },
        ]

        # When querying recent events
        result = await k8s_tools.get_recent_events(
            client=mock_client, namespace="production", resource_name="api-abc123"
        )

        # Then the summary includes event details
        assert "BackOff" in result
        assert "Back-off restarting failed container" in result
        assert "Warning" in result


class TestGetPodLogs:
    @pytest.mark.asyncio
    async def test_returns_fallback_when_client_is_none(self) -> None:
        # Given no kubernetes client

        # When querying pod logs
        result = await k8s_tools.get_pod_logs(
            client=None, namespace="default", pod_name="api-abc123"
        )

        # Then a fallback message is returned
        assert "not available" in result.lower()

    @pytest.mark.asyncio
    async def test_returns_pod_log_output(self) -> None:
        # Given a configured kubernetes client with log output
        mock_client = mock.AsyncMock()
        mock_client.is_configured = True
        mock_client.get_pod_logs.return_value = (
            "2026-04-01T10:00:00Z ERROR Connection refused to database\n"
            "2026-04-01T10:00:01Z INFO Retrying connection...\n"
        )

        # When querying pod logs
        result = await k8s_tools.get_pod_logs(
            client=mock_client, namespace="production", pod_name="api-abc123"
        )

        # Then the log output is included
        assert "Connection refused" in result
        assert "api-abc123" in result


class TestDescribeResource:
    @pytest.mark.asyncio
    async def test_returns_fallback_when_client_is_none(self) -> None:
        # Given no kubernetes client

        # When describing a resource
        result = await k8s_tools.describe_resource(
            client=None, namespace="default", kind="Service", name="api-svc"
        )

        # Then a fallback message is returned
        assert "not available" in result.lower()

    @pytest.mark.asyncio
    async def test_returns_resource_description(self) -> None:
        # Given a configured kubernetes client with resource data
        mock_client = mock.AsyncMock()
        mock_client.is_configured = True
        mock_client.describe_resource.return_value = {
            "kind": "Service",
            "name": "api-svc",
            "namespace": "production",
            "type": "ClusterIP",
            "cluster_ip": "10.96.0.1",
            "ports": [{"port": 80, "target_port": 8000, "protocol": "TCP"}],
        }

        # When describing a resource
        result = await k8s_tools.describe_resource(
            client=mock_client, namespace="production", kind="Service", name="api-svc"
        )

        # Then the description includes key details
        assert "Service" in result
        assert "api-svc" in result
