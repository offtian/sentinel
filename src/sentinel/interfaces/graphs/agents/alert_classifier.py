from __future__ import annotations

import dataclasses
from typing import Any

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
PROMPT_SHA256 = _PROMPT_TEMPLATE.sha256


def build_agent(
    *, model: Any | None = None, skills: tuple[str, ...] = ()
) -> Agent[Dependencies, AlertClassification]:
    """
    Build the alert classifier agent with configured skills baked in.

    :param model: PydanticAI ``Model`` instance, model identifier string,
        or ``None``. ``CommonConfiguration._build_agent_model`` resolves
        the proxy-routing OpenAIChatModel; passing ``None`` falls back to
        the ``"test"`` placeholder for unit tests that monkey-patch ``.run``.
    :param skills: Tuple of skill names to append to the system prompt,
        in declaration order. Unknown names raise ``SkillNotFoundError``.
    """
    system_prompt = utils.compose_system_prompt(
        base_prompt=_PROMPT_TEMPLATE.system_text,
        skill_names=skills,
    )
    return Agent(
        model if model is not None else "test",
        deps_type=Dependencies,
        output_type=AlertClassification,
        system_prompt=system_prompt,
        instrument=True,
    )
