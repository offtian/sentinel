"""
K8s Investigator PydanticAI agent.

Analyses Kubernetes cluster state to diagnose production incidents.
Uses K8s tools (pod status, deployment status, events, logs) injected
at runtime via toolsets.
"""

from __future__ import annotations

import dataclasses

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from sentinel.domain import prompts
from sentinel.interfaces.graphs.agents import utils


class K8sInvestigationOutput(BaseModel):
    """Structured output from the K8s investigator agent."""

    root_cause: str
    confidence: float
    evidence: list[str]
    remediation_steps: list[str]
    affected_resources: list[str]
    timeline: str


@dataclasses.dataclass
class Dependencies:
    alert_title: str
    alert_description: str
    alert_severity: str
    service: str
    cluster_name: str
    namespace: str | None = None


BASE_SYSTEM_PROMPT = prompts.load_system_prompt("k8s_investigator")


def _build_k8s_context(ctx: RunContext[Dependencies]) -> str:
    return prompts.render_user_prompt(
        "k8s_investigator",
        alert_title=ctx.deps.alert_title,
        alert_description=ctx.deps.alert_description,
        alert_severity=ctx.deps.alert_severity,
        service=ctx.deps.service,
        cluster_name=ctx.deps.cluster_name,
        namespace=ctx.deps.namespace,
    )


def build_agent(
    *, model: str | None = None, skills: tuple[str, ...] = ()
) -> Agent[Dependencies, K8sInvestigationOutput]:
    """
    Build the K8s investigator agent with configured skills baked in.
    """
    system_prompt = utils.compose_system_prompt(base_prompt=BASE_SYSTEM_PROMPT, skill_names=skills)
    agent_instance: Agent[Dependencies, K8sInvestigationOutput] = Agent(
        model or "test",
        deps_type=Dependencies,
        output_type=K8sInvestigationOutput,
        system_prompt=system_prompt,
        instrument=True,
    )
    agent_instance.instructions(_build_k8s_context)
    return agent_instance
