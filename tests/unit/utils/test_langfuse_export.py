from __future__ import annotations

import base64
from unittest import mock

import pytest

from sentinel.utils import langfuse_export
from sentinel.utils import logs as logs_mod


def _all_attrs() -> dict[str, str]:
    """
    Return a dict containing every RFC §13.2 mandatory span attribute.
    """
    return {
        "request_id": "req-1",
        "tenant_id": "tenant-a",
        "cluster_id": "cluster-1",
        "region": "eu-west-1",
        "pii_class": "internal",
        "received_at": "2026-04-26T00:00:00Z",
        "prompt_version_sha": "deadbeef",
        "model_id": "openai/gpt-4.1",
        "team_profile": "sre",
    }


_SPAN_SPEC = (
    "name",
    "attributes",
    "instrumentation_scope",
    "set_attribute",
    "set_status",
    "end",
)


def _make_span(
    *,
    attributes: dict[str, str],
    scope_name: str | None = None,
    span_name: str = "test-span",
) -> mock.MagicMock:
    """
    Return a MagicMock matching the ReadableSpan surface the validator touches.

    Uses an explicit spec list rather than ``spec=ReadableSpan`` because the
    SDK's ``ReadableSpan`` projection deliberately omits ``set_attribute``;
    the live ``Span`` instance passed to ``on_end`` still exposes it during
    export, which is what the validator relies on.
    """
    span = mock.MagicMock(spec=_SPAN_SPEC)
    span.name = span_name
    span.attributes = attributes
    if scope_name is None:
        span.instrumentation_scope = None
    else:
        scope = mock.MagicMock()
        scope.name = scope_name
        span.instrumentation_scope = scope
    return span


class TestMandatoryAttributesValidator:
    def test_no_log_when_all_nine_attrs_present(self):
        # Given a ReadableSpan carrying every mandatory attribute
        span = _make_span(attributes=_all_attrs())
        validator = langfuse_export.MandatoryAttributesValidator()

        # When on_end is invoked under a patched log_event sink
        with mock.patch.object(logs_mod, "log_event") as patched_log:
            result = validator.on_end(span)

        # Then no event is logged and no debug attribute is stamped
        assert result is None
        patched_log.assert_not_called()
        span.set_attribute.assert_not_called()

    def test_logs_and_marks_when_attrs_missing(self):
        # Given a span missing prompt_version_sha and model_id
        attributes = _all_attrs()
        del attributes["prompt_version_sha"]
        del attributes["model_id"]
        span = _make_span(attributes=attributes, span_name="incomplete-span")
        validator = langfuse_export.MandatoryAttributesValidator()

        # When on_end is invoked under a patched log_event sink
        with mock.patch.object(logs_mod, "log_event") as patched_log:
            validator.on_end(span)

        # Then the missing-attrs event fires once with both names in order
        patched_log.assert_called_once()
        event_name, kwargs = patched_log.call_args.args[0], patched_log.call_args.kwargs
        assert event_name == "otel.span.missing_mandatory_attrs"
        assert kwargs["params"]["span_name"] == "incomplete-span"
        assert kwargs["params"]["missing"] == ("prompt_version_sha", "model_id")
        assert kwargs["params"]["scope"] is None

        # And the validator stamps the diagnostic attributes onto the span
        set_calls = span.set_attribute.call_args_list
        assert mock.call("_validation_failed", True) in set_calls  # noqa: FBT003
        assert mock.call("_missing_attrs", ("prompt_version_sha", "model_id")) in set_calls

    @pytest.mark.parametrize(
        "scope_name",
        [
            "opentelemetry.instrumentation.fastapi",
            "opentelemetry.instrumentation.sqlalchemy",
            "opentelemetry.instrumentation.httpx",
        ],
    )
    def test_skips_validation_for_framework_scope_span(self, scope_name: str):
        # Given a framework-scoped span with NO mandatory attributes at all
        span = _make_span(attributes={}, scope_name=scope_name)
        validator = langfuse_export.MandatoryAttributesValidator()

        # When on_end is invoked under a patched log_event sink
        with mock.patch.object(logs_mod, "log_event") as patched_log:
            validator.on_end(span)

        # Then no log fires and no attribute is stamped (carve-out wins)
        patched_log.assert_not_called()
        span.set_attribute.assert_not_called()

    def test_does_not_drop_span_when_validation_fails(self):
        # Given a span missing every mandatory attribute
        span = _make_span(attributes={}, span_name="empty-span")
        validator = langfuse_export.MandatoryAttributesValidator()

        # When on_end is invoked
        with mock.patch.object(logs_mod, "log_event"):
            result = validator.on_end(span)

        # Then on_end returns None and only the two diagnostic attrs were touched
        assert result is None
        attr_names = {call.args[0] for call in span.set_attribute.call_args_list}
        assert attr_names == {"_validation_failed", "_missing_attrs"}
        # And no lifecycle method (end / set_status) was called on the span
        span.end.assert_not_called()
        span.set_status.assert_not_called()


class TestSpanProcessorContract:
    def test_on_start_is_a_noop(self):
        # Given a fresh validator and a mock span
        validator = langfuse_export.MandatoryAttributesValidator()
        span = mock.MagicMock()

        # When on_start is invoked under a patched log sink
        with mock.patch.object(logs_mod, "log_event") as patched_log:
            result = validator.on_start(span, parent_context=None)

        # Then it returns None and does not log
        assert result is None
        patched_log.assert_not_called()

    def test_force_flush_returns_true(self):
        # Given a fresh validator
        validator = langfuse_export.MandatoryAttributesValidator()

        # When force_flush is called
        # Then it satisfies the SpanProcessor contract by returning True
        assert validator.force_flush(timeout_millis=0) is True

    def test_shutdown_returns_none(self):
        # Given a fresh validator
        validator = langfuse_export.MandatoryAttributesValidator()

        # When shutdown is called
        # Then it returns None without raising
        assert validator.shutdown() is None


class TestBuildLangfuseExporter:
    def test_url_is_built_with_otel_traces_path(self):
        # Given a Langfuse host with no trailing slash
        host = "http://lf.local"

        # When the exporter is built
        exporter = langfuse_export.build_langfuse_exporter(
            host=host,
            public_key="pk",
            secret_key="sk",  # noqa: S106
        )

        # Then the endpoint targets Langfuse's OTel ingestion path
        assert exporter is not None
        assert exporter._endpoint == "http://lf.local/api/public/otel/v1/traces"

    def test_url_strips_trailing_slash_on_host(self):
        # Given a host that already includes a trailing slash
        host = "http://lf.local/"

        # When the exporter is built
        exporter = langfuse_export.build_langfuse_exporter(
            host=host,
            public_key="pk",
            secret_key="sk",  # noqa: S106
        )

        # Then the endpoint contains exactly one slash before /api
        assert exporter is not None
        assert exporter._endpoint == "http://lf.local/api/public/otel/v1/traces"

    def test_authorization_header_is_basic_auth_of_pk_colon_sk(self):
        # Given a public/secret key pair
        public_key = "pk"
        secret_key = "sk"  # noqa: S105 — test fixture, not a real secret
        expected_token = base64.b64encode(b"pk:sk").decode("ascii")

        # When the exporter is built
        exporter = langfuse_export.build_langfuse_exporter(
            host="http://lf.local",
            public_key=public_key,
            secret_key=secret_key,
        )

        # Then the Authorization header is Basic <b64(public:secret)>
        assert exporter is not None
        assert exporter._headers["Authorization"] == f"Basic {expected_token}"

    def test_returns_none_and_logs_when_construction_raises(self):
        # Given OTLPSpanExporter raises during construction
        with (
            mock.patch.object(
                langfuse_export,
                "OTLPSpanExporter",
                side_effect=RuntimeError("nope"),
            ),
            mock.patch.object(logs_mod, "log_exception") as patched_log_exc,
        ):
            # When build_langfuse_exporter is called
            result = langfuse_export.build_langfuse_exporter(
                host="http://lf.local",
                public_key="pk",
                secret_key="sk",  # noqa: S106
            )

        # Then the helper returns None and surfaces a structured exception event
        assert result is None
        patched_log_exc.assert_called_once()
        params = patched_log_exc.call_args.kwargs["params"]
        assert params["event"] == "langfuse.exporter.construction_failed"
        assert params["host"] == "http://lf.local"
