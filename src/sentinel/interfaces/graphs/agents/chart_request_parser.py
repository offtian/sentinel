"""
PydanticAI agent that parses natural-language chart requests into structured ChartSpec.

The agent extracts service name, image, ports, resources, replicas, and other
fields from a free-text deployment request.
"""

from __future__ import annotations

import dataclasses

from pydantic_ai import Agent, RunContext

from sentinel.domain.charts import entities
from sentinel.plugins import prompts


@dataclasses.dataclass
class Dependencies:
    raw_message: str
    requester: str
    team: str


SYSTEM_PROMPT = prompts.load_system_prompt("chart_request_parser")

agent: Agent[Dependencies, entities.ChartSpec] = Agent(
    "test",
    deps_type=Dependencies,
    output_type=entities.ChartSpec,
    system_prompt=SYSTEM_PROMPT,
    instrument=True,
)


@agent.instructions
def build_context(ctx: RunContext[Dependencies]) -> str:
    """Render the user prompt with request context."""
    return prompts.render_user_prompt(
        "chart_request_parser",
        raw_message=ctx.deps.raw_message,
        requester=ctx.deps.requester,
        team=ctx.deps.team,
    )
