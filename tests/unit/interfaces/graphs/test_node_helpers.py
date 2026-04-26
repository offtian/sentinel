from __future__ import annotations

import asyncio
import contextlib
from unittest import mock

from opentelemetry import trace as otel_trace

from sentinel import config as config_mod
from sentinel.interfaces.graphs import _node_helpers
from sentinel.utils import logs, metrics
from tests import factories


def _make_fake_tracer_and_span() -> tuple[mock.MagicMock, mock.MagicMock]:
    """Return ``(tracer, span)`` where ``tracer.start_as_current_span`` is a cm."""
    fake_span = mock.MagicMock()
    fake_context = mock.MagicMock()
    fake_context.__enter__ = mock.MagicMock(return_value=fake_span)
    fake_context.__exit__ = mock.MagicMock(return_value=False)
    fake_tracer = mock.MagicMock()
    fake_tracer.start_as_current_span.return_value = fake_context
    return fake_tracer, fake_span


def _start_span_attributes(fake_span: mock.MagicMock) -> dict[str, object]:
    """Return the attributes set on the (mocked) current span."""
    return fake_span.set_attributes.call_args.args[0]


def _start_span_name(fake_tracer: mock.MagicMock) -> str:
    return fake_tracer.start_as_current_span.call_args.args[0]


class TestInstrumentedNodeRun:
    def test_records_duration_on_success(self):
        # Given an async function returning a value
        async def fake_run():
            return "result"

        # When wrapped and executed
        with mock.patch.object(metrics, "record_pipeline_node_duration") as recorder:
            wrapped = _node_helpers.instrumented_node_run(
                pipeline="investigation",
                node="classify_alert",
                fn=fake_run,
            )
            result = asyncio.run(wrapped())

        # Then the result is returned and the duration is recorded with status=ok
        assert result == "result"
        recorder.assert_called_once()
        kwargs = recorder.call_args.kwargs
        assert kwargs["pipeline"] == "investigation"
        assert kwargs["node"] == "classify_alert"
        assert kwargs["status"] == "ok"
        assert kwargs["duration_seconds"] >= 0

    def test_records_error_status_on_exception(self):
        # Given an async function that raises
        async def fake_run():
            raise ValueError("boom")

        # When wrapped and executed
        with mock.patch.object(metrics, "record_pipeline_node_duration") as recorder:
            wrapped = _node_helpers.instrumented_node_run(
                pipeline="investigation",
                node="classify_alert",
                fn=fake_run,
            )
            with contextlib.suppress(ValueError):
                asyncio.run(wrapped())

        # Then duration is recorded with status=error
        recorder.assert_called_once()
        assert recorder.call_args.kwargs["status"] == "error"

    def test_sets_only_observation_type_when_envelope_is_none(self):
        # Given a wrapper invoked without an envelope
        async def fake_run():
            return "ok"

        fake_tracer, fake_span = _make_fake_tracer_and_span()

        # When the wrapper executes
        with (
            mock.patch.object(metrics, "record_pipeline_node_duration"),
            mock.patch.object(_node_helpers, "_NODE_TRACER", fake_tracer),
            mock.patch.object(otel_trace, "get_current_span", return_value=fake_span),
        ):
            wrapped = _node_helpers.instrumented_node_run(
                pipeline="investigation",
                node="classify_alert",
                fn=fake_run,
            )
            asyncio.run(wrapped())

        # Then only the Langfuse observation-type attribute is set so the
        # Langfuse UI classifies the graph node span as a "chain"
        attrs = _start_span_attributes(fake_span)
        assert attrs == {"langfuse.observation.type": "chain"}
        assert _start_span_name(fake_tracer) == "investigation.classify_alert"

    def test_sets_envelope_span_attributes_when_envelope_provided(self):
        # Given a wrapper invoked with an envelope and a config that resolves team_id
        async def fake_run():
            return "ok"

        envelope = factories.make_envelope()
        fake_tracer, fake_span = _make_fake_tracer_and_span()
        fake_config = mock.MagicMock()
        fake_config.team_id = "sre"

        # When the wrapper executes
        with (
            mock.patch.object(metrics, "record_pipeline_node_duration"),
            mock.patch.object(_node_helpers, "_NODE_TRACER", fake_tracer),
            mock.patch.object(otel_trace, "get_current_span", return_value=fake_span),
            mock.patch.object(config_mod, "get_config", return_value=fake_config),
        ):
            wrapped = _node_helpers.instrumented_node_run(
                pipeline="investigation",
                node="classify_alert",
                fn=fake_run,
                envelope=envelope,
            )
            asyncio.run(wrapped())

        # Then the six envelope-owned attributes plus team_profile and the
        # Langfuse-namespaced observation/session/user attributes land on
        # the span as construction attributes
        attrs = _start_span_attributes(fake_span)
        expected_keys = {
            "request_id",
            "tenant_id",
            "cluster_id",
            "region",
            "pii_class",
            "received_at",
            "team_profile",
            "langfuse.observation.type",
            "langfuse.session.id",
            "langfuse.user.id",
        }
        assert set(attrs.keys()) == expected_keys
        assert attrs["request_id"] == str(envelope.request_id)
        assert attrs["tenant_id"] == envelope.tenant_id
        assert attrs["cluster_id"] == envelope.cluster_id
        assert attrs["region"] == envelope.region
        assert attrs["pii_class"] == envelope.pii_class
        assert attrs["received_at"] == envelope.received_at.isoformat()
        assert attrs["team_profile"] == "sre"
        assert attrs["langfuse.observation.type"] == "chain"
        assert attrs["langfuse.session.id"] == str(envelope.request_id)
        assert attrs["langfuse.user.id"] == envelope.tenant_id

    def test_skips_team_profile_and_warns_when_get_config_raises(self):
        # Given a wrapper with an envelope but get_config blowing up at lookup time
        async def fake_run():
            return "ok"

        envelope = factories.make_envelope()
        fake_tracer, fake_span = _make_fake_tracer_and_span()

        # When the wrapper executes
        with (
            mock.patch.object(metrics, "record_pipeline_node_duration"),
            mock.patch.object(_node_helpers, "_NODE_TRACER", fake_tracer),
            mock.patch.object(otel_trace, "get_current_span", return_value=fake_span),
            mock.patch.object(
                config_mod, "get_config", side_effect=RuntimeError("config bootstrap failure")
            ),
            mock.patch.object(logs, "log_event") as log_event,
        ):
            wrapped = _node_helpers.instrumented_node_run(
                pipeline="investigation",
                node="classify_alert",
                fn=fake_run,
                envelope=envelope,
            )
            asyncio.run(wrapped())

        # Then the envelope attrs still land on the span and the failure was logged
        attrs = _start_span_attributes(fake_span)
        assert "team_profile" not in attrs
        assert "request_id" in attrs
        log_event.assert_called_once()
        assert log_event.call_args.args[0] == "otel.team_profile.unset"

    def test_sets_envelope_attributes_even_when_inner_fn_raises(self):
        # Given a wrapper with an envelope and a failing inner function
        async def failing_run():
            raise RuntimeError("downstream failure")

        envelope = factories.make_envelope()
        fake_tracer, fake_span = _make_fake_tracer_and_span()
        fake_config = mock.MagicMock()
        fake_config.team_id = "sre"

        # When the wrapper executes and the inner function raises
        with (
            mock.patch.object(metrics, "record_pipeline_node_duration"),
            mock.patch.object(_node_helpers, "_NODE_TRACER", fake_tracer),
            mock.patch.object(otel_trace, "get_current_span", return_value=fake_span),
            mock.patch.object(config_mod, "get_config", return_value=fake_config),
            contextlib.suppress(RuntimeError),
        ):
            wrapped = _node_helpers.instrumented_node_run(
                pipeline="investigation",
                node="classify_alert",
                fn=failing_run,
                envelope=envelope,
            )
            asyncio.run(wrapped())

        # Then the span was opened with envelope attributes before the failure surfaced
        attrs = _start_span_attributes(fake_span)
        assert "request_id" in attrs
        assert attrs["team_profile"] == "sre"


class TestRunPipelineWithEnvelope:
    def test_sets_input_at_start_and_output_at_finish(self):
        # Given an envelope, an input payload, and a body returning a value
        envelope = factories.make_envelope()
        fake_span = mock.MagicMock()
        fake_context = mock.MagicMock()
        fake_context.__enter__ = mock.MagicMock(return_value=fake_span)
        fake_context.__exit__ = mock.MagicMock(return_value=False)
        fake_tracer = mock.MagicMock()
        fake_tracer.start_as_current_span.return_value = fake_context
        fake_config = mock.MagicMock()
        fake_config.team_id = "sre"

        async def fake_body():
            return {"reply": "ok"}

        # When the helper wraps the body with input/output capture
        with (
            mock.patch.object(_node_helpers, "_PIPELINE_TRACER", fake_tracer),
            mock.patch.object(config_mod, "get_config", return_value=fake_config),
        ):
            result = asyncio.run(
                _node_helpers.run_pipeline_with_envelope(
                    pipeline="sre",
                    envelope=envelope,
                    input_payload='{"alert":"high cpu"}',
                    fn=fake_body,
                    serialize_output=lambda value: f"reply={value['reply']}",
                )
            )

        # Then the span carried envelope, team_profile, and Langfuse input at start
        assert result == {"reply": "ok"}
        start_kwargs = fake_tracer.start_as_current_span.call_args.kwargs
        attributes = start_kwargs["attributes"]
        assert fake_tracer.start_as_current_span.call_args.args == ("sre.investigation_pipeline",)
        assert attributes["langfuse.observation.type"] == "chain"
        assert attributes["langfuse.observation.input"] == '{"alert":"high cpu"}'
        assert attributes["request_id"] == str(envelope.request_id)
        assert attributes["team_profile"] == "sre"

        # And the output attribute was stamped after the body returned
        fake_span.set_attribute.assert_called_once_with("langfuse.observation.output", "reply=ok")

    def test_skips_output_attribute_when_serialiser_raises(self):
        # Given a body that succeeds but a serialiser that raises
        envelope = factories.make_envelope()
        fake_span = mock.MagicMock()
        fake_context = mock.MagicMock()
        fake_context.__enter__ = mock.MagicMock(return_value=fake_span)
        fake_context.__exit__ = mock.MagicMock(return_value=False)
        fake_tracer = mock.MagicMock()
        fake_tracer.start_as_current_span.return_value = fake_context
        fake_config = mock.MagicMock()
        fake_config.team_id = "sre"

        async def fake_body():
            return object()

        def broken_serialiser(_value):
            raise ValueError("cannot serialise")

        # When the helper runs and the serialiser raises
        with (
            mock.patch.object(_node_helpers, "_PIPELINE_TRACER", fake_tracer),
            mock.patch.object(config_mod, "get_config", return_value=fake_config),
            mock.patch.object(logs, "log_exception") as log_exception,
        ):
            result = asyncio.run(
                _node_helpers.run_pipeline_with_envelope(
                    pipeline="sre",
                    envelope=envelope,
                    input_payload="payload",
                    fn=fake_body,
                    serialize_output=broken_serialiser,
                )
            )

        # Then the body's result is still returned, the failure is logged,
        # and no output attribute is stamped on the span
        assert result is not None
        log_exception.assert_called_once()
        assert log_exception.call_args.kwargs["params"] == {
            "event": "otel.pipeline_output.serialize_failed"
        }
        fake_span.set_attribute.assert_not_called()

    def test_does_not_swallow_body_exceptions(self):
        # Given a body that raises
        envelope = factories.make_envelope()
        fake_span = mock.MagicMock()
        fake_context = mock.MagicMock()
        fake_context.__enter__ = mock.MagicMock(return_value=fake_span)
        fake_context.__exit__ = mock.MagicMock(return_value=False)
        fake_tracer = mock.MagicMock()
        fake_tracer.start_as_current_span.return_value = fake_context
        fake_config = mock.MagicMock()
        fake_config.team_id = "sre"

        async def failing_body():
            raise RuntimeError("graph failure")

        # When the helper executes and the body raises
        with (
            mock.patch.object(_node_helpers, "_PIPELINE_TRACER", fake_tracer),
            mock.patch.object(config_mod, "get_config", return_value=fake_config),
            contextlib.suppress(RuntimeError),
        ):
            asyncio.run(
                _node_helpers.run_pipeline_with_envelope(
                    pipeline="sre",
                    envelope=envelope,
                    input_payload="payload",
                    fn=failing_body,
                    serialize_output=lambda value: str(value),
                )
            )

        # Then the input attribute was still set at start, but no output stamped
        start_kwargs = fake_tracer.start_as_current_span.call_args.kwargs
        assert start_kwargs["attributes"]["langfuse.observation.input"] == "payload"
        fake_span.set_attribute.assert_not_called()
