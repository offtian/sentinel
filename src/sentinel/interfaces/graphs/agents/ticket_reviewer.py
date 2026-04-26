from __future__ import annotations

import dataclasses

from pydantic import BaseModel
from pydantic_ai import Agent

from sentinel.domain import prompts
from sentinel.interfaces.graphs.agents import utils


class TicketClassification(BaseModel):
    category: str
    urgency: str
    required_expertise: list[str]
    key_questions: list[str]
    search_queries: list[str]


@dataclasses.dataclass
class Dependencies:
    ticket_summary: str
    ticket_description: str
    ticket_priority: str
    ticket_labels: list[str]


_PROMPT_TEMPLATE = prompts.load_template("ticket_reviewer")
PROMPT_SHA256 = _PROMPT_TEMPLATE.sha256


def build_agent(
    *, model: str | None = None, skills: tuple[str, ...] = ()
) -> Agent[Dependencies, TicketClassification]:
    """
    Build the ticket reviewer agent with configured skills baked in.
    """
    system_prompt = utils.compose_system_prompt(
        base_prompt=_PROMPT_TEMPLATE.system_text, skill_names=skills
    )
    return Agent(
        utils.resolve_agent_model(model or "test"),
        deps_type=Dependencies,
        output_type=TicketClassification,
        system_prompt=system_prompt,
        instrument=True,
    )
