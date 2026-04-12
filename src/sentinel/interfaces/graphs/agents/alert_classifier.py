from __future__ import annotations

import dataclasses

from pydantic import BaseModel
from pydantic_ai import Agent

from sentinel.domain import prompts
from sentinel.interfaces.graphs.agents import utils


class AlertClassification(BaseModel):
    severity: str
    affected_service: str
    category: str
    summary: str
    requires_immediate_action: bool


@dataclasses.dataclass
class Dependencies:
    alert_title: str
    alert_description: str
    alert_source: str


_PROMPT_TEMPLATE = prompts.load_template("alert_classifier")


def build_agent(
    *, model: str | None = None, skills: tuple[str, ...] = ()
) -> Agent[Dependencies, AlertClassification]:
    """
    Build the alert classifier agent with configured skills baked in.

    :param model: Normalised pydantic-ai model string (e.g. ``openai:gpt-4.1-mini``).
        When ``None`` the agent uses the placeholder ``"test"`` model, which
        is the right default for unit tests that monkey-patch ``.run``.
    :param skills: Tuple of skill names to append to the system prompt,
        in declaration order. Unknown names raise ``SkillNotFoundError``.
    """
    system_prompt = utils.compose_system_prompt(
        base_prompt=_PROMPT_TEMPLATE.system_text,
        skill_names=skills,
    )
    return Agent(
        model or "test",
        deps_type=Dependencies,
        output_type=AlertClassification,
        system_prompt=system_prompt,
        instrument=True,
    )
