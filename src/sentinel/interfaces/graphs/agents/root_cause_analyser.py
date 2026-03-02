from __future__ import annotations

import dataclasses
from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from sentinel import _config
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


SYSTEM_PROMPT = """\
You are an expert Site Reliability Engineer performing root cause analysis.

You have been given an alert and the results of an automated investigation that
queried logs, metrics, traces, and Kubernetes state. Synthesise all findings into
a clear root cause analysis.

Your analysis must include:
1. **Root cause**: A clear, specific explanation of what went wrong and why
2. **Confidence**: A score from 0.0 to 1.0 indicating how confident you are
3. **Evidence**: Specific data points from the investigation that support your conclusion
4. **Remediation steps**: Ordered list of actions to resolve the issue
5. **Affected services**: All services impacted by this incident
6. **Timeline**: A brief reconstruction of the incident timeline

Be specific and actionable. Avoid vague statements. If you're uncertain, say so
and suggest further investigation steps.
"""

agent: Agent[Dependencies, RootCauseAnalysis] = Agent(
    utils.get_model_with_gateway(_config.ROOT_CAUSE_LLM),
    deps_type=Dependencies,
    output_type=RootCauseAnalysis,
    system_prompt=SYSTEM_PROMPT,
    instrument=True,
)


@agent.instructions
def build_investigation_context(ctx: RunContext[Dependencies]) -> str:
    tool_call_summary = "\n".join(
        f"- {call.get('tool', 'unknown')}: {call.get('result', 'no result')}"
        for call in ctx.deps.holmes_tool_calls
    )
    sources = ", ".join(ctx.deps.holmes_sources) if ctx.deps.holmes_sources else "none"

    return f"""
## Alert Details
- **Title**: {ctx.deps.alert_title}
- **Description**: {ctx.deps.alert_description}
- **Severity**: {ctx.deps.alert_severity}

## Investigation Results
{ctx.deps.holmes_analysis}

## Data Sources Queried
{sources}

## Tool Call Results
{tool_call_summary}
"""
