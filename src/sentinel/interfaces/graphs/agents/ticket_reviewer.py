from __future__ import annotations

import dataclasses

from pydantic import BaseModel
from pydantic_ai import Agent

from sentinel.interfaces.graphs.agents import utils
from sentinel.plugins import prompts


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


BASE_SYSTEM_PROMPT = prompts.load_system_prompt("ticket_reviewer")


def build_agent(
    *, model: str | None = None, skills: tuple[str, ...] = ()
) -> Agent[Dependencies, TicketClassification]:
    """
    Build the ticket reviewer agent with configured skills baked in.
    """
    system_prompt = utils.compose_system_prompt(
        base_prompt=BASE_SYSTEM_PROMPT, skill_names=skills
    )
    return Agent(
        model or "test",
        deps_type=Dependencies,
        output_type=TicketClassification,
        system_prompt=system_prompt,
        instrument=True,
    )


SYSTEM_PROMPT = BASE_SYSTEM_PROMPT
agent = build_agent()
