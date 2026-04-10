"""
Kubernetes toolset for K8s investigation agents.

Wraps the domain tool functions from ``sentinel.domain.tools.kubernetes``
into a PydanticAI ``FunctionToolset`` that can be injected at
``agent.run(toolsets=[...])`` time.

All tools are read-only and no-op when the K8s client is unavailable.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from sentinel.domain.tools import kubernetes as k8s_tools


def build_kubernetes_toolset(
    *,
    k8s_client: k8s_tools.K8sClient | None,
    namespace: str = "default",
) -> FunctionToolset[Any]:
    """
    Build a read-only toolset for querying Kubernetes cluster state.

    :param k8s_client: The configured K8s API client, or None.
    :param namespace: Default namespace for queries.
    """
    toolset: FunctionToolset[Any] = FunctionToolset()

    @toolset.tool
    async def get_pod_status(ctx: RunContext[Any], pod_name: str, ns: str = namespace) -> str:
        """
        Get the current status of a Kubernetes pod.
        Returns phase, restart count, and conditions.

        Args:
            ctx: PydanticAI run context (injected automatically).
            pod_name: Name of the pod to check.
            ns: Kubernetes namespace. Defaults to the alerted namespace.
        """
        return await k8s_tools.get_pod_status(client=k8s_client, namespace=ns, pod_name=pod_name)

    @toolset.tool
    async def get_deployment_status(
        ctx: RunContext[Any], deployment_name: str, ns: str = namespace
    ) -> str:
        """
        Get the rollout status of a Kubernetes deployment.
        Returns replica counts, update status, and conditions.

        Args:
            ctx: PydanticAI run context (injected automatically).
            deployment_name: Name of the deployment to check.
            ns: Kubernetes namespace.
        """
        return await k8s_tools.get_deployment_status(
            client=k8s_client, namespace=ns, deployment_name=deployment_name
        )

    @toolset.tool
    async def get_recent_events(
        ctx: RunContext[Any],
        resource_name: str,
        ns: str = namespace,
        limit: int = 20,
    ) -> str:
        """
        Get recent Kubernetes events for a resource.
        Returns warnings, errors, and other events.

        Args:
            ctx: PydanticAI run context (injected automatically).
            resource_name: Name of the K8s resource.
            ns: Kubernetes namespace.
            limit: Maximum number of events to return.
        """
        return await k8s_tools.get_recent_events(
            client=k8s_client,
            namespace=ns,
            resource_name=resource_name,
            limit=limit,
        )

    @toolset.tool
    async def get_pod_logs(
        ctx: RunContext[Any],
        pod_name: str,
        ns: str = namespace,
        container: str | None = None,
        tail_lines: int = 100,
    ) -> str:
        """
        Get the last N lines of logs from a pod's container.

        Args:
            ctx: PydanticAI run context (injected automatically).
            pod_name: Name of the pod.
            ns: Kubernetes namespace.
            container: Specific container name (for multi-container pods).
            tail_lines: Number of lines to return from the end of the log.
        """
        return await k8s_tools.get_pod_logs(
            client=k8s_client,
            namespace=ns,
            pod_name=pod_name,
            container=container,
            tail_lines=tail_lines,
        )

    @toolset.tool
    async def describe_resource(
        ctx: RunContext[Any], kind: str, name: str, ns: str = namespace
    ) -> str:
        """
        Describe any Kubernetes resource (pod, service, ingress, pvc, etc.).

        Args:
            ctx: PydanticAI run context (injected automatically).
            kind: Resource kind (e.g. "pod", "service", "ingress", "pvc").
            name: Resource name.
            ns: Kubernetes namespace.
        """
        return await k8s_tools.describe_resource(
            client=k8s_client, namespace=ns, kind=kind, name=name
        )

    return toolset
