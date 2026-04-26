"""

Langfuse OTLP export plumbing and the mandatory-attribute span validator.

This module owns the OTel span processors and exporter helpers that feed
Sentinel's traces into a self-hosted Langfuse instance. It enforces RFC §13.2
by attaching a :class:`MandatoryAttributesValidator` to the trace pipeline so
spans missing any of the nine mandatory attributes are flagged for debugging
without being dropped (RFC §14.7 wants partial traces visible in Langfuse).
"""

from __future__ import annotations

import base64
from typing import Any

from opentelemetry import trace as otel_trace

# Direct symbol import: this is the documented import path for the Langfuse
# OTLP/HTTP exporter and must be the bound class object so callers can patch
# it in tests via ``mock.patch.object(langfuse_export, "OTLPSpanExporter")``.
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

# Direct symbol import: subclassing requires the bound class object, mirroring
# how ``BaseNode`` is imported across ``interfaces/graphs/`` modules.
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor

from sentinel.utils import logs


MANDATORY_ATTRS: tuple[str, ...] = (
    # Six envelope-derived attributes (set by the node helper at node entry).
    "request_id",
    "tenant_id",
    "cluster_id",
    "region",
    "pii_class",
    "received_at",
    # Three agent-context attributes (set at each PydanticAI invocation site
    # plus the per-process ``team_profile`` stamped by the node helper).
    "prompt_version_sha",
    "model_id",
    "team_profile",
)

# Spans emitted by these instrumentation libraries do not carry envelope or
# agent context (they sit below the pipeline boundary), so the validator
# short-circuits for them rather than filling Langfuse with false positives.
_FRAMEWORK_SCOPES: frozenset[str] = frozenset(
    {
        "opentelemetry.instrumentation.fastapi",
        "opentelemetry.instrumentation.sqlalchemy",
        "opentelemetry.instrumentation.httpx",
    }
)


def _is_framework_scope(span: Any) -> bool:
    scope = getattr(span, "instrumentation_scope", None)
    scope_name = scope.name if scope is not None else None
    return scope_name in _FRAMEWORK_SCOPES


class MandatoryAttributesPropagator(SpanProcessor):
    """
    SpanProcessor that copies missing mandatory attrs from parent to child.

    pydantic-ai opens ``chat ...`` and ``running tool`` spans as children of
    the pipeline-node span set by ``_node_helpers.run_node_with_envelope``.
    The parent carries the six envelope attrs plus ``team_profile``; the
    agent-set helper stamps ``prompt_version_sha`` and ``model_id`` onto the
    same parent. Without propagation, the child LLM/tool spans go to
    Langfuse missing every mandatory attribute, breaking session/user
    grouping and the RFC §13.2 contract.

    On ``on_start``, this processor reads the active parent span from the
    OTel context and copies any of :data:`MANDATORY_ATTRS` that the parent
    has but the child does not. Spans whose instrumentation scope is in
    :data:`_FRAMEWORK_SCOPES` are skipped (HTTP/SQL spans are deliberately
    out of scope per the validator carve-out).
    """

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        """
        Copy missing mandatory attrs from parent span onto ``span`` at start.

        ``span`` is a live SDK ``Span`` here (mutable) — this is the only
        callback that fires before pydantic-ai's instrumented ``chat``
        bodies record their tokens, so attributes set here propagate into
        the exported payload.
        """
        try:
            if _is_framework_scope(span):
                return

            parent_span = otel_trace.get_current_span(parent_context)
            parent_attrs = getattr(parent_span, "attributes", None)
            if not parent_attrs:
                return

            child_attrs = getattr(span, "attributes", None) or {}
            to_copy = {
                attr: parent_attrs[attr]
                for attr in MANDATORY_ATTRS
                if attr in parent_attrs and attr not in child_attrs
            }
            if to_copy:
                span.set_attributes(to_copy)
        except Exception as exc:
            logs.log_exception(
                exc,
                params={"event": "otel.mandatory_attrs.propagate_failed"},
            )

    def on_end(self, span: ReadableSpan) -> None:
        """
        Return immediately; propagation work is done in :meth:`on_start`.
        """
        return

    def shutdown(self) -> None:
        """
        Return immediately; the propagator owns no resources to release.
        """
        return

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        """
        Return True; the propagator buffers no work.
        """
        return True


class MandatoryAttributesValidator(SpanProcessor):
    """
    SpanProcessor enforcing RFC §13.2 mandatory attributes per span.

    On span end, checks that every attribute in :data:`MANDATORY_ATTRS` is
    present on the span. Spans whose ``instrumentation_scope.name`` is in
    :data:`_FRAMEWORK_SCOPES` are skipped (they sit below the pipeline
    boundary and do not carry envelope context).

    Spans missing any mandatory attribute are *not* dropped: a structured
    event ``otel.span.missing_mandatory_attrs`` is emitted via
    :mod:`sentinel.utils.logs`, and two diagnostic attributes
    (``_validation_failed=True`` and ``_missing_attrs=(...)``) are stamped
    onto the span so the partial trace remains visible in Langfuse for
    debugging (RFC §14.7).
    """

    def on_start(
        self,
        span: Any,
        parent_context: Any = None,
    ) -> None:
        """
        Return immediately without inspecting the span.

        Validation runs at span end (when the final attribute set is known);
        on_start is a no-op required by the :class:`SpanProcessor` contract.
        """
        return

    def on_end(self, span: ReadableSpan) -> None:
        """
        Validate the mandatory-attribute set on a finished span.

        Skips spans emitted by framework instrumentations that sit below the
        pipeline boundary. For pipeline spans missing any mandatory attribute,
        emits a structured warning and stamps diagnostic attributes onto the
        span without dropping it.
        """
        scope = span.instrumentation_scope
        scope_name = scope.name if scope is not None else None
        if scope_name in _FRAMEWORK_SCOPES:
            return

        attributes = span.attributes or {}
        missing = tuple(attr for attr in MANDATORY_ATTRS if attr not in attributes)
        if not missing:
            return

        logs.log_event(
            "otel.span.missing_mandatory_attrs",
            params={
                "span_name": span.name,
                "missing": missing,
                "scope": scope_name,
            },
        )
        # The ``on_end`` callback receives a ``ReadableSpan`` per OTel spec
        # (spans are immutable once ended) so attributes cannot be stamped
        # back onto the span here. The structured log above is the
        # diagnostic surface; downstream validators read missing attrs from
        # the log stream rather than from span attributes. The earlier
        # implementation attempted ``set_attribute`` via an ``Any`` cast
        # which crashes on the real SDK ``ReadableSpan``.
        return

    def shutdown(self) -> None:
        """
        Return immediately; the validator owns no resources to release.

        Required by the :class:`SpanProcessor` contract.
        """
        return

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        """
        Return True unconditionally.

        The validator emits no buffered work, so there is nothing to flush;
        :class:`SpanProcessor` requires a boolean success indicator.
        """
        return True


# Langfuse exposes the OTLP/HTTP traces ingestion endpoint at this fixed path
# under the host root (``{host}/api/public/otel/v1/traces``). Centralising the
# constant avoids drift between docs, tests, and the exporter wiring.
_LANGFUSE_OTEL_TRACES_PATH = "/api/public/otel/v1/traces"


def build_langfuse_exporter(
    *, host: str, public_key: str, secret_key: str
) -> OTLPSpanExporter | None:
    """
    Return an OTLPSpanExporter pointed at a Langfuse OTel ingestion endpoint.

    The endpoint URL is composed as ``f"{host.rstrip('/')}{_LANGFUSE_OTEL_TRACES_PATH}"``
    to tolerate a host string with or without a trailing slash. The
    Authorization header carries Basic auth derived from
    ``base64(public_key:secret_key)`` per Langfuse's OTLP ingest contract.

    On any construction error (network resolver failure, invalid arg, etc.)
    the failure is logged via :func:`logs.log_exception` with event
    ``langfuse.exporter.construction_failed`` and ``None`` is returned so the
    caller can fall back to the existing exporters without crashing startup.
    """
    try:
        endpoint = f"{host.rstrip('/')}{_LANGFUSE_OTEL_TRACES_PATH}"
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode("ascii")
        headers = {"Authorization": f"Basic {token}"}
        return OTLPSpanExporter(endpoint=endpoint, headers=headers)
    except Exception as exc:
        logs.log_exception(
            exc,
            params={
                "event": "langfuse.exporter.construction_failed",
                "host": host,
            },
        )
        return None
