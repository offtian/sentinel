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
from sentinel.utils.observability import spans as obs_spans


def _get_team_profile() -> str:
    """
    Return the active team_id string, or empty on config failure.

    Reads ``team_id`` from the active configuration. On any failure (config
    bootstrap is broken, registry lookup raises), logs a structured warning
    and returns an empty string so the rest of the envelope attributes still
    land on the span.
    """
    try:
        return config_mod.get_config().team_id
    except Exception as exc:
        logs.log_event("otel.team_profile.unset", params={"reason": str(exc)})
        return ""


# ``_NODE_TRACER`` / ``_PIPELINE_TRACER`` start out as ``None`` and are
# resolved lazily via :func:`_get_node_tracer` / :func:`_get_pipeline_tracer`
# at the point of use. Acquiring the tracer at module-import time bound the
# helper to whatever ``TracerProvider`` was current then — typically the
# global ``ProxyTracerProvider`` *before* ``bootstrap.initialise()`` ran
# ``logfire.configure()``. The proxy lazily resolves to the real tracer on
# first use, but only once: a tracer cached at module load can outlive a
# subsequent test setup that swaps the provider, leaving Sentinel emitting
# spans through a dead provider while pydantic-ai (which constructs its
# ``InstrumentationSettings`` per-run) emits through the live one. The
# observable symptom was agent ``... run`` spans drifting up to be siblings
# of the graph's pipeline span instead of nesting under
# ``investigation.classify_alert`` / ``... .analyse_root_cause``.
#
# These names remain module-level so unit tests can keep patching them via
# ``mock.patch.object(_node_helpers, "_NODE_TRACER", fake_tracer)``; the
# helpers honour a patched value when present and fall back to a live
# ``get_tracer`` call otherwise.
_NODE_TRACER: otel_trace.Tracer | None = None
_PIPELINE_TRACER: otel_trace.Tracer | None = None


def _get_node_tracer() -> otel_trace.Tracer:
    """
    Return the tracer used to open per-node spans, resolved at call time.

    Honours a test-patched ``_NODE_TRACER`` so the existing unit tests still
    intercept span creation, and otherwise calls ``otel_trace.get_tracer``
    fresh on every invocation so the global ``TracerProvider`` installed by
    ``bootstrap.init_traces`` is picked up regardless of import order.
    """
    if _NODE_TRACER is not None:
        return _NODE_TRACER
    return otel_trace.get_tracer("sentinel.node")


def _get_pipeline_tracer() -> otel_trace.Tracer:
    """
    Return the tracer used to open the top-level pipeline span, resolved at
    call time. See :func:`_get_node_tracer` for the rationale.
    """
    if _PIPELINE_TRACER is not None:
        return _PIPELINE_TRACER
    return otel_trace.get_tracer("sentinel.pipeline")


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
    ``fn``, plus the ``team_profile``, ``pipeline``/``node`` labels, and
    Langfuse session/user grouping attributes via :class:`NodeSpanAttributes`.
    The remaining two mandatory attributes (``prompt_version_sha``, ``model_id``)
    are set at agent invocation sites via :func:`agents.utils.set_agent_span_attributes`.

    :param pipeline: Pipeline label (e.g. ``"sre"``, ``"support"``).
    :param node: Node label (e.g. ``"classify_alert"``).
    :param fn: Async callable representing the node's work.
    :param envelope: Optional identity envelope. When provided, its six
        canonical fields are set on the current OTel span as attributes.
    """

    async def _runner() -> T:
        if envelope is not None:
            attributes: dict[str, otel_types.AttributeValue] = obs_spans.NodeSpanAttributes(
                request_id=str(envelope.request_id),
                tenant_id=envelope.tenant_id,
                cluster_id=envelope.cluster_id,
                region=envelope.region,
                pii_class=envelope.pii_class,
                received_at=envelope.received_at.isoformat(),
                pipeline=pipeline,
                node=node,
                team_profile=_get_team_profile(),
                langfuse_session_id=str(envelope.request_id),
                langfuse_user_id=envelope.tenant_id,
            ).to_otel_dict()
        else:
            attributes = {"langfuse.observation.type": "chain"}
        # Open an explicit child span for the node so the agent.run() and tool
        # spans nest under a stable, named parent. pydantic-graph's own
        # ``run node X`` span is unreliable for nodes that call into
        # pydantic-ai (its instrumented agent context detaches in practice),
        # leaving classifier/analyser agent spans floating as siblings of the
        # graph iteration span. Owning the node span here makes the parent
        # explicit regardless of pydantic-graph's behaviour.
        #
        # ``_get_node_tracer()`` resolves the tracer at call time so a global
        # ``TracerProvider`` installed by ``bootstrap.init_traces`` after this
        # module imported is picked up. Setting attributes on the span yielded
        # by the context manager (rather than ``get_current_span()``) keeps
        # them on our owned span even if the outer trace context is unusual.
        span_name = f"{pipeline}.{node}"
        with _get_node_tracer().start_as_current_span(span_name) as span:
            span.set_attributes(attributes)
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
        **obs_spans.NodeSpanAttributes(
            request_id=str(envelope.request_id),
            tenant_id=envelope.tenant_id,
            cluster_id=envelope.cluster_id,
            region=envelope.region,
            pii_class=envelope.pii_class,
            received_at=envelope.received_at.isoformat(),
            pipeline=pipeline,
            node="pipeline",
            team_profile=_get_team_profile(),
            langfuse_session_id=str(envelope.request_id),
            langfuse_user_id=envelope.tenant_id,
        ).to_otel_dict(),
        "langfuse.observation.input": input_payload,
    }

    with _get_pipeline_tracer().start_as_current_span(span_name, attributes=attributes) as span:
        result = await fn()
        try:
            span.set_attribute("langfuse.observation.output", serialize_output(result))
        except Exception as exc:
            logs.log_exception(
                exc,
                params={"event": "otel.pipeline_output.serialize_failed"},
            )
        return result
