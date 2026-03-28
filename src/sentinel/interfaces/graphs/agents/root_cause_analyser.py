from __future__ import annotations

import dataclasses
from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from sentinel.plugins import prompts


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


SYSTEM_PROMPT = prompts.load_system_prompt("root_cause_analyser")


agent: Agent[Dependencies, RootCauseAnalysis] = Agent(
    "test",  # Default placeholder; overridden at call site with the configured LiteLLM model.
    deps_type=Dependencies,
    output_type=RootCauseAnalysis,
    system_prompt=SYSTEM_PROMPT,
    instrument=True,
)


@agent.instructions
def build_investigation_context(ctx: RunContext[Dependencies]) -> str:
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
