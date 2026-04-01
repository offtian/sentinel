"""
Sentinel evaluation framework using pydantic_evals.

Evaluate individual agent quality against golden datasets using
composable pattern-based evaluators.
"""

from . import cases, evaluators, rendering, reporting, types
from .runner import run as run
from .types import RunType as RunType
