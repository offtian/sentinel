from __future__ import annotations

import dataclasses

from pydantic import BaseModel
from pydantic_ai import Agent

from sentinel import _config
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


SYSTEM_PROMPT = """\
You are an expert SRE alert classifier. Given an alert from a monitoring system,
classify it accurately.

Determine:
1. **Severity**: critical, high, medium, or low
2. **Affected service**: The primary service or component affected
3. **Category**: One of: infrastructure, application, database, network, security, performance, availability
4. **Summary**: A brief one-sentence summary of the issue
5. **Requires immediate action**: Whether this needs immediate human intervention

Be precise and avoid over-escalating. Use context from the alert source and description.
"""

agent: Agent[Dependencies, AlertClassification] = Agent(
    utils.get_model_with_gateway(_config.ALERT_CLASSIFIER_LLM),
    deps_type=Dependencies,
    output_type=AlertClassification,
    system_prompt=SYSTEM_PROMPT,
    instrument=True,
)
