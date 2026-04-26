"""
Toolset runtime context for replay-bundle capture (F4.6).

A pipeline run that needs reproducibility binds a
:class:`~sentinel.utils.replay_bundle.ReplayBundleBuilder` into a
``ContextVar`` at start; every toolset wrapper records its inputs and
outputs into that builder via :func:`record_tool_call`; the harness
calls :func:`flush_replay_capture` on pipeline ``End`` to freeze the
accumulated I/O into an immutable :class:`ReplayBundle` and reset the
context.

The binding is per-asyncio-task (stdlib ``ContextVar`` semantics): two
investigations running concurrently under ``asyncio.gather`` see only
their own builder, never each other's. Production paths that don't bind
a builder pay zero cost — :func:`record_tool_call` is a fast no-op
when the context var is unset, so toolset wiring is invisible to runs
that aren't being captured for replay.

The module name is leading-underscore (``_runtime``) because it is
internal to the ``plugins.toolsets`` package — only toolset factories in
the same package and the pipeline harness should depend on it.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import AbstractToolset, WrapperToolset
from pydantic_ai.toolsets.abstract import ToolsetTool

from sentinel.data.primitives import envelope as envelope_mod
from sentinel.utils import logs
from sentinel.utils import replay_bundle as bundle_mod


_REPLAY_BUILDER_VAR: ContextVar[bundle_mod.ReplayBundleBuilder | None] = ContextVar(
    "sentinel_replay_builder",
    default=None,
)


class ReplayCapturingToolset(WrapperToolset[Any]):
    """
    Toolset wrapper that records every tool invocation into the active replay builder.

    Wrap any PydanticAI :class:`AbstractToolset` (a :class:`FunctionToolset`,
    an MCP server, etc.) with this class to get uniform RFC §3.8 tool-I/O
    capture without touching the underlying toolset's tool implementations.
    The wrapper delegates to the wrapped toolset for the actual call, then
    appends a :class:`~sentinel.utils.replay_bundle.ToolIOEntry` to the
    active :class:`~sentinel.utils.replay_bundle.ReplayBundleBuilder` (if
    one is bound to the current asyncio context) — runs that aren't being
    captured pay only one ``ContextVar.get()`` per tool call.

    Failures inside the wrapped tool are re-raised after a best-effort
    capture: the entry's ``outputs`` records the exception's string form so
    replay diffs surface tool-error drift, and the original exception
    propagates so the pipeline's error path is unchanged.
    """

    def __init__(self, wrapped: AbstractToolset[Any], *, label: str | None = None) -> None:
        super().__init__(wrapped)
        self._label = label or wrapped.__class__.__name__

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[Any],
        tool: ToolsetTool[Any],
    ) -> Any:
        """Delegate to the wrapped toolset and capture the call into the active builder."""
        called_at = datetime.now(tz=UTC)
        try:
            result = await self.wrapped.call_tool(name, tool_args, ctx, tool)
        except Exception as exc:
            # Best-effort capture before re-raising so replay diffs see error drift.
            record_tool_call(
                tool_name=name,
                inputs=dict(tool_args),
                outputs=f"<error: {type(exc).__name__}: {exc}>",
                at=called_at,
            )
            raise
        record_tool_call(
            tool_name=name,
            inputs=dict(tool_args),
            outputs=result,
            at=called_at,
        )
        return result


def wrap_for_replay(
    toolset: AbstractToolset[Any] | None,
    *,
    label: str | None = None,
) -> AbstractToolset[Any] | None:
    """
    Wrap *toolset* with :class:`ReplayCapturingToolset` for I/O capture.

    Returns ``None`` unchanged so callers can pipe optional toolsets through
    without conditional branching. Already-wrapped toolsets are returned
    as-is to keep wrapping idempotent.

    :param toolset: The toolset to wrap, or ``None``.
    :param label: Optional human-readable label for log lines.
    """
    if toolset is None:
        return None
    if isinstance(toolset, ReplayCapturingToolset):
        return toolset
    logs.log_event(
        "toolset_replay_capture_attached",
        params={"label": label or toolset.__class__.__name__},
    )
    return ReplayCapturingToolset(toolset, label=label)


def bind_replay_builder(
    builder: bundle_mod.ReplayBundleBuilder,
) -> Token[bundle_mod.ReplayBundleBuilder | None]:
    """
    Bind *builder* to the current asyncio context.

    Returns a ``Token`` that the caller MUST hand back to
    :func:`unbind_replay_builder` (or :func:`flush_replay_capture`) to
    restore the previous binding. Forgetting to unbind leaks the builder
    to subsequent tasks scheduled in the same context.
    """
    return _REPLAY_BUILDER_VAR.set(builder)


def unbind_replay_builder(token: Token[bundle_mod.ReplayBundleBuilder | None]) -> None:
    """Restore the previous builder binding (mirror of :func:`bind_replay_builder`)."""
    _REPLAY_BUILDER_VAR.reset(token)


def current_replay_builder() -> bundle_mod.ReplayBundleBuilder | None:
    """Return the builder bound to the current context, or ``None`` if unbound."""
    return _REPLAY_BUILDER_VAR.get()


def record_tool_call(
    *,
    tool_name: str,
    inputs: dict[str, Any],
    outputs: Any,
    evidence_object_id: str | None = None,
    at: datetime | None = None,
) -> None:
    """
    Append a tool invocation to the active replay builder.

    No-op when no builder is bound to the current context (the production
    happy path for runs that aren't being captured). When a builder *is*
    bound, the call is recorded as a
    :class:`~sentinel.utils.replay_bundle.ToolIOEntry` in invocation
    order — F4.7's ``RecordedTransport`` replays them in the same order.

    :param tool_name: Toolset-registered tool name (e.g. ``query_recent_logs``).
    :param inputs: Kwargs the tool was called with — must be JSON-serialisable.
    :param outputs: Tool return value — must be JSON-serialisable.
    :param evidence_object_id: Optional S3/blob pointer for large outputs.
    :param at: Wall-clock timestamp of the call; defaults to ``datetime.now(UTC)``.
    """
    builder = _REPLAY_BUILDER_VAR.get()
    if builder is None:
        return
    builder.record_tool_io(
        bundle_mod.ToolIOEntry(
            tool_name=tool_name,
            inputs=inputs,
            outputs=outputs,
            evidence_object_id=evidence_object_id,
            at=at if at is not None else datetime.now(tz=UTC),
        )
    )


def record_llm_call(
    *,
    agent_name: str,
    model_id: str,
    inputs: dict[str, Any],
    outputs: Any,
    token_usage: dict[str, Any] | None = None,
    at: datetime | None = None,
) -> None:
    """
    Append an LLM agent invocation to the active replay builder.

    Symmetric counterpart to :func:`record_tool_call` for LLM calls.
    No-op when no builder is bound. Used by the pipeline tracer to
    capture agent inputs/outputs alongside tool I/O for full RFC §3.8
    bundle coverage.
    """
    builder = _REPLAY_BUILDER_VAR.get()
    if builder is None:
        return
    builder.record_llm_io(
        bundle_mod.LLMIOEntry(
            agent_name=agent_name,
            model_id=model_id,
            inputs=inputs,
            outputs=outputs,
            token_usage=token_usage,
            at=at if at is not None else datetime.now(tz=UTC),
        )
    )


def flush_replay_capture(
    *,
    token: Token[bundle_mod.ReplayBundleBuilder | None] | None,
    envelope: envelope_mod.Envelope,
    alert_payload: dict[str, Any],
    runbook_id: str | None,
    runbook_version_sha: str | None,
    final_outputs: dict[str, Any],
) -> bundle_mod.ReplayBundle | None:
    """
    Freeze the active builder into an immutable bundle and unbind it.

    Returns ``None`` when *token* is ``None`` (no capture was started for
    this run) — that's the production path for pipelines that haven't
    opted into replay capture.

    Otherwise reads the active builder, materialises a
    :class:`~sentinel.utils.replay_bundle.ReplayBundle` from the supplied
    pipeline-level facts plus the accumulated tool / LLM I/O, and resets
    the ``ContextVar`` to its previous binding.

    :param token: The token returned by :func:`bind_replay_builder`, or ``None``.
    :param envelope: The identity envelope minted at ingress.
    :param alert_payload: The raw alert/ticket dict the pipeline ran against.
    :param runbook_id: Matched runbook identifier, or ``None``.
    :param runbook_version_sha: Pinned runbook content SHA, or ``None``.
    :param final_outputs: The pipeline's final reply payload.
    """
    if token is None:
        return None
    builder = _REPLAY_BUILDER_VAR.get()
    try:
        if builder is None:
            return None
        return builder.build(
            envelope=envelope,
            alert_payload=alert_payload,
            runbook_id=runbook_id,
            runbook_version_sha=runbook_version_sha,
            final_outputs=final_outputs,
        )
    finally:
        unbind_replay_builder(token)
