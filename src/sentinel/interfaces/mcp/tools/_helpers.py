"""
Shared instrumentation for MCP tool handlers.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from sentinel.utils import logs


async def instrumented_mcp_tool(
    *,
    tool_name: str,
    params: Mapping[str, Any],
    fn: Callable[[], Awaitable[str]],
) -> str:
    """
    Wrap an MCP tool call with structured logging and duration tracking.

    Logs ``mcp_tool_invoked`` on entry, ``mcp_tool_completed`` on success
    (with ``duration_ms``), and ``logs.log_exception`` on error.

    :param tool_name: Identifier for the tool being called.
    :param params: Parameters to include in the invocation log.
    :param fn: Async callable that performs the actual work.
    :returns: The string result from ``fn``.
    """
    logs.log_event("mcp_tool_invoked", params={"tool": tool_name, **params})
    start = time.monotonic()
    try:
        result = await fn()
    except Exception as exc:
        logs.log_exception(exc, params={"tool": tool_name, **params})
        raise
    duration_ms = (time.monotonic() - start) * 1000
    logs.log_event(
        "mcp_tool_completed",
        params={"tool": tool_name, "duration_ms": duration_ms},
    )
    return result
