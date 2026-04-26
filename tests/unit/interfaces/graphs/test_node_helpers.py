from __future__ import annotations

import asyncio
import contextlib
from unittest import mock

from opentelemetry import trace as otel_trace

from sentinel import config as config_mod
from sentinel.interfaces.graphs import _node_helpers
from sentinel.utils import logs, metrics
from tests import factories


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

        fake_span = mock.MagicMock()

        # When the wrapper executes
        with (
            mock.patch.object(metrics, "record_pipeline_node_duration"),
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
        fake_span.set_attributes.assert_called_once()
        attrs_set = fake_span.set_attributes.call_args.args[0]
        assert attrs_set == {"langfuse.observation.type": "chain"}

    def test_sets_envelope_span_attributes_when_envelope_provided(self):
        # Given a wrapper invoked with an envelope and a config that resolves team_id
        async def fake_run():
            return "ok"

        envelope = factories.make_envelope()
        fake_span = mock.MagicMock()
        fake_config = mock.MagicMock()
        fake_config.team_id = "sre"

        # When the wrapper executes
        with (
            mock.patch.object(metrics, "record_pipeline_node_duration"),
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
        # the span in a single call
        fake_span.set_attributes.assert_called_once()
        attrs_set = fake_span.set_attributes.call_args.args[0]
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
        assert set(attrs_set.keys()) == expected_keys
        assert attrs_set["request_id"] == str(envelope.request_id)
        assert attrs_set["tenant_id"] == envelope.tenant_id
        assert attrs_set["cluster_id"] == envelope.cluster_id
        assert attrs_set["region"] == envelope.region
        assert attrs_set["pii_class"] == envelope.pii_class
        assert attrs_set["received_at"] == envelope.received_at.isoformat()
        assert attrs_set["team_profile"] == "sre"
        assert attrs_set["langfuse.observation.type"] == "chain"
        assert attrs_set["langfuse.session.id"] == str(envelope.request_id)
        assert attrs_set["langfuse.user.id"] == envelope.tenant_id

    def test_skips_team_profile_and_warns_when_get_config_raises(self):
        # Given a wrapper with an envelope but get_config blowing up at lookup time
        async def fake_run():
            return "ok"

        envelope = factories.make_envelope()
        fake_span = mock.MagicMock()

        # When the wrapper executes
        with (
            mock.patch.object(metrics, "record_pipeline_node_duration"),
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
        fake_span.set_attributes.assert_called_once()
        attrs_set = fake_span.set_attributes.call_args.args[0]
        assert "team_profile" not in attrs_set
        assert "request_id" in attrs_set
        log_event.assert_called_once()
        assert log_event.call_args.args[0] == "otel.team_profile.unset"

    def test_sets_envelope_attributes_even_when_inner_fn_raises(self):
        # Given a wrapper with an envelope and a failing inner function
        async def failing_run():
            raise RuntimeError("downstream failure")

        envelope = factories.make_envelope()
        fake_span = mock.MagicMock()
        fake_config = mock.MagicMock()
        fake_config.team_id = "sre"

        # When the wrapper executes and the inner function raises
        with (
            mock.patch.object(metrics, "record_pipeline_node_duration"),
            mock.patch.object(otel_trace, "get_current_span", return_value=fake_span),
            mock.patch.object(config_mod, "get_config", return_value=fake_config),
        ):
            wrapped = _node_helpers.instrumented_node_run(
                pipeline="investigation",
                node="classify_alert",
                fn=failing_run,
                envelope=envelope,
            )
            with contextlib.suppress(RuntimeError):
                asyncio.run(wrapped())

        # Then span attributes were set before the failure surfaced
        fake_span.set_attributes.assert_called_once()
