from __future__ import annotations

import asyncio
import contextlib
from unittest import mock

from sentinel.interfaces.graphs import _node_helpers
from sentinel.utils import metrics


class TestInstrumentedNodeRun:
    def test_records_duration_on_success(self):
        # Given an async function returning a value
        async def fake_run():
            return "result"

        # When wrapped and executed
        with mock.patch.object(metrics, "record_pipeline_node_duration") as recorder:
            wrapped = _node_helpers.instrumented_node_run(
                pipeline="sre",
                node="classify_alert",
                fn=fake_run,
            )
            result = asyncio.run(wrapped())

        # Then the result is returned and the duration is recorded with status=ok
        assert result == "result"
        recorder.assert_called_once()
        kwargs = recorder.call_args.kwargs
        assert kwargs["pipeline"] == "sre"
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
                pipeline="sre",
                node="classify_alert",
                fn=fake_run,
            )
            with contextlib.suppress(ValueError):
                asyncio.run(wrapped())

        # Then duration is recorded with status=error
        recorder.assert_called_once()
        assert recorder.call_args.kwargs["status"] == "error"
