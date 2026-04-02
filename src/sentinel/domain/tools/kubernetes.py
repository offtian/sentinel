"""
Read-only Kubernetes query tools for SRE investigation agents.

Each function queries a Kubernetes cluster and returns a human-readable
summary string.  Functions are intentionally framework-agnostic — they
accept typed parameters and return ``str``, making them testable without
PydanticAI.

Callers are responsible for supplying a configured ``K8sClient``.  When
the client is ``None`` or not configured, every function returns a
descriptive fallback message instead of raising.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from sentinel.utils import logs


@runtime_checkable
class K8sClient(Protocol):
    """
    Protocol that Kubernetes vendor adapters must satisfy.
    """

    @property
    def is_configured(self) -> bool: ...

    async def get_pod_status(self, *, namespace: str, pod_name: str) -> dict[str, Any]: ...

    async def get_deployment_status(
        self, *, namespace: str, deployment_name: str
    ) -> dict[str, Any]: ...

    async def get_recent_events(
        self, *, namespace: str, resource_name: str, limit: int = 20
    ) -> list[dict[str, Any]]: ...

    async def get_pod_logs(
        self,
        *,
        namespace: str,
        pod_name: str,
        container: str | None = None,
        tail_lines: int = 100,
    ) -> str: ...

    async def describe_resource(
        self, *, namespace: str, kind: str, name: str
    ) -> dict[str, Any]: ...


async def get_pod_status(
    *,
    client: K8sClient | None,
    namespace: str,
    pod_name: str,
) -> str:
    """
    Return a human-readable summary of a pod's status.

    Include phase, restart count, and conditions.
    """
    if client is None or not client.is_configured:
        return "Kubernetes client not available. Unable to query pod status."

    try:
        data = await client.get_pod_status(namespace=namespace, pod_name=pod_name)
    except Exception as exc:
        logs.log_exception(exc, params={"tool": "get_pod_status", "pod_name": pod_name})
        return f"Pod status query failed: {type(exc).__name__} — {exc}"

    name = data.get("name", pod_name)
    phase = data.get("phase", "Unknown")
    restart_count = data.get("restart_count", 0)
    conditions = data.get("conditions", [])

    lines = [
        f"Pod: {name}",
        f"Phase: {phase}",
        f"Restart count: {restart_count}",
    ]

    if conditions:
        lines.append("Conditions:")
        for cond in conditions:
            cond_type = cond.get("type", "?")
            status = cond.get("status", "?")
            reason = cond.get("reason", "")
            suffix = f" ({reason})" if reason else ""
            lines.append(f"  {cond_type}: {status}{suffix}")

    return "\n".join(lines)


async def get_deployment_status(
    *,
    client: K8sClient | None,
    namespace: str,
    deployment_name: str,
) -> str:
    """
    Return a human-readable summary of a deployment's rollout status.

    Include desired, ready, available, and unavailable replica counts.
    """
    if client is None or not client.is_configured:
        return "Kubernetes client not available. Unable to query deployment status."

    try:
        data = await client.get_deployment_status(
            namespace=namespace, deployment_name=deployment_name
        )
    except Exception as exc:
        logs.log_exception(
            exc, params={"tool": "get_deployment_status", "deployment_name": deployment_name}
        )
        return f"Deployment status query failed: {type(exc).__name__} — {exc}"

    name = data.get("name", deployment_name)
    replicas = data.get("replicas", 0)
    ready = data.get("ready_replicas", 0)
    available = data.get("available_replicas", 0)
    unavailable = data.get("unavailable_replicas", 0)
    conditions = data.get("conditions", [])

    lines = [
        f"Deployment: {name}",
        f"Replicas: {replicas} desired, {ready} ready, {available} available, {unavailable} unavailable",
    ]

    if conditions:
        lines.append("Conditions:")
        for cond in conditions:
            cond_type = cond.get("type", "?")
            status = cond.get("status", "?")
            reason = cond.get("reason", "")
            suffix = f" ({reason})" if reason else ""
            lines.append(f"  {cond_type}: {status}{suffix}")

    return "\n".join(lines)


async def get_recent_events(
    *,
    client: K8sClient | None,
    namespace: str,
    resource_name: str,
    limit: int = 20,
) -> str:
    """
    Return a human-readable summary of recent Kubernetes events for a resource.
    """
    if client is None or not client.is_configured:
        return "Kubernetes client not available. Unable to query events."

    try:
        events = await client.get_recent_events(
            namespace=namespace, resource_name=resource_name, limit=limit
        )
    except Exception as exc:
        logs.log_exception(
            exc, params={"tool": "get_recent_events", "resource_name": resource_name}
        )
        return f"Events query failed: {type(exc).__name__} — {exc}"

    if not events:
        return f"No recent events found for '{resource_name}' in namespace '{namespace}'."

    lines = [f"Found {len(events)} events for '{resource_name}':"]
    for event in events:
        event_type = event.get("type", "?")
        reason = event.get("reason", "?")
        message = str(event.get("message", ""))[:200]
        ts = event.get("last_timestamp", "")
        count = event.get("count", 1)
        count_suffix = f" (x{count})" if count > 1 else ""
        lines.append(f"  [{ts}] {event_type}/{reason}: {message}{count_suffix}")

    return "\n".join(lines)


async def get_pod_logs(
    *,
    client: K8sClient | None,
    namespace: str,
    pod_name: str,
    container: str | None = None,
    tail_lines: int = 100,
) -> str:
    """
    Return recent container logs for a pod.
    """
    if client is None or not client.is_configured:
        return "Kubernetes client not available. Unable to query pod logs."

    try:
        log_output = await client.get_pod_logs(
            namespace=namespace,
            pod_name=pod_name,
            container=container,
            tail_lines=tail_lines,
        )
    except Exception as exc:
        logs.log_exception(exc, params={"tool": "get_pod_logs", "pod_name": pod_name})
        return f"Pod logs query failed: {type(exc).__name__} — {exc}"

    if not log_output or not log_output.strip():
        return f"No logs found for pod '{pod_name}' in namespace '{namespace}'."

    container_label = f" (container: {container})" if container else ""
    header = f"Logs for pod '{pod_name}'{container_label}:"
    return f"{header}\n{log_output}"


async def describe_resource(
    *,
    client: K8sClient | None,
    namespace: str,
    kind: str,
    name: str,
) -> str:
    """
    Return a human-readable description of a Kubernetes resource.
    """
    if client is None or not client.is_configured:
        return "Kubernetes client not available. Unable to describe resource."

    try:
        data = await client.describe_resource(namespace=namespace, kind=kind, name=name)
    except Exception as exc:
        logs.log_exception(exc, params={"tool": "describe_resource", "kind": kind, "name": name})
        return f"Describe resource failed: {type(exc).__name__} — {exc}"

    if not data:
        return f"No data returned for {kind}/{name} in namespace '{namespace}'."

    resource_kind = data.get("kind", kind)
    resource_name = data.get("name", name)
    resource_ns = data.get("namespace", namespace)

    lines = [f"{resource_kind}: {resource_name} (namespace: {resource_ns})"]

    # Render remaining fields as key-value pairs
    skip_keys = {"kind", "name", "namespace"}
    for key, value in data.items():
        if key in skip_keys:
            continue
        if isinstance(value, list | dict):
            lines.append(f"  {key}: {json.dumps(value)}")
        else:
            lines.append(f"  {key}: {value}")

    return "\n".join(lines)
