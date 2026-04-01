"""
Domain tool functions for Sentinel agents.

This package contains pure async functions that implement tool logic.
They have no dependency on PydanticAI — they take typed parameters and
return formatted strings suitable for LLM consumption.

The PydanticAI ``FunctionToolset`` wrappers that adapt these into
agent-compatible tools live in ``sentinel.plugins.toolsets``.
"""
