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


_NODE_TRACER = otel_trace.get_tracer("sentinel.node")
_PIPELINE_TRACER = otel_trace.get_tracer("sentinel.pipeline")


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
        # Open an explicit child span for the node so the agent.run() and tool
        # spans nest under a stable, named parent. pydantic-graph's own
        # ``run node X`` span is unreliable for nodes that call into
        # pydantic-ai (its instrumented agent context detaches in practice),
        # leaving classifier/analyser agent spans floating as siblings of the
        # graph iteration span. Owning the node span here makes the parent
        # explicit regardless of pydantic-graph's behaviour.
        span_name = f"{pipeline}.{node}"
        with _NODE_TRACER.start_as_current_span(span_name):
            # Use ``get_current_span`` so callers patching the OTel API at
            # the ``_node_helpers.otel_trace`` reference can observe the
            # attribute set in unit tests; in production this returns the
            # same span just opened above.
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


async def run_pipeline_with_envelope[T](
    *,
    pipeline: str,
    envelope: envelope_mod.Envelope,
    input_payload: str,
    fn: Callable[[], Awaitable[T]],
    serialize_output: Callable[[T], str],
) -> T:
    """
    Run a top-level pipeline body inside a parent OTel span carrying envelope
    identity, ``team_profile``, and Langfuse-namespaced trace I/O attributes.

    The pydantic-graph ``run graph ...`` span carries only the static graph
    schema, so without a wrapping span Langfuse renders the trace with no
    input or output. This helper opens ``sre.investigation_pipeline`` (or
    similar) as the new root observation, stamps the envelope's mandatory
    attributes plus ``langfuse.observation.input`` before the run, and
    stamps ``langfuse.observation.output`` once the body returns. Children
    inherit the envelope attrs through ``MandatoryAttributesPropagator``.

    :param pipeline: Pipeline label (e.g. ``"sre"``); used as the span name.
    :param envelope: Identity envelope minted at ingress.
    :param input_payload: Pre-serialised input string for Langfuse trace I/O.
    :param fn: Async callable producing the pipeline output.
    :param serialize_output: Callable that turns the output into a string for
        ``langfuse.observation.output``. Called only when ``fn`` returns
        normally; on exception the output attribute is left unset.
    """
    span_name = f"{pipeline}.investigation_pipeline"
    attributes: dict[str, otel_types.AttributeValue] = {
        "langfuse.observation.type": "chain",
        "langfuse.observation.input": input_payload,
    }
    attributes.update(envelope.to_span_attributes())
    attributes.update(_team_profile_attribute())

    with _PIPELINE_TRACER.start_as_current_span(span_name, attributes=attributes) as span:
        result = await fn()
        try:
            span.set_attribute("langfuse.observation.output", serialize_output(result))
        except Exception as exc:
            logs.log_exception(
                exc,
                params={"event": "otel.pipeline_output.serialize_failed"},
            )
        return result
