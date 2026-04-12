"""
Shared utilities for evaluators.

Provides the ``resolve_field`` helper used by all evaluators to
traverse nested dicts via dot-separated field paths.
"""

from __future__ import annotations

from typing import Any


def resolve_field(*, payload: dict[str, Any], field_path: str) -> Any:
    """
    Traverse a nested dict using a dot-separated field path.

    :raises KeyError: if any segment is missing.
    """
    current: Any = payload
    for segment in field_path.split("."):
        current = current[segment]
    return current
