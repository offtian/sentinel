from __future__ import annotations

import asyncio
import contextlib
from unittest import mock

from opentelemetry import trace as otel_trace

from sentinel.interfaces.graphs import _node_helpers
from sentinel.utils import metrics
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

    def test_does_not_set_span_attributes_when_envelope_is_none(self):
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

        # Then no span attributes were set
        fake_span.set_attributes.assert_not_called()

    def test_sets_envelope_span_attributes_when_envelope_provided(self):
        # Given a wrapper invoked with an envelope
        async def fake_run():
            return "ok"

        envelope = factories.make_envelope()
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
                envelope=envelope,
            )
            asyncio.run(wrapped())

        # Then the six envelope-owned mandatory attributes are set on the span
        fake_span.set_attributes.assert_called_once()
        attrs_set = fake_span.set_attributes.call_args.args[0]
        expected_keys = {
            "request_id",
            "tenant_id",
            "cluster_id",
            "region",
            "pii_class",
            "received_at",
        }
        assert set(attrs_set.keys()) == expected_keys
        assert attrs_set["request_id"] == str(envelope.request_id)
        assert attrs_set["tenant_id"] == envelope.tenant_id
        assert attrs_set["cluster_id"] == envelope.cluster_id
        assert attrs_set["region"] == envelope.region
        assert attrs_set["pii_class"] == envelope.pii_class
        assert attrs_set["received_at"] == envelope.received_at.isoformat()

    def test_sets_envelope_attributes_even_when_inner_fn_raises(self):
        # Given a wrapper with an envelope and a failing inner function
        async def failing_run():
            raise RuntimeError("downstream failure")

        envelope = factories.make_envelope()
        fake_span = mock.MagicMock()

        # When the wrapper executes and the inner function raises
        with (
            mock.patch.object(metrics, "record_pipeline_node_duration"),
            mock.patch.object(otel_trace, "get_current_span", return_value=fake_span),
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
