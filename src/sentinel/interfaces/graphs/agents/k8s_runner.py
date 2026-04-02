"""
Runner function for the K8s investigator PydanticAI agent.

Lives in the interfaces layer so it can import the agent definition
and plugin toolsets.  Injected into ``NativeK8sAgent`` via the
``agent_runner`` parameter at configuration time.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sentinel.domain.sre import entities, investigation, k8s_native_agent
from sentinel.domain.tools import kubernetes as k8s_tools
from sentinel.interfaces.graphs.agents import k8s_investigator, utils
from sentinel.plugins.toolsets import kubernetes as k8s_toolset_mod


async def run_k8s_agent(
    *,
    alert: entities.Alert,
    context: investigation.InvestigationContext | None,
    k8s_client: k8s_tools.K8sClient | None,
    model_name: str,
    mcp_toolsets: Sequence[Any],
) -> k8s_native_agent.AgentResult:
    """
    Run the PydanticAI K8s investigator agent with toolsets.

    Separated from the domain adapter for layer boundary compliance.
    """
    namespace = context.namespace or "default" if context else "default"
    cluster_name = context.cluster_name if context else "unknown"

    k8s_toolset = k8s_toolset_mod.build_kubernetes_toolset(
        k8s_client=k8s_client,
        namespace=namespace,
    )

    toolsets: list[Any] = [k8s_toolset, *mcp_toolsets]

    deps = k8s_investigator.Dependencies(
        alert_title=alert.title,
        alert_description=alert.description,
        alert_severity=alert.severity.value,
        service=alert.service,
        cluster_name=cluster_name,
        namespace=namespace,
    )

    result = await k8s_investigator.agent.run(
        deps=deps,
        model=utils.get_model_with_gateway(model_name),
        toolsets=toolsets,
    )

    output = result.output
    return k8s_native_agent.AgentResult(
        root_cause=output.root_cause,
        confidence=output.confidence,
        evidence=output.evidence,
        remediation_steps=output.remediation_steps,
        affected_resources=output.affected_resources,
        timeline=output.timeline,
        audit_entries=[],
    )
