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


SYSTEM_PROMPT = utils.append_skills_to_prompt(
    base_prompt=prompts.load_system_prompt("ticket_reviewer"),
    category="ticket_triage",
    max_skills=3,
)


agent: Agent[Dependencies, TicketClassification] = Agent(
    "test",  # Default placeholder; overridden at call site with the configured LiteLLM model.
    deps_type=Dependencies,
    output_type=TicketClassification,
    system_prompt=SYSTEM_PROMPT,
    instrument=True,
)
