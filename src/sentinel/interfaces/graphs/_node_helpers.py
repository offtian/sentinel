"""

Helpers for instrumenting Pydantic Graph pipeline nodes.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

import structlog
from opentelemetry import trace as otel_trace
from opentelemetry.util import types as otel_types

from sentinel import config as config_mod
from sentinel.data.primitives import envelope as envelope_mod
from sentinel.utils import logs, metrics


def _team_profile_attribute() -> dict[str, otel_types.AttributeValue]:
    """
    Return a single-key span-attribute dict for ``team_profile``, or empty.

    Reads ``team_id`` from the active configuration. On any failure (config
    bootstrap is broken, registry lookup raises), logs a structured warning
    and returns an empty dict so the rest of the envelope attributes still
    land on the span.
    """
    try:
        return {"team_profile": config_mod.get_config().team_id}
    except Exception as exc:
        logs.log_event("otel.team_profile.unset", params={"reason": str(exc)})
        return {}


def instrumented_node_run[T](
    *,
    pipeline: str,
    node: str,
    fn: Callable[[], Awaitable[T]],
    envelope: envelope_mod.Envelope | None = None,
) -> Callable[[], Awaitable[T]]:
    """

    Wrap a node run callable to record its duration as a metric.

    Records duration with status=ok on normal return and status=error if the
    callable raises. The original exception is re-raised unchanged.

    When ``envelope`` is provided, attaches the six envelope-owned mandatory
    OTel span attributes per RFC §13.2 to the current span before invoking
    ``fn``, plus the ``team_profile`` attribute resolved from the active
    configuration. The remaining two mandatory attributes
    (``prompt_version_sha``, ``model_id``) are set at agent invocation sites
    via :func:`agents.utils.set_agent_span_attributes`.

    :param pipeline: Pipeline label (e.g. ``"sre"``, ``"support"``).
    :param node: Node label (e.g. ``"classify_alert"``).
    :param fn: Async callable representing the node's work.
    :param envelope: Optional identity envelope. When provided, its six
        canonical fields are set on the current OTel span as attributes.
    """

    async def _runner() -> T:
        attributes: dict[str, otel_types.AttributeValue] = {"langfuse.observation.type": "chain"}
        if envelope is not None:
            attributes.update(envelope.to_span_attributes())
            attributes.update(_team_profile_attribute())
        otel_trace.get_current_span().set_attributes(attributes)
        start = time.perf_counter()
        status = "ok"
        try:
            return await fn()
        except Exception:
            status = "error"
            raise
        finally:
            duration = time.perf_counter() - start
            metrics.record_pipeline_node_duration(
                pipeline=pipeline,
                node=node,
                duration_seconds=duration,
                status=status,
            )

    return _runner


async def run_node_with_envelope[T](
    *,
    pipeline: str,
    node: str,
    envelope: envelope_mod.Envelope,
    fn: Callable[[], Awaitable[T]],
) -> T:
    """

    Run a node body bound to the given envelope's identity context.

    Combines three responsibilities:
    - Bind the envelope's log context to ``structlog.contextvars`` for the
      duration of ``fn`` (auto-cleaned on exit, even on exceptions).
    - Set the six envelope-owned OTel span attributes on the current span
      via :func:`instrumented_node_run`.
    - Record node duration as a metric.

    :param pipeline: Pipeline label (e.g. ``"sre"``, ``"support"``).
    :param node: Node label (e.g. ``"classify_alert"``).
    :param envelope: Identity envelope minted at ingress.
    :param fn: Async callable representing the node's work.
    """
    with structlog.contextvars.bound_contextvars(**envelope.to_log_context()):
        return await instrumented_node_run(
            pipeline=pipeline,
            node=node,
            fn=fn,
            envelope=envelope,
        )()
