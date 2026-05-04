"""
Typed OTel span-attribute wrappers for Sentinel pipelines.

Re-exports the boundary models so callers can import directly from
``sentinel.utils.observability``.
"""

from sentinel.utils.observability.spans import (
    AgentSpanAttributes,
    NodeSpanAttributes,
    ToolSpanAttributes,
)


__all__ = [
    "AgentSpanAttributes",
    "NodeSpanAttributes",
    "ToolSpanAttributes",
]
