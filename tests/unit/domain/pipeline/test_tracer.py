"""Tests for the ExecutionTracer that persists traces to the database."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest import mock

import pytest

from sentinel.domain.pipeline import tracer


class TestStartPipeline:
    @pytest.mark.asyncio
    async def test_sets_trace_id_and_pipeline_run_id(self) -> None:
        # Given an ExecutionTracer with a mock database
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None
        et = tracer.ExecutionTracer(db=mock_db)

        # When start_pipeline is called
        with mock.patch.object(tracer, "pipeline_ops") as mock_ops:
            mock_ops.persist_pipeline_run = mock.AsyncMock(return_value=uuid.uuid4())
            await et.start_pipeline(
                pipeline_type="sre_investigation",
                job_request_id=uuid.uuid4(),
                input_data={"alert_id": "alert-1"},
            )

        # Then trace_id and pipeline_run_id are set
        assert et.trace_id is not None
        assert et.pipeline_run_id is not None

    @pytest.mark.asyncio
    async def test_delegates_to_pipeline_operations(self) -> None:
        # Given an ExecutionTracer with a mock database
        mock_db = mock.AsyncMock()
        et = tracer.ExecutionTracer(db=mock_db)
        job_id = uuid.uuid4()

        # When start_pipeline is called
        with mock.patch.object(tracer, "pipeline_ops") as mock_ops:
            mock_ops.persist_pipeline_run = mock.AsyncMock(return_value=uuid.uuid4())
            await et.start_pipeline(
                pipeline_type="sre_investigation",
                job_request_id=job_id,
            )

        # Then persist_pipeline_run is called with correct kwargs
        mock_ops.persist_pipeline_run.assert_awaited_once()
        call_kwargs = mock_ops.persist_pipeline_run.call_args.kwargs
        assert call_kwargs["pipeline_type"] == "sre_investigation"
        assert call_kwargs["job_request_id"] == job_id

    @pytest.mark.asyncio
    async def test_graceful_degradation_without_db(self) -> None:
        # Given an ExecutionTracer without a database
        et = tracer.ExecutionTracer(db=None)

        # When start_pipeline is called
        await et.start_pipeline(pipeline_type="sre_investigation")

        # Then no error is raised and trace_id is still set
        assert et.trace_id is not None
        assert et.pipeline_run_id is not None


class TestCompletePipeline:
    @pytest.mark.asyncio
    async def test_delegates_to_pipeline_operations(self) -> None:
        # Given a started pipeline tracer
        mock_db = mock.AsyncMock()
        et = tracer.ExecutionTracer(db=mock_db)
        et._trace_id = uuid.uuid4()
        et._pipeline_run_id = uuid.uuid4()
        et._pipeline_started_at = datetime(2026, 4, 4, 10, 0, tzinfo=UTC)

        # When complete_pipeline is called
        with mock.patch.object(tracer, "pipeline_ops") as mock_ops:
            mock_ops.complete_pipeline_run = mock.AsyncMock()
            await et.complete_pipeline(
                status="completed",
                output_data={"root_cause": "leak"},
            )

        # Then complete_pipeline_run is called with correct status
        mock_ops.complete_pipeline_run.assert_awaited_once()
        assert mock_ops.complete_pipeline_run.call_args.kwargs["status"] == "completed"

    @pytest.mark.asyncio
    async def test_noop_when_db_is_none(self) -> None:
        # Given an ExecutionTracer without a database
        et = tracer.ExecutionTracer(db=None)
        et._trace_id = uuid.uuid4()

        # When complete_pipeline is called
        await et.complete_pipeline(status="completed")

        # Then no error is raised (noop)


class TestStartNode:
    @pytest.mark.asyncio
    async def test_returns_node_uuid(self) -> None:
        # Given a started pipeline tracer
        mock_db = mock.AsyncMock()
        et = tracer.ExecutionTracer(db=mock_db)
        et._trace_id = uuid.uuid4()
        et._pipeline_run_id = uuid.uuid4()

        # When start_node is called
        with mock.patch.object(tracer, "pipeline_ops") as mock_ops:
            expected_id = uuid.uuid4()
            mock_ops.persist_node_execution = mock.AsyncMock(return_value=expected_id)
            node_id = await et.start_node(node_name="ClassifyAlert")

        # Then a UUID is returned
        assert node_id == expected_id

    @pytest.mark.asyncio
    async def test_increments_node_order(self) -> None:
        # Given a started pipeline tracer
        mock_db = mock.AsyncMock()
        et = tracer.ExecutionTracer(db=mock_db)
        et._trace_id = uuid.uuid4()
        et._pipeline_run_id = uuid.uuid4()

        # When two nodes are started
        with mock.patch.object(tracer, "pipeline_ops") as mock_ops:
            mock_ops.persist_node_execution = mock.AsyncMock(
                side_effect=[uuid.uuid4(), uuid.uuid4()]
            )
            await et.start_node(node_name="ClassifyAlert")
            await et.start_node(node_name="InvestigateWithHolmes")

        # Then node_order increments
        first_call = mock_ops.persist_node_execution.call_args_list[0].kwargs
        second_call = mock_ops.persist_node_execution.call_args_list[1].kwargs
        assert first_call["node_order"] == 1
        assert second_call["node_order"] == 2


class TestCompleteNode:
    @pytest.mark.asyncio
    async def test_delegates_to_pipeline_operations(self) -> None:
        # Given a started node
        mock_db = mock.AsyncMock()
        et = tracer.ExecutionTracer(db=mock_db)
        et._trace_id = uuid.uuid4()
        et._pipeline_run_id = uuid.uuid4()
        node_id = uuid.uuid4()
        et._node_started_at[node_id] = datetime(2026, 4, 4, 10, 1, tzinfo=UTC)

        # When complete_node is called
        with mock.patch.object(tracer, "pipeline_ops") as mock_ops:
            mock_ops.complete_node_execution = mock.AsyncMock()
            await et.complete_node(
                node_id=node_id,
                status="completed",
                output_data={"severity": "critical"},
            )

        # Then complete_node_execution is called
        mock_ops.complete_node_execution.assert_awaited_once()
        assert mock_ops.complete_node_execution.call_args.kwargs["status"] == "completed"


class TestRecordAgentCall:
    @pytest.mark.asyncio
    async def test_delegates_to_pipeline_operations(self) -> None:
        # Given a started node
        mock_db = mock.AsyncMock()
        et = tracer.ExecutionTracer(db=mock_db)
        et._trace_id = uuid.uuid4()
        node_id = uuid.uuid4()

        # When record_agent_call is called
        with mock.patch.object(tracer, "pipeline_ops") as mock_ops:
            mock_ops.persist_agent_call = mock.AsyncMock(return_value=uuid.uuid4())
            await et.record_agent_call(
                node_id=node_id,
                agent_name="alert_classifier",
                model_id="gpt-4.1-mini",
                messages=[],
                duration_ms=500,
            )

        # Then persist_agent_call is called with correct agent name
        mock_ops.persist_agent_call.assert_awaited_once()
        assert mock_ops.persist_agent_call.call_args.kwargs["agent_name"] == "alert_classifier"


class TestTraceCollectorBackwardCompat:
    def test_has_record_method(self) -> None:
        # Given an ExecutionTracer
        et = tracer.ExecutionTracer(db=None)

        # Then it satisfies the TraceCollector interface
        assert hasattr(et, "record")
        assert callable(et.record)

    def test_record_appends_to_traces_list(self) -> None:
        # Given an ExecutionTracer
        et = tracer.ExecutionTracer(db=None)

        # When record is called (TraceCollector interface)
        et.record(agent_name="test_agent", messages=[])

        # Then traces are accumulated
        assert len(et.traces) == 1
        assert et.traces[0].agent_name == "test_agent"
