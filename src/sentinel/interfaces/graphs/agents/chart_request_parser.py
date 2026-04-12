"""
PydanticAI agent that parses natural-language chart requests into structured ChartSpec.

The agent extracts service name, image, ports, resources, replicas, and other
fields from a free-text deployment request.
"""

from __future__ import annotations

import dataclasses

from pydantic_ai import Agent, RunContext

from sentinel.domain import prompts
from sentinel.domain.charts import entities
from sentinel.interfaces.graphs.agents import utils


@dataclasses.dataclass
class Dependencies:
    raw_message: str
    requester: str
    team: str


_PROMPT_TEMPLATE = prompts.load_template("chart_request_parser")


def _build_context(ctx: RunContext[Dependencies]) -> str:
    """Render the user prompt with request context."""
    return _PROMPT_TEMPLATE.render_user(
        raw_message=ctx.deps.raw_message,
        requester=ctx.deps.requester,
        team=ctx.deps.team,
    )


def build_agent(
    *, model: str | None = None, skills: tuple[str, ...] = ()
) -> Agent[Dependencies, entities.ChartSpec]:
    """
    Build the chart request parser agent with configured skills baked in.
    """
    system_prompt = utils.compose_system_prompt(
        base_prompt=_PROMPT_TEMPLATE.system_text, skill_names=skills
    )
    agent_instance: Agent[Dependencies, entities.ChartSpec] = Agent(
        model or "test",
        deps_type=Dependencies,
        output_type=entities.ChartSpec,
        system_prompt=system_prompt,
        instrument=True,
        output_retries=3,
    )
    agent_instance.instructions(_build_context)
    return agent_instance
