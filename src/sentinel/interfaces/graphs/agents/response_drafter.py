from __future__ import annotations

import dataclasses

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from sentinel import _config
from sentinel.domain.search import searcher
from sentinel.interfaces.graphs.agents import utils


class DraftedResponse(BaseModel):
    response: str
    sources_used: list[SourceReference]
    confidence: float
    notes_for_agent: str


class SourceReference(BaseModel):
    title: str
    url: str


@dataclasses.dataclass
class Dependencies:
    ticket_summary: str
    ticket_description: str
    ticket_category: str
    key_questions: list[str]
    document_search_results: list[searcher.DocumentSearchResult]
    ticket_search_results: list[searcher.TicketSearchResult]


SYSTEM_PROMPT = """\
You are an expert customer support response drafter. Given a support ticket and
relevant documentation, draft a professional and helpful response.

Guidelines:
1. Address all key questions from the ticket
2. Reference specific documentation with source links
3. Be clear, concise, and professional
4. If the documentation doesn't fully answer the question, note what's missing
5. Provide step-by-step instructions where applicable
6. Maintain a friendly, helpful tone

Your response will be reviewed by a human agent before being sent to the customer.
Include a confidence score (0.0-1.0) and any notes for the reviewing agent.
"""

agent: Agent[Dependencies, DraftedResponse] = Agent(
    utils.get_model_with_gateway(_config.RESPONSE_DRAFTER_LLM),
    deps_type=Dependencies,
    output_type=DraftedResponse,
    system_prompt=SYSTEM_PROMPT,
    instrument=True,
)


@agent.instructions
def build_context(ctx: RunContext[Dependencies]) -> str:
    doc_results = "\n".join(
        f"- [{result.title}]({result.url}): {result.excerpt}"
        for result in ctx.deps.document_search_results
    )
    ticket_results = "\n".join(
        f"- [{result.key}]({result.url}): {result.summary} → {result.resolution or 'No resolution'}"
        for result in ctx.deps.ticket_search_results
    )
    questions = "\n".join(f"- {q}" for q in ctx.deps.key_questions)

    return f"""
## Ticket
- **Summary**: {ctx.deps.ticket_summary}
- **Category**: {ctx.deps.ticket_category}
- **Description**: {ctx.deps.ticket_description}

## Key Questions
{questions}

## Relevant Documentation
{doc_results or "No documentation found."}

## Similar Past Tickets
{ticket_results or "No similar tickets found."}
"""
