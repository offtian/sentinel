"""
PydanticAI agent that generates Helm chart YAML files from a validated ChartSpec.

The agent produces Deployment, Service, HPA, NetworkPolicy, and ArgoCD
Application resources based on the spec and team policy.
"""

from __future__ import annotations

import dataclasses

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from sentinel.domain.charts import entities
from sentinel.interfaces.graphs.agents import utils
from sentinel.plugins import prompts


class ChartGeneratorOutput(BaseModel):
    """Output from the chart generator agent."""

    files: tuple[entities.GeneratedFile, ...]


@dataclasses.dataclass
class Dependencies:
    service_name: str
    image: str
    spec_json: str
    policy_json: str


BASE_SYSTEM_PROMPT = prompts.load_system_prompt("chart_generator")


def _build_context(ctx: RunContext[Dependencies]) -> str:
    """Render the user prompt with spec and policy context."""
    return prompts.render_user_prompt(
        "chart_generator",
        service_name=ctx.deps.service_name,
        image=ctx.deps.image,
        spec_json=ctx.deps.spec_json,
        policy_json=ctx.deps.policy_json,
    )


def build_agent(
    *, model: str | None = None, skills: tuple[str, ...] = ()
) -> Agent[Dependencies, ChartGeneratorOutput]:
    """
    Build the chart generator agent with configured skills baked in.
    """
    system_prompt = utils.compose_system_prompt(base_prompt=BASE_SYSTEM_PROMPT, skill_names=skills)
    agent_instance: Agent[Dependencies, ChartGeneratorOutput] = Agent(
        model or "test",
        deps_type=Dependencies,
        output_type=ChartGeneratorOutput,
        system_prompt=system_prompt,
        instrument=True,
        output_retries=3,
    )
    agent_instance.instructions(_build_context)
    return agent_instance


SYSTEM_PROMPT = BASE_SYSTEM_PROMPT
agent = build_agent()
