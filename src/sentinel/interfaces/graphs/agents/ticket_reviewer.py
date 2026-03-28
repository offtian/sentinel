from __future__ import annotations

import dataclasses

from pydantic import BaseModel
from pydantic_ai import Agent

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


SYSTEM_PROMPT = prompts.load_system_prompt("ticket_reviewer")


agent: Agent[Dependencies, TicketClassification] = Agent(
    "test",  # Default placeholder; overridden at call site with the configured LiteLLM model.
    deps_type=Dependencies,
    output_type=TicketClassification,
    system_prompt=SYSTEM_PROMPT,
    instrument=True,
)
