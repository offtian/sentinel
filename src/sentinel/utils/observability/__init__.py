"""
Typed OTel span-attribute wrappers for Sentinel pipelines.

Re-exports the four boundary models and the usage-extraction helper so
callers can import directly from ``sentinel.utils.observability``.
"""

from sentinel.utils.observability.spans import (
    AgentSpanAttributes,
    NodeSpanAttributes,
    ToolSpanAttributes,
    UsageAttributes,
)
from sentinel.utils.observability.usage import extract_usage


__all__ = [
    "AgentSpanAttributes",
    "NodeSpanAttributes",
    "ToolSpanAttributes",
    "UsageAttributes",
    "extract_usage",
]
