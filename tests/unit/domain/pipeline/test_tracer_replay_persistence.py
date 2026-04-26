"""F4.7 ReplayBundle persistence wiring on the ExecutionTracer."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest import mock

import pytest

from sentinel.domain.pipeline import tracer
from sentinel.plugins.toolsets import _runtime as runtime
from sentinel.utils import replay_bundle as bundle_mod
from tests import factories


class _FakeConfig:
    """Stand-in for ``CommonConfiguration`` carrying only the replay flag."""

    def __init__(self, *, enable_replay_bundle: bool) -> None:
        self.enable_replay_bundle = enable_replay_bundle


class TestStartPipelineReplayCaptureBinding:
    @pytest.mark.asyncio
    async def test_binds_builder_when_envelope_and_alert_payload_provided(self) -> None:
        # Given an ExecutionTracer and an envelope + alert payload
        mock_db = mock.AsyncMock()
        et = tracer.ExecutionTracer(db=mock_db)
        envelope = factories.make_envelope()
        alert_payload = {"id": "PD-1", "title": "boom"}

        # When start_pipeline is called with the replay context and replay is enabled
        with (
            mock.patch.object(tracer, "pipeline_ops") as mock_ops,
            mock.patch.object(tracer, "config_mod") as mock_cfg,
        ):
            mock_ops.persist_pipeline_run = mock.AsyncMock(return_value=uuid.uuid4())
            mock_cfg.get_config.return_value = _FakeConfig(enable_replay_bundle=True)
            await et.start_pipeline(
                pipeline_type="investigation",
                envelope=envelope,
                alert_payload=alert_payload,
            )

        # Then a builder is bound to the ContextVar and the tracer remembers the context
        assert runtime.current_replay_builder() is not None
        assert et._replay_token is not None
        assert et._replay_envelope is envelope
        assert et._replay_alert_payload == alert_payload
        # And: cleanup so the ContextVar doesn't leak into other tests
        runtime.unbind_replay_builder(et._replay_token)
        et._replay_token = None

    @pytest.mark.asyncio
    async def test_does_not_bind_when_replay_disabled(self) -> None:
        # Given an ExecutionTracer and a config with replay disabled
        mock_db = mock.AsyncMock()
        et = tracer.ExecutionTracer(db=mock_db)
        envelope = factories.make_envelope()

        # When start_pipeline is called with envelope but replay is disabled
        with (
            mock.patch.object(tracer, "pipeline_ops") as mock_ops,
            mock.patch.object(tracer, "config_mod") as mock_cfg,
        ):
            mock_ops.persist_pipeline_run = mock.AsyncMock(return_value=uuid.uuid4())
            mock_cfg.get_config.return_value = _FakeConfig(enable_replay_bundle=False)
            await et.start_pipeline(
                pipeline_type="investigation",
                envelope=envelope,
                alert_payload={"id": "PD-1"},
            )

        # Then no token is stored and the ContextVar is empty
        assert et._replay_token is None
        assert runtime.current_replay_builder() is None

    @pytest.mark.asyncio
    async def test_does_not_bind_when_envelope_missing(self) -> None:
        # Given an ExecutionTracer and a config with replay enabled
        mock_db = mock.AsyncMock()
        et = tracer.ExecutionTracer(db=mock_db)

        # When start_pipeline is called WITHOUT envelope/alert_payload (back-compat)
        with (
            mock.patch.object(tracer, "pipeline_ops") as mock_ops,
            mock.patch.object(tracer, "config_mod") as mock_cfg,
        ):
            mock_ops.persist_pipeline_run = mock.AsyncMock(return_value=uuid.uuid4())
            mock_cfg.get_config.return_value = _FakeConfig(enable_replay_bundle=True)
            await et.start_pipeline(pipeline_type="investigation")

        # Then no capture is started — back-compat with pre-F4.7 callers
        assert et._replay_token is None
        assert runtime.current_replay_builder() is None


class TestCompletePipelineReplayPersistence:
    @pytest.mark.asyncio
    async def test_writes_bundle_and_sha_to_complete_pipeline_run(self) -> None:
        # Given a tracer that has an open replay capture and one recorded tool call
        mock_db = mock.AsyncMock()
        et = tracer.ExecutionTracer(db=mock_db)
        et._trace_id = uuid.uuid4()
        et._pipeline_run_id = uuid.uuid4()
        et._pipeline_started_at = datetime(2026, 4, 26, 10, 0, tzinfo=UTC)
        envelope = factories.make_envelope()
        alert_payload = {"id": "PD-1", "title": "high cpu"}
        builder = bundle_mod.ReplayBundleBuilder()
        et._replay_token = runtime.bind_replay_builder(builder)
        et._replay_envelope = envelope
        et._replay_alert_payload = alert_payload
        # And: a tool call recorded mid-run
        runtime.record_tool_call(
            tool_name="get_pod_status",
            inputs={"namespace": "prod"},
            outputs={"status": "Running"},
            at=datetime(2026, 4, 26, 10, 0, 1, tzinfo=UTC),
        )

        # When complete_pipeline is called with runbook info and a final reply
        with mock.patch.object(tracer, "pipeline_ops") as mock_ops:
            mock_ops.complete_pipeline_run = mock.AsyncMock()
            await et.complete_pipeline(
                status="completed",
                final_reply={"alert_id": "PD-1", "root_cause": "OOM"},
                runbook_id="runbook-cpu",
                runbook_version_sha="abc123",
            )

        # Then complete_pipeline_run receives a non-empty bundle dict + sha and the
        # bundle's tool_io contains the recorded call
        mock_ops.complete_pipeline_run.assert_awaited_once()
        call_kwargs = mock_ops.complete_pipeline_run.call_args.kwargs
        assert call_kwargs["replay_bundle_sha"] is not None
        assert isinstance(call_kwargs["replay_bundle_json"], dict)
        assert call_kwargs["replay_bundle_json"]["runbook_id"] == "runbook-cpu"
        assert call_kwargs["replay_bundle_json"]["runbook_version_sha"] == "abc123"
        recorded_tools = call_kwargs["replay_bundle_json"]["tool_io"]
        assert len(recorded_tools) == 1
        assert recorded_tools[0]["tool_name"] == "get_pod_status"
        # And: the tracer's slots are reset and the ContextVar is cleared
        assert et._replay_token is None
        assert runtime.current_replay_builder() is None

    @pytest.mark.asyncio
    async def test_writes_null_columns_when_no_capture_was_started(self) -> None:
        # Given a tracer that never opened a replay capture (back-compat path)
        mock_db = mock.AsyncMock()
        et = tracer.ExecutionTracer(db=mock_db)
        et._trace_id = uuid.uuid4()
        et._pipeline_run_id = uuid.uuid4()
        et._pipeline_started_at = datetime(2026, 4, 26, 10, 0, tzinfo=UTC)

        # When complete_pipeline is called
        with mock.patch.object(tracer, "pipeline_ops") as mock_ops:
            mock_ops.complete_pipeline_run = mock.AsyncMock()
            await et.complete_pipeline(
                status="completed",
                final_reply={"alert_id": "PD-1"},
            )

        # Then both replay columns are explicitly None (no row pollution)
        call_kwargs = mock_ops.complete_pipeline_run.call_args.kwargs
        assert call_kwargs["replay_bundle_json"] is None
        assert call_kwargs["replay_bundle_sha"] is None

    @pytest.mark.asyncio
    async def test_releases_context_token_even_when_persist_raises(self) -> None:
        # Given a tracer with an open replay capture
        mock_db = mock.AsyncMock()
        et = tracer.ExecutionTracer(db=mock_db)
        et._trace_id = uuid.uuid4()
        et._pipeline_run_id = uuid.uuid4()
        et._pipeline_started_at = datetime(2026, 4, 26, 10, 0, tzinfo=UTC)
        builder = bundle_mod.ReplayBundleBuilder()
        et._replay_token = runtime.bind_replay_builder(builder)
        et._replay_envelope = factories.make_envelope()
        et._replay_alert_payload = {"id": "PD-1"}

        # When complete_pipeline_run raises during DB persistence
        with mock.patch.object(tracer, "pipeline_ops") as mock_ops:
            mock_ops.complete_pipeline_run = mock.AsyncMock(side_effect=RuntimeError("db down"))
            with pytest.raises(RuntimeError):
                await et.complete_pipeline(
                    status="completed",
                    final_reply={"alert_id": "PD-1"},
                )

        # Then the ContextVar token is still released — the capture window is
        # closed so concurrent tasks don't see a stale builder
        assert et._replay_token is None
        assert runtime.current_replay_builder() is None
