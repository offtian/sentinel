"""
PydanticAI agent that picks one runbook from a tied / rescue candidate set.

Used by :mod:`sentinel.domain.runbooks.matcher` Stage 2A (tie disambiguation)
and Stage 2B (zero-match rescue). The agent only ever sees the alert summary
and ``(runbook_id, description)`` tuples — never the runbook body — so an
indirect prompt-injection embedded in a runbook body cannot reach this
disambiguator.

Output is Pydantic-validated (:class:`models.DisambiguatorChoice`) with
``confidence`` constrained to ``[0, 1]`` and ``justification`` capped at
200 chars.

Transport-level failures are caught at the :func:`disambiguate` boundary and
re-raised as :class:`models.DisambiguatorUnavailableError` so the matcher's
fallback paths (alphabetical / no-match) can fire without leaking PydanticAI
exception types into the domain layer.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from pydantic_ai import Agent, RunContext

from sentinel.domain import prompts
from sentinel.domain.runbooks import models
from sentinel.utils import logs


@dataclasses.dataclass
class RunbookDisambiguatorDeps:
    """Runtime dependencies passed to the disambiguator agent."""

    alert_summary: str
    candidates: tuple[tuple[str, str], ...]


_PROMPT_TEMPLATE = prompts.load_template("runbook_disambiguator")
PROMPT_SHA256 = _PROMPT_TEMPLATE.sha256


def _build_user_prompt(ctx: RunContext[RunbookDisambiguatorDeps]) -> str:
    """Render the user prompt with the alert summary and candidate list."""
    return _PROMPT_TEMPLATE.render_user(
        alert_summary=ctx.deps.alert_summary,
        candidates=ctx.deps.candidates,
    )


def build_agent(
    *, model: Any | None = None
) -> Agent[RunbookDisambiguatorDeps, models.DisambiguatorChoice]:
    """
    Build the runbook disambiguator agent.

    :param model: PydanticAI ``Model`` instance, model identifier string, or
        ``None``. ``None`` falls back to the ``"test"`` placeholder for unit
        tests that monkey-patch ``.run``.
    """
    agent_instance: Agent[RunbookDisambiguatorDeps, models.DisambiguatorChoice] = Agent(
        model if model is not None else "test",
        deps_type=RunbookDisambiguatorDeps,
        output_type=models.DisambiguatorChoice,
        system_prompt=_PROMPT_TEMPLATE.system_text,
        instrument=True,
    )
    agent_instance.instructions(_build_user_prompt)
    return agent_instance


async def disambiguate(
    *,
    alert_summary: str,
    candidates: tuple[tuple[str, str], ...],
    model: str | None,
) -> models.DisambiguatorChoice:
    """
    Run the disambiguator and return the validated choice.

    Wraps the agent ``run`` so the matcher does not have to construct the
    PydanticAI dependencies directly. Any unexpected exception from the
    underlying transport (LiteLLM, HTTP, model output parsing) is logged and
    re-raised as :class:`models.DisambiguatorUnavailableError` so the matcher
    falls back to the deterministic path.

    :raises models.DisambiguatorUnavailableError: when the underlying agent
        call raises any exception.
    """
    agent = build_agent(model=model)
    deps = RunbookDisambiguatorDeps(alert_summary=alert_summary, candidates=candidates)
    try:
        result = await agent.run(deps=deps)
    except Exception as exc:
        logs.log_exception(
            exc,
            params={
                "failure_event": "runbook_disambiguator_call_failed",
                "candidate_count": len(candidates),
            },
        )
        raise models.DisambiguatorUnavailableError(str(exc)) from exc
    return result.output
