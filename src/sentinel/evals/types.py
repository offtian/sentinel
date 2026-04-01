"""
Shared types for the evaluation framework.

Define the run type enum and input data model used across
the eval runner, cases, and evaluators.
"""

from __future__ import annotations

import dataclasses
import json
from enum import Enum
from typing import Any


class RunType(str, Enum):
    SCHEDULED = "scheduled"
    ADHOC = "adhoc"


@dataclasses.dataclass(frozen=True)
class InputData:
    """
    Input data for an evaluation case.

    Carry the agent name and the raw case payload so evaluators
    and the runner can access both the routing key and the case fields.
    """

    agent_name: str
    case_payload: dict[str, Any]

    def __str__(self) -> str:
        return json.dumps({"agent_name": self.agent_name, "case_payload": self.case_payload})
