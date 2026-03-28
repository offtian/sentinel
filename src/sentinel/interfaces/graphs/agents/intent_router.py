from __future__ import annotations

import dataclasses
import enum

from pydantic import BaseModel
from pydantic_ai import Agent

from sentinel.plugins import prompts


class Intent(str, enum.Enum):
    SRE = "sre"
    SUPPORT = "support"


class IntentClassification(BaseModel):
    intent: Intent
    rationale: str


@dataclasses.dataclass
class Dependencies:
    message: str


SYSTEM_PROMPT = prompts.load_system_prompt("intent_router")

agent: Agent[Dependencies, IntentClassification] = Agent(
    "test",  # Placeholder; overridden at call site with the configured LiteLLM model.
    deps_type=Dependencies,
    output_type=IntentClassification,
    system_prompt=SYSTEM_PROMPT,
    instrument=True,
)
