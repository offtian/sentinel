from __future__ import annotations

import dataclasses

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from sentinel.domain.search import searcher
from sentinel.interfaces.graphs.agents import utils
from sentinel.plugins import prompts


class SourceReference(BaseModel):
    title: str
    url: str


class DraftedResponse(BaseModel):
    response: str
    sources_used: list[SourceReference]
    confidence: float
    notes_for_agent: str


@dataclasses.dataclass
class Dependencies:
    ticket_summary: str
    ticket_description: str
    ticket_category: str
    key_questions: list[str]
    document_search_results: list[searcher.DocumentSearchResult]
    ticket_search_results: list[searcher.TicketSearchResult]


SYSTEM_PROMPT = prompts.load_system_prompt("response_drafter")


agent: Agent[Dependencies, DraftedResponse] = Agent(
    "test",  # Default placeholder; overridden at call site with the configured LiteLLM model.
    deps_type=Dependencies,
    output_type=DraftedResponse,
    system_prompt=SYSTEM_PROMPT,
    instrument=True,
)


@agent.system_prompt
def inject_response_pattern_skills(ctx: RunContext[Dependencies]) -> str:
    """
    Append response-pattern Skills matching the ticket category.

    Returns an empty string when the category is unset or no skill matches.
    """
    if not ctx.deps.ticket_category:
        return ""
    return utils.render_skills_section(category=ctx.deps.ticket_category, max_skills=3)


@agent.instructions
def build_context(ctx: RunContext[Dependencies]) -> str:
    doc_results = [
        {"title": r.title, "url": r.url, "excerpt": r.excerpt}
        for r in ctx.deps.document_search_results
    ]
    ticket_results = [
        {
            "key": r.key,
            "url": r.url,
            "summary": r.summary,
            "resolution": r.resolution,
        }
        for r in ctx.deps.ticket_search_results
    ]
    return prompts.render_user_prompt(
        "response_drafter",
        ticket_summary=ctx.deps.ticket_summary,
        ticket_category=ctx.deps.ticket_category,
        ticket_description=ctx.deps.ticket_description,
        key_questions=ctx.deps.key_questions,
        document_results=doc_results,
        ticket_results=ticket_results,
    )
