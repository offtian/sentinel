"""
Replay primitive: ``AbstractToolset`` that pops recorded tool I/O in order.

Used by the F4.7 replay CLI to substitute a single ordered queue of
:class:`~sentinel.utils.replay_bundle.ToolIOEntry` for every live toolset
in a pipeline. The original capture path
(:class:`~sentinel.plugins.toolsets._runtime.ReplayCapturingToolset`)
records all tools into one global timeline regardless of which toolset
they came from, so a single ``RecordedToolset`` shared across every
toolset slot reproduces the same global order.

Replay is strict on order, name, and inputs: any drift raises
:class:`~sentinel.domain.pipeline.errors.RecordedReplayMismatchError`.
That loud-failure stance is the entire point of the replay-bundle
contract — silent reordering tolerance defeats the determinism guarantee
F4.8 will enforce in CI.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.toolsets.abstract import ToolsetTool

from sentinel.domain.pipeline import errors as pipeline_errors
from sentinel.utils import replay_bundle as bundle_mod


class RecordedToolset(AbstractToolset[Any]):
    """
    AbstractToolset that returns recorded tool outputs in invocation order.

    On every :meth:`call_tool` the toolset pops the next
    :class:`~sentinel.utils.replay_bundle.ToolIOEntry` from its queue and
    asserts the live call matches the recorded entry's name and inputs.
    Mismatches raise
    :class:`~sentinel.domain.pipeline.errors.RecordedReplayMismatchError`
    with a discriminating ``kind``. Exhaustion (live call after the queue
    is empty) raises ``kind="tool", reason="exhausted"``.

    :meth:`get_tools` returns an empty mapping — replay never consults
    the toolset for tool discovery; the recorded LLM output already
    encodes which tools the agent decided to call.
    """

    def __init__(self, entries: Sequence[bundle_mod.ToolIOEntry]) -> None:
        self._iter: Iterator[bundle_mod.ToolIOEntry] = iter(entries)

    @property
    def id(self) -> str | None:
        """Return a stable identifier for this toolset (no per-instance ID needed)."""
        return "recorded-toolset"

    async def get_tools(
        self,
        ctx: RunContext[Any],
    ) -> dict[str, ToolsetTool[Any]]:
        """Return an empty tool mapping — replay does not advertise tools."""
        del ctx  # unused: replay never lists tools
        return {}

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[Any],
        tool: ToolsetTool[Any],
    ) -> Any:
        """Pop the next recorded entry and return its outputs after strict match."""
        del ctx, tool  # unused: replay does not consult ctx or live tool defs
        try:
            entry = next(self._iter)
        except StopIteration as exc:
            raise pipeline_errors.RecordedReplayMismatchError(
                kind="tool",
                expected=name,
                actual=None,
                reason="exhausted",
            ) from exc
        if entry.tool_name != name:
            raise pipeline_errors.RecordedReplayMismatchError(
                kind="tool_name",
                expected=entry.tool_name,
                actual=name,
            )
        actual_args = dict(tool_args)
        if entry.inputs != actual_args:
            raise pipeline_errors.RecordedReplayMismatchError(
                kind="tool_args",
                expected=entry.inputs,
                actual=actual_args,
            )
        return entry.outputs
