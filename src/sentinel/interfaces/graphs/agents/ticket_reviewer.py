from __future__ import annotations

import dataclasses

from pydantic import BaseModel
from pydantic_ai import Agent

from sentinel import _config
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


SYSTEM_PROMPT = """\
You are an expert support ticket reviewer. Given a Jira Service Desk ticket,
analyse it and provide classification and guidance for the support team.

Determine:
1. **Category**: The type of issue (billing, technical, account, onboarding, integration, data, general)
2. **Urgency**: critical, high, medium, or low
3. **Required expertise**: What domain knowledge is needed to resolve this
4. **Key questions**: The core questions the customer is asking
5. **Search queries**: 2-4 search queries to find relevant documentation

Be precise in your search queries - they will be used to search Notion, Confluence, and S3 documentation.
"""

agent: Agent[Dependencies, TicketClassification] = Agent(
    utils.get_model_with_gateway(_config.TICKET_REVIEWER_LLM),
    deps_type=Dependencies,
    output_type=TicketClassification,
    system_prompt=SYSTEM_PROMPT,
    instrument=True,
)
