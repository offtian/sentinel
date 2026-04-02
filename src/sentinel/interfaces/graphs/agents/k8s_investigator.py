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

from sentinel.plugins import prompts


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


SYSTEM_PROMPT = prompts.load_system_prompt("k8s_investigator")


agent: Agent[Dependencies, K8sInvestigationOutput] = Agent(
    "test",  # Overridden at call site with the configured LiteLLM model.
    deps_type=Dependencies,
    output_type=K8sInvestigationOutput,
    system_prompt=SYSTEM_PROMPT,
    instrument=True,
)


@agent.instructions
def build_k8s_context(ctx: RunContext[Dependencies]) -> str:
    return prompts.render_user_prompt(
        "k8s_investigator",
        alert_title=ctx.deps.alert_title,
        alert_description=ctx.deps.alert_description,
        alert_severity=ctx.deps.alert_severity,
        service=ctx.deps.service,
        cluster_name=ctx.deps.cluster_name,
        namespace=ctx.deps.namespace,
    )
