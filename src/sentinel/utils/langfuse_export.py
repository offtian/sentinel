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
        # ReadableSpan is the *read* projection; the live ``Span`` object
        # passed to ``on_end`` is in fact a ``sdk.trace.Span`` that still
        # exposes ``set_attribute`` while the span is being exported. The
        # ``ReadableSpan`` type hint comes from the SpanProcessor signature so
        # we narrow via Any to call the mutator. The OTel ``set_attribute`` API
        # is positional (``key, value``) by spec — FBT003 noqa is the
        # documented carve-out for third-party SDK boundary calls.
        mutable_span: Any = span
        mutable_span.set_attribute("_validation_failed", True)  # noqa: FBT003
        mutable_span.set_attribute("_missing_attrs", missing)
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
