from __future__ import annotations

import dataclasses

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from sentinel.domain import prompts
from sentinel.domain.search import searcher
from sentinel.interfaces.graphs.agents import utils


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


_PROMPT_TEMPLATE = prompts.load_template("response_drafter")
PROMPT_SHA256 = _PROMPT_TEMPLATE.sha256


def _build_context(ctx: RunContext[Dependencies]) -> str:
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
    return _PROMPT_TEMPLATE.render_user(
        ticket_summary=ctx.deps.ticket_summary,
        ticket_category=ctx.deps.ticket_category,
        ticket_description=ctx.deps.ticket_description,
        key_questions=ctx.deps.key_questions,
        document_results=doc_results,
        ticket_results=ticket_results,
    )


def _inject_response_pattern_skills(ctx: RunContext[Dependencies]) -> str:
    """
    Second-layer dynamic Skills injection keyed off ticket category.

    Returns an empty string when the category is unset or no skill matches.
    """
    if not ctx.deps.ticket_category:
        return ""
    return utils.render_skills_section(category=ctx.deps.ticket_category, max_skills=3)


def build_agent(
    *, model: str | None = None, skills: tuple[str, ...] = ()
) -> Agent[Dependencies, DraftedResponse]:
    """
    Build the response drafter agent with configured skills baked in.
    """
    system_prompt = utils.compose_system_prompt(
        base_prompt=_PROMPT_TEMPLATE.system_text, skill_names=skills
    )
    agent_instance: Agent[Dependencies, DraftedResponse] = Agent(
        utils.resolve_agent_model(model or "test"),
        deps_type=Dependencies,
        output_type=DraftedResponse,
        system_prompt=system_prompt,
        instrument=True,
    )
    agent_instance.instructions(_build_context)
    agent_instance.system_prompt(_inject_response_pattern_skills)
    return agent_instance
