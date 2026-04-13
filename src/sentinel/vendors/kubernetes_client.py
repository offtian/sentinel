"""
Kubernetes vendor adapter implementing the K8sClient protocol.

Uses ``kubernetes_asyncio`` for async cluster queries. Gracefully degrades
to unconfigured state when no cluster config is available (in-cluster or
kubeconfig), following the project's no-op vendor adapter pattern.
"""

from __future__ import annotations

import re
from typing import Any

from kubernetes_asyncio import client, config
from kubernetes_asyncio.client import exceptions as k8s_exceptions

from sentinel.utils import logs


# Re-export for test convenience -- callers can reference
# ``kubernetes_client.ApiException`` without reaching into the library.
ApiException = k8s_exceptions.ApiException

_REQUEST_TIMEOUT = 10

_SUPPORTED_DESCRIBE_KINDS: dict[str, str] = {
    "Pod": "read_namespaced_pod",
    "Deployment": "read_namespaced_deployment",
    "Service": "read_namespaced_service",
    "ConfigMap": "read_namespaced_config_map",
    "StatefulSet": "read_namespaced_stateful_set",
    "DaemonSet": "read_namespaced_daemon_set",
    "ReplicaSet": "read_namespaced_replica_set",
}

# Strict K8s resource name pattern — used to validate user-supplied names
# before interpolation into field selectors.
_K8S_NAME_PATTERN_STR = r"^[a-z0-9]([a-z0-9.\-]{0,251}[a-z0-9])?$"

_APPS_KINDS = frozenset({"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"})


class KubernetesClient:
    """
    Async Kubernetes client adapter satisfying the ``K8sClient`` protocol.

    Prefer the ``create()`` async classmethod which attempts both in-cluster
    and kubeconfig loading.  The synchronous constructor only attempts
    in-cluster config.
    """

    def __init__(self) -> None:
        self._configured = False
        self._core: client.CoreV1Api | None = None
        self._apps: client.AppsV1Api | None = None

        try:
            config.load_incluster_config()  # type: ignore[no-untyped-call]
        except config.ConfigException:
            logs.log_event(
                "k8s_incluster_config_unavailable",
                params={"fallback": "load_kube_config via create()"},
            )
            return

        self._initialise_api_clients()

    @classmethod
    async def create(cls) -> KubernetesClient:
        """
        Create a KubernetesClient, trying in-cluster config first then
        kubeconfig (async) as a fallback.
        """
        instance = cls()
        if instance.is_configured:
            logs.log_event("k8s_client_configured", params={"method": "incluster"})
            return instance

        try:
            await config.load_kube_config()
            instance._initialise_api_clients()
            logs.log_event("k8s_client_configured", params={"method": "kubeconfig"})
        except config.ConfigException:
            logs.log_event(
                "k8s_client_not_configured",
                params={"reason": "No incluster or kubeconfig available"},
            )

        return instance

    def _initialise_api_clients(self) -> None:
        self._core = client.CoreV1Api()
        self._apps = client.AppsV1Api()
        self._configured = True

    @property
    def is_configured(self) -> bool:
        return self._configured

    async def get_pod_status(self, *, namespace: str, pod_name: str) -> dict[str, Any]:
        """
        Return a dict describing a pod's status including phase, restart
        count, and conditions.

        :raises ApiException: if the Kubernetes API returns an error
        """
        if self._core is None:
            raise RuntimeError("KubernetesClient not configured — check is_configured first")
        pod = await self._core.read_namespaced_pod(
            name=pod_name,
            namespace=namespace,
            _request_timeout=_REQUEST_TIMEOUT,
        )

        restart_count = 0
        if pod.status.container_statuses:
            restart_count = sum(cs.restart_count for cs in pod.status.container_statuses)

        conditions = [
            {
                "type": cond.type,
                "status": cond.status,
                "reason": cond.reason,
            }
            for cond in (pod.status.conditions or [])
        ]

        return {
            "name": pod.metadata.name,
            "namespace": pod.metadata.namespace,
            "phase": pod.status.phase,
            "restart_count": restart_count,
            "conditions": conditions,
        }

    async def get_deployment_status(
        self, *, namespace: str, deployment_name: str
    ) -> dict[str, Any]:
        """
        Return a dict describing a deployment's rollout status.

        :raises ApiException: if the Kubernetes API returns an error
        """
        if self._apps is None:
            raise RuntimeError("KubernetesClient not configured — check is_configured first")
        deploy = await self._apps.read_namespaced_deployment(
            name=deployment_name,
            namespace=namespace,
            _request_timeout=_REQUEST_TIMEOUT,
        )

        conditions = [
            {
                "type": cond.type,
                "status": cond.status,
                "reason": cond.reason,
            }
            for cond in (deploy.status.conditions or [])
        ]

        return {
            "name": deploy.metadata.name,
            "namespace": deploy.metadata.namespace,
            "replicas": deploy.spec.replicas,
            "ready_replicas": deploy.status.ready_replicas or 0,
            "available_replicas": deploy.status.available_replicas or 0,
            "unavailable_replicas": deploy.status.unavailable_replicas or 0,
            "conditions": conditions,
        }

    async def get_recent_events(
        self,
        *,
        namespace: str,
        resource_name: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Return a list of recent event dicts for the named resource.

        :raises ApiException: if the Kubernetes API returns an error
        """
        if self._core is None:
            raise RuntimeError("KubernetesClient not configured — check is_configured first")
        if not re.match(_K8S_NAME_PATTERN_STR, resource_name):
            msg = f"Invalid Kubernetes resource name: {resource_name!r}"
            raise ValueError(msg)
        field_selector = f"involvedObject.name={resource_name}"
        event_list = await self._core.list_namespaced_event(
            namespace=namespace,
            field_selector=field_selector,
            limit=limit,
            _request_timeout=_REQUEST_TIMEOUT,
        )

        return [
            {
                "type": event.type,
                "reason": event.reason,
                "message": event.message,
                "last_timestamp": str(event.last_timestamp) if event.last_timestamp else "",
                "count": event.count or 1,
            }
            for event in event_list.items
        ]

    async def get_pod_logs(
        self,
        *,
        namespace: str,
        pod_name: str,
        container: str | None = None,
        tail_lines: int = 100,
    ) -> str:
        """
        Return recent container logs for a pod as a string.

        :raises ApiException: if the Kubernetes API returns an error
        """
        if self._core is None:
            raise RuntimeError("KubernetesClient not configured — check is_configured first")
        kwargs: dict[str, Any] = {
            "name": pod_name,
            "namespace": namespace,
            "tail_lines": tail_lines,
            "_request_timeout": _REQUEST_TIMEOUT,
        }
        if container is not None:
            kwargs["container"] = container
        return await self._core.read_namespaced_pod_log(**kwargs)

    async def describe_resource(self, *, namespace: str, kind: str, name: str) -> dict[str, Any]:
        """
        Return a dict representation of a Kubernetes resource.

        Supports Pod, Deployment, Service, ConfigMap, StatefulSet,
        DaemonSet, and ReplicaSet.

        :raises ValueError: if the resource kind is not supported
        :raises ApiException: if the Kubernetes API returns an error
        """
        api_client, method_name = self._resolve_describe_target(kind=kind)

        method = getattr(api_client, method_name)
        resource = await method(
            name=name,
            namespace=namespace,
            _request_timeout=_REQUEST_TIMEOUT,
        )

        result: dict[str, Any] = resource.to_dict()
        return result

    def _resolve_describe_target(
        self, *, kind: str
    ) -> tuple[client.CoreV1Api | client.AppsV1Api, str]:
        """
        Map a resource kind to the appropriate API client and method name.

        :raises ValueError: if the kind is not in ``_SUPPORTED_DESCRIBE_KINDS``
        """
        method_name = _SUPPORTED_DESCRIBE_KINDS.get(kind)
        if method_name is None:
            raise ValueError(
                f"Unsupported resource kind: {kind!r}. "
                f"Supported: {sorted(_SUPPORTED_DESCRIBE_KINDS)}"
            )

        apps_kinds = _APPS_KINDS
        if kind in apps_kinds:
            if self._apps is None:
                raise RuntimeError("KubernetesClient not configured — check is_configured first")
            return self._apps, method_name

        if self._core is None:
            raise RuntimeError("KubernetesClient not configured — check is_configured first")
        return self._core, method_name
