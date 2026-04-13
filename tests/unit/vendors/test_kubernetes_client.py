from __future__ import annotations

from unittest import mock

import pytest

from sentinel.domain.tools import kubernetes as k8s_protocol
from sentinel.vendors import kubernetes_client


_REQUEST_TIMEOUT = 10


class TestIsConfigured:
    def test_returns_false_when_no_cluster_config(self) -> None:
        # Given no cluster config is available (incluster raises)
        with mock.patch.object(
            kubernetes_client.config,
            "load_incluster_config",
            side_effect=kubernetes_client.config.ConfigException("no cluster"),
        ):
            client = kubernetes_client.KubernetesClient()

        # When we check is_configured
        result = client.is_configured

        # Then it should be False
        assert result is False

    def test_returns_true_when_incluster_config_loads(self) -> None:
        # Given in-cluster config loads successfully
        with (
            mock.patch.object(
                kubernetes_client.config,
                "load_incluster_config",
            ),
            mock.patch.object(kubernetes_client.client, "CoreV1Api"),
            mock.patch.object(kubernetes_client.client, "AppsV1Api"),
        ):
            client = kubernetes_client.KubernetesClient()

        # When we check is_configured
        result = client.is_configured

        # Then it should be True
        assert result is True


class TestCreateFactory:
    @pytest.mark.anyio
    async def test_configured_via_incluster(self) -> None:
        # Given in-cluster config loads successfully
        with (
            mock.patch.object(
                kubernetes_client.config,
                "load_incluster_config",
            ),
            mock.patch.object(kubernetes_client.client, "CoreV1Api"),
            mock.patch.object(kubernetes_client.client, "AppsV1Api"),
        ):
            client = await kubernetes_client.KubernetesClient.create()

        # When we check is_configured
        result = client.is_configured

        # Then it should be True
        assert result is True

    @pytest.mark.anyio
    async def test_configured_via_kubeconfig_fallback(self) -> None:
        # Given in-cluster fails but kubeconfig succeeds
        with (
            mock.patch.object(
                kubernetes_client.config,
                "load_incluster_config",
                side_effect=kubernetes_client.config.ConfigException("no cluster"),
            ),
            mock.patch.object(
                kubernetes_client.config,
                "load_kube_config",
                new_callable=mock.AsyncMock,
            ),
            mock.patch.object(kubernetes_client.client, "CoreV1Api"),
            mock.patch.object(kubernetes_client.client, "AppsV1Api"),
        ):
            client = await kubernetes_client.KubernetesClient.create()

        # When we check is_configured
        result = client.is_configured

        # Then it should be True
        assert result is True

    @pytest.mark.anyio
    async def test_not_configured_when_both_fail(self) -> None:
        # Given both config loaders raise
        with (
            mock.patch.object(
                kubernetes_client.config,
                "load_incluster_config",
                side_effect=kubernetes_client.config.ConfigException("no cluster"),
            ),
            mock.patch.object(
                kubernetes_client.config,
                "load_kube_config",
                new_callable=mock.AsyncMock,
                side_effect=kubernetes_client.config.ConfigException("no kubeconfig"),
            ),
        ):
            client = await kubernetes_client.KubernetesClient.create()

        # When we check is_configured
        result = client.is_configured

        # Then it should be False
        assert result is False


def _make_configured_client() -> tuple[
    kubernetes_client.KubernetesClient, mock.MagicMock, mock.MagicMock
]:
    """
    Return a configured KubernetesClient with mocked CoreV1Api and AppsV1Api.
    """
    with (
        mock.patch.object(
            kubernetes_client.config,
            "load_incluster_config",
        ),
        mock.patch.object(kubernetes_client.client, "CoreV1Api") as mock_core,
        mock.patch.object(kubernetes_client.client, "AppsV1Api") as mock_apps,
    ):
        client = kubernetes_client.KubernetesClient()
    return client, mock_core.return_value, mock_apps.return_value


class TestGetPodStatus:
    @pytest.mark.anyio
    async def test_returns_pod_status_dict(self) -> None:
        # Given a configured client with a mock pod response
        client, mock_core, _ = _make_configured_client()
        mock_pod = mock.MagicMock()
        mock_pod.metadata.name = "web-abc123"
        mock_pod.metadata.namespace = "default"
        mock_pod.status.phase = "Running"
        mock_pod.status.container_statuses = [
            mock.MagicMock(restart_count=2, name="web"),
        ]
        mock_pod.status.conditions = [
            mock.MagicMock(type="Ready", status="True", reason=None),
        ]
        mock_core.read_namespaced_pod = mock.AsyncMock(return_value=mock_pod)

        # When we get pod status
        result = await client.get_pod_status(namespace="default", pod_name="web-abc123")

        # Then the result contains expected fields
        assert result["name"] == "web-abc123"
        assert result["phase"] == "Running"
        assert result["restart_count"] == 2
        assert len(result["conditions"]) == 1
        assert result["conditions"][0]["type"] == "Ready"

        mock_core.read_namespaced_pod.assert_awaited_once_with(
            name="web-abc123",
            namespace="default",
            _request_timeout=_REQUEST_TIMEOUT,
        )

    @pytest.mark.anyio
    async def test_raises_on_api_error(self) -> None:
        # Given a configured client where the API raises
        client, mock_core, _ = _make_configured_client()
        mock_core.read_namespaced_pod = mock.AsyncMock(
            side_effect=kubernetes_client.ApiException(status=404, reason="Not Found")
        )

        # When we get pod status
        # Then it raises ApiException
        with pytest.raises(kubernetes_client.ApiException):
            await client.get_pod_status(namespace="default", pod_name="missing-pod")


class TestGetDeploymentStatus:
    @pytest.mark.anyio
    async def test_returns_deployment_status_dict(self) -> None:
        # Given a configured client with a mock deployment response
        client, _, mock_apps = _make_configured_client()
        mock_deploy = mock.MagicMock()
        mock_deploy.metadata.name = "web"
        mock_deploy.metadata.namespace = "default"
        mock_deploy.spec.replicas = 3
        mock_deploy.status.ready_replicas = 3
        mock_deploy.status.available_replicas = 3
        mock_deploy.status.unavailable_replicas = None
        mock_deploy.status.conditions = [
            mock.MagicMock(type="Available", status="True", reason="MinimumReplicasAvailable"),
        ]
        mock_apps.read_namespaced_deployment = mock.AsyncMock(return_value=mock_deploy)

        # When we get deployment status
        result = await client.get_deployment_status(namespace="default", deployment_name="web")

        # Then the result contains expected fields
        assert result["name"] == "web"
        assert result["replicas"] == 3
        assert result["ready_replicas"] == 3
        assert result["available_replicas"] == 3
        assert result["unavailable_replicas"] == 0
        assert result["conditions"][0]["type"] == "Available"

        mock_apps.read_namespaced_deployment.assert_awaited_once_with(
            name="web",
            namespace="default",
            _request_timeout=_REQUEST_TIMEOUT,
        )

    @pytest.mark.anyio
    async def test_raises_on_api_error(self) -> None:
        # Given a configured client where the API raises
        client, _, mock_apps = _make_configured_client()
        mock_apps.read_namespaced_deployment = mock.AsyncMock(
            side_effect=kubernetes_client.ApiException(status=404, reason="Not Found")
        )

        # When / Then
        with pytest.raises(kubernetes_client.ApiException):
            await client.get_deployment_status(namespace="default", deployment_name="missing")


class TestGetRecentEvents:
    @pytest.mark.anyio
    async def test_returns_event_list(self) -> None:
        # Given a configured client with mock events
        client, mock_core, _ = _make_configured_client()
        mock_event = mock.MagicMock()
        mock_event.type = "Warning"
        mock_event.reason = "BackOff"
        mock_event.message = "Back-off restarting failed container"
        mock_event.last_timestamp = "2026-04-12T10:00:00Z"
        mock_event.count = 5
        mock_event.involved_object.name = "web-abc123"

        mock_event_list = mock.MagicMock()
        mock_event_list.items = [mock_event]
        mock_core.list_namespaced_event = mock.AsyncMock(return_value=mock_event_list)

        # When we get recent events
        result = await client.get_recent_events(
            namespace="default", resource_name="web-abc123", limit=10
        )

        # Then the result is a list of event dicts
        assert len(result) == 1
        assert result[0]["type"] == "Warning"
        assert result[0]["reason"] == "BackOff"
        assert result[0]["count"] == 5

    @pytest.mark.anyio
    async def test_raises_on_api_error(self) -> None:
        # Given a configured client where the API raises
        client, mock_core, _ = _make_configured_client()
        mock_core.list_namespaced_event = mock.AsyncMock(
            side_effect=kubernetes_client.ApiException(status=403, reason="Forbidden")
        )

        # When / Then
        with pytest.raises(kubernetes_client.ApiException):
            await client.get_recent_events(namespace="default", resource_name="web-abc123")


class TestGetPodLogs:
    @pytest.mark.anyio
    async def test_returns_log_string(self) -> None:
        # Given a configured client with mock log output
        client, mock_core, _ = _make_configured_client()
        mock_core.read_namespaced_pod_log = mock.AsyncMock(
            return_value="2026-04-12 ERROR something broke\n2026-04-12 INFO recovered"
        )

        # When we get pod logs
        result = await client.get_pod_logs(
            namespace="default", pod_name="web-abc123", tail_lines=50
        )

        # Then the result is a string with log content
        assert "ERROR something broke" in result
        mock_core.read_namespaced_pod_log.assert_awaited_once_with(
            name="web-abc123",
            namespace="default",
            tail_lines=50,
            _request_timeout=_REQUEST_TIMEOUT,
        )

    @pytest.mark.anyio
    async def test_passes_container_name(self) -> None:
        # Given a configured client
        client, mock_core, _ = _make_configured_client()
        mock_core.read_namespaced_pod_log = mock.AsyncMock(return_value="logs here")

        # When we get pod logs with a specific container
        await client.get_pod_logs(namespace="default", pod_name="web-abc123", container="sidecar")

        # Then the container param is forwarded
        mock_core.read_namespaced_pod_log.assert_awaited_once_with(
            name="web-abc123",
            namespace="default",
            container="sidecar",
            tail_lines=100,
            _request_timeout=_REQUEST_TIMEOUT,
        )

    @pytest.mark.anyio
    async def test_raises_on_api_error(self) -> None:
        # Given a configured client where the API raises
        client, mock_core, _ = _make_configured_client()
        mock_core.read_namespaced_pod_log = mock.AsyncMock(
            side_effect=kubernetes_client.ApiException(status=404, reason="Not Found")
        )

        # When / Then
        with pytest.raises(kubernetes_client.ApiException):
            await client.get_pod_logs(namespace="default", pod_name="missing")


class TestDescribeResource:
    @pytest.mark.anyio
    async def test_describes_pod(self) -> None:
        # Given a configured client with a mock pod
        client, mock_core, _ = _make_configured_client()
        mock_pod = mock.MagicMock()
        mock_pod.to_dict.return_value = {
            "metadata": {"name": "web-abc123", "namespace": "default"},
            "kind": "Pod",
            "status": {"phase": "Running"},
        }
        mock_core.read_namespaced_pod = mock.AsyncMock(return_value=mock_pod)

        # When we describe a pod
        result = await client.describe_resource(namespace="default", kind="Pod", name="web-abc123")

        # Then the result is a dict with resource data
        assert result["metadata"]["name"] == "web-abc123"
        assert result["kind"] == "Pod"

    @pytest.mark.anyio
    async def test_describes_deployment(self) -> None:
        # Given a configured client with a mock deployment
        client, _, mock_apps = _make_configured_client()
        mock_deploy = mock.MagicMock()
        mock_deploy.to_dict.return_value = {
            "metadata": {"name": "web", "namespace": "default"},
            "kind": "Deployment",
        }
        mock_apps.read_namespaced_deployment = mock.AsyncMock(return_value=mock_deploy)

        # When we describe a deployment
        result = await client.describe_resource(namespace="default", kind="Deployment", name="web")

        # Then the result contains deployment data
        assert result["kind"] == "Deployment"

    @pytest.mark.anyio
    async def test_describes_service(self) -> None:
        # Given a configured client with a mock service
        client, mock_core, _ = _make_configured_client()
        mock_svc = mock.MagicMock()
        mock_svc.to_dict.return_value = {
            "metadata": {"name": "web-svc", "namespace": "default"},
            "kind": "Service",
        }
        mock_core.read_namespaced_service = mock.AsyncMock(return_value=mock_svc)

        # When we describe a service
        result = await client.describe_resource(
            namespace="default", kind="Service", name="web-svc"
        )

        # Then the result contains service data
        assert result["kind"] == "Service"

    @pytest.mark.anyio
    async def test_raises_on_unsupported_kind(self) -> None:
        # Given a configured client
        client, _, _ = _make_configured_client()

        # When we describe an unsupported kind
        # Then it raises ValueError
        with pytest.raises(ValueError, match="Unsupported resource kind"):
            await client.describe_resource(namespace="default", kind="CronJob", name="myjob")

    @pytest.mark.anyio
    async def test_raises_on_api_error(self) -> None:
        # Given a configured client where the API raises
        client, mock_core, _ = _make_configured_client()
        mock_core.read_namespaced_pod = mock.AsyncMock(
            side_effect=kubernetes_client.ApiException(status=404, reason="Not Found")
        )

        # When / Then
        with pytest.raises(kubernetes_client.ApiException):
            await client.describe_resource(namespace="default", kind="Pod", name="missing")


class TestProtocolCompliance:
    def test_satisfies_k8s_client_protocol(self) -> None:
        # Given a configured client
        client, _, _ = _make_configured_client()

        # When we check protocol compliance
        result = isinstance(client, k8s_protocol.K8sClient)

        # Then it satisfies the K8sClient protocol
        assert result is True
