from __future__ import annotations

import dataclasses
from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from sentinel.domain import prompts
from sentinel.interfaces.graphs.agents import utils


class RootCauseAnalysis(BaseModel):
    root_cause: str
    confidence: float
    evidence: list[str]
    remediation_steps: list[str]
    affected_services: list[str]
    timeline: str


@dataclasses.dataclass
class Dependencies:
    alert_title: str
    alert_description: str
    alert_severity: str
    holmes_analysis: str
    holmes_tool_calls: list[dict[str, Any]]
    holmes_sources: list[str]
    # Classifier-produced category, used for the optional second-layer
    # dynamic runbook skill injection (see inject_runbook_skills below).
    category: str = ""


_SYSTEM_PROMPT_HANDLE = prompts.load_system_prompt("root_cause_analyser")
BASE_SYSTEM_PROMPT = _SYSTEM_PROMPT_HANDLE.text


def _build_investigation_context(ctx: RunContext[Dependencies]) -> str:
    return prompts.render_user_prompt(
        "root_cause_analyser",
        alert_title=ctx.deps.alert_title,
        alert_description=ctx.deps.alert_description,
        alert_severity=ctx.deps.alert_severity,
        holmes_analysis=ctx.deps.holmes_analysis,
        sources_queried=(
            ", ".join(ctx.deps.holmes_sources) if ctx.deps.holmes_sources else "none"
        ),
        tool_calls=ctx.deps.holmes_tool_calls,
    )


def _inject_runbook_skills(ctx: RunContext[Dependencies]) -> str:
    """
    Second-layer dynamic Skills injection keyed off classifier category.

    Configured skills are already baked into the static system prompt by
    ``build_agent``; this function adds any additional runbook that
    matches the runtime category via ``applies_to`` globs. Returns an
    empty string when the category is unset or no skill matches.
    """
    if not ctx.deps.category:
        return ""
    return utils.render_skills_section(category=ctx.deps.category, max_skills=3)


def build_agent(
    *, model: str | None = None, skills: tuple[str, ...] = ()
) -> Agent[Dependencies, RootCauseAnalysis]:
    """
    Build the root cause analyser agent with configured skills baked in.

    Runtime category-driven dynamic injection still fires on top of the
    configured static skills via the ``@agent.system_prompt`` hook, so the
    operator can declare a base runbook set in ``config.load_agents()``
    and let the classifier output add category-specific skills at runtime.
    """
    system_prompt = utils.compose_system_prompt(base_prompt=BASE_SYSTEM_PROMPT, skill_names=skills)
    agent_instance: Agent[Dependencies, RootCauseAnalysis] = Agent(
        model or "test",
        deps_type=Dependencies,
        output_type=RootCauseAnalysis,
        system_prompt=system_prompt,
        instrument=True,
    )
    agent_instance.instructions(_build_investigation_context)
    agent_instance.system_prompt(_inject_runbook_skills)
    return agent_instance
