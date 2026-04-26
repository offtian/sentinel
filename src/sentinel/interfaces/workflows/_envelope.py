"""
LangGraph node decorator that binds the F2 envelope identity context.

The decorator is the LangGraph counterpart to
``run_node_with_envelope`` in ``interfaces/graphs/_node_helpers.py``: both
read ``state["envelope"]`` and bind its log context plus span attributes
for the duration of the node body. The legacy helper additionally records
a node duration metric; that responsibility moves to LangGraph's own
runtime instrumentation in this harness, so the decorator stays
single-purpose.

Single public symbol: :func:`with_envelope`.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import structlog
from opentelemetry import trace as otel_trace


_StateT = TypeVar("_StateT", bound=dict[str, Any])

WorkflowNode = Callable[[_StateT], Awaitable[dict[str, Any]]]


def with_envelope(node_fn: WorkflowNode[_StateT]) -> WorkflowNode[_StateT]:
    """
    Return a LangGraph-compatible wrapper that binds envelope identity.

    The wrapper reads ``state["envelope"]`` (an :class:`Envelope` minted at
    ingress), sets the six envelope-owned mandatory OTel span attributes
    (RFC \xa713.2) on the current span, and binds the envelope's log
    context to ``structlog.contextvars`` for the duration of the wrapped
    node. Bindings auto-clean on both success and exception paths.

    The wrapped node's return value passes through unchanged; the
    decorator does not interpret partial-state dicts. Exceptions raised
    inside the node propagate unchanged.

    :param node_fn: The async LangGraph node function to wrap. Must accept
        a state dict as its only positional argument and return a partial
        state dict.
    """

    @functools.wraps(node_fn)
    async def wrapped(state: _StateT) -> dict[str, Any]:
        envelope = state["envelope"]
        otel_trace.get_current_span().set_attributes(envelope.to_span_attributes())
        with structlog.contextvars.bound_contextvars(**envelope.to_log_context()):
            return await node_fn(state)

    return wrapped
