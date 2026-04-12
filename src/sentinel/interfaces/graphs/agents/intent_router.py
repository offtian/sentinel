from __future__ import annotations

import dataclasses
import enum

from pydantic import BaseModel
from pydantic_ai import Agent

from sentinel.domain import prompts
from sentinel.interfaces.graphs.agents import utils


class Intent(str, enum.Enum):
    SRE = "sre"
    SUPPORT = "support"


class IntentClassification(BaseModel):
    intent: Intent
    rationale: str


@dataclasses.dataclass
class Dependencies:
    message: str


_SYSTEM_PROMPT_HANDLE = prompts.load_system_prompt("intent_router")
BASE_SYSTEM_PROMPT = _SYSTEM_PROMPT_HANDLE.text


def build_agent(
    *, model: str | None = None, skills: tuple[str, ...] = ()
) -> Agent[Dependencies, IntentClassification]:
    """
    Build the intent router agent with configured skills baked in.
    """
    system_prompt = utils.compose_system_prompt(base_prompt=BASE_SYSTEM_PROMPT, skill_names=skills)
    return Agent(
        model or "test",
        deps_type=Dependencies,
        output_type=IntentClassification,
        system_prompt=system_prompt,
        instrument=True,
    )
