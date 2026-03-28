from __future__ import annotations

import dataclasses

from pydantic import BaseModel
from pydantic_ai import Agent

from sentinel.plugins import prompts


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


SYSTEM_PROMPT = prompts.load_system_prompt("alert_classifier")

agent: Agent[Dependencies, AlertClassification] = Agent(
    "test",  # Placeholder; overridden at call site with the configured LiteLLM model.
    deps_type=Dependencies,
    output_type=AlertClassification,
    system_prompt=SYSTEM_PROMPT,
    instrument=True,
)
