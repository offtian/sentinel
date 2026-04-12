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

    @pytest.mark.asyncio
    async def test_passes_snapshot_fields_to_persist(self) -> None:
        # Given an ExecutionTracer with a mock database
        mock_db = mock.AsyncMock()
        et = tracer.ExecutionTracer(db=mock_db)

        # When start_pipeline is called with snapshot fields
        with mock.patch.object(tracer, "pipeline_ops") as mock_ops:
            mock_ops.persist_pipeline_run = mock.AsyncMock(return_value=uuid.uuid4())
            await et.start_pipeline(
                pipeline_type="sre_investigation",
                input_data={"alert_id": "PD-1"},
                input_hash="abc123",
                model_ids_json=["openai/gpt-4.1-mini"],
                mcp_endpoints_json=[],
                skill_activations_json=[],
                prompt_version="538d165abc12:alert_classifier",
                prompt_sha256="deadbeef" * 8,
                prompt_text="You are an SRE assistant.",
            )

        # Then persist_pipeline_run receives all snapshot fields
        call_kwargs = mock_ops.persist_pipeline_run.call_args.kwargs
        assert call_kwargs["input_hash"] == "abc123"
        assert call_kwargs["model_ids_json"] == ["openai/gpt-4.1-mini"]
        assert call_kwargs["prompt_version"] == "538d165abc12:alert_classifier"
        assert call_kwargs["prompt_sha256"] == "deadbeef" * 8
        assert call_kwargs["prompt_text"] == "You are an SRE assistant."

    @pytest.mark.asyncio
    async def test_passes_agent_prompts_json_to_persist(self) -> None:
        # Given an ExecutionTracer with a mock database and multi-agent prompt metadata
        mock_db = mock.AsyncMock()
        et = tracer.ExecutionTracer(db=mock_db)
        agent_prompts = [
            {"agent_name": "alert_classifier", "prompt_version": "v1", "prompt_sha256": "aaa"},
            {"agent_name": "root_cause_analyser", "prompt_version": "v2", "prompt_sha256": "bbb"},
        ]

        # When start_pipeline is called with agent_prompts_json
        with mock.patch.object(tracer, "pipeline_ops") as mock_ops:
            mock_ops.persist_pipeline_run = mock.AsyncMock(return_value=uuid.uuid4())
            await et.start_pipeline(
                pipeline_type="sre_investigation",
                agent_prompts_json=agent_prompts,
            )

        # Then persist_pipeline_run receives agent_prompts_json
        call_kwargs = mock_ops.persist_pipeline_run.call_args.kwargs
        assert call_kwargs["agent_prompts_json"] == agent_prompts


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
    async def test_passes_final_reply_to_complete(self) -> None:
        # Given a started pipeline tracer
        mock_db = mock.AsyncMock()
        et = tracer.ExecutionTracer(db=mock_db)
        et._trace_id = uuid.uuid4()
        et._pipeline_run_id = uuid.uuid4()
        et._pipeline_started_at = datetime(2026, 4, 4, 10, 0, tzinfo=UTC)
        reply_payload = {"alert_id": "PD-1", "root_cause": "OOM"}

        # When complete_pipeline is called with final_reply
        with mock.patch.object(tracer, "pipeline_ops") as mock_ops:
            mock_ops.complete_pipeline_run = mock.AsyncMock()
            await et.complete_pipeline(
                status="completed",
                output_data={"root_cause": "OOM"},
                final_reply=reply_payload,
            )

        # Then complete_pipeline_run receives the final_reply
        call_kwargs = mock_ops.complete_pipeline_run.call_args.kwargs
        assert call_kwargs["final_reply"] == reply_payload

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


class TestRecordAgentResult:
    @pytest.mark.asyncio
    async def test_extracts_usage_from_result(self) -> None:
        # Given an ExecutionTracer and a mock agent result with usage data
        et = tracer.ExecutionTracer(db=None)
        et._trace_id = uuid.uuid4()
        node_id = uuid.uuid4()

        mock_usage = mock.MagicMock()
        mock_usage.request_tokens = 100
        mock_usage.response_tokens = 50
        mock_usage.total_tokens = 150

        mock_result = mock.MagicMock()
        mock_result.usage.return_value = mock_usage
        mock_result.all_messages.return_value = []

        # When record_agent_result is called
        with (
            mock.patch.object(et, "record_agent_call", new_callable=mock.AsyncMock) as mock_record,
            mock.patch.object(tracer, "costing") as mock_costing,
        ):
            mock_costing.estimate_cost_usd.return_value = 0.002
            await et.record_agent_result(
                node_id=node_id,
                agent_name="alert_classifier",
                model_id="openai/gpt-4.1-mini",
                result=mock_result,
                duration_ms=300,
            )

        # Then record_agent_call receives the extracted token_usage dict
        mock_record.assert_awaited_once()
        call_kwargs = mock_record.call_args.kwargs
        assert call_kwargs["token_usage"]["input_tokens"] == 100
        assert call_kwargs["token_usage"]["output_tokens"] == 50
        assert call_kwargs["token_usage"]["total_tokens"] == 150

    @pytest.mark.asyncio
    async def test_includes_cost_usd_in_token_usage(self) -> None:
        # Given an ExecutionTracer and a mock agent result with usage data
        et = tracer.ExecutionTracer(db=None)
        et._trace_id = uuid.uuid4()
        node_id = uuid.uuid4()

        mock_usage = mock.MagicMock()
        mock_usage.request_tokens = 200
        mock_usage.response_tokens = 80
        mock_usage.total_tokens = 280

        mock_result = mock.MagicMock()
        mock_result.usage.return_value = mock_usage
        mock_result.all_messages.return_value = []

        # When record_agent_result is called with a costing helper that returns a value
        with (
            mock.patch.object(et, "record_agent_call", new_callable=mock.AsyncMock) as mock_record,
            mock.patch.object(tracer, "costing") as mock_costing,
        ):
            mock_costing.estimate_cost_usd.return_value = 0.0042
            await et.record_agent_result(
                node_id=node_id,
                agent_name="root_cause_analyser",
                model_id="openai/gpt-4.1",
                result=mock_result,
            )

        # Then cost_usd is included in the token_usage passed to record_agent_call
        call_kwargs = mock_record.call_args.kwargs
        assert call_kwargs["token_usage"]["cost_usd"] == 0.0042
        mock_costing.estimate_cost_usd.assert_called_once_with(
            model_id="openai/gpt-4.1",
            input_tokens=200,
            output_tokens=80,
        )

    @pytest.mark.asyncio
    async def test_graceful_when_usage_returns_none(self) -> None:
        # Given an ExecutionTracer and a mock agent result whose usage() returns None
        et = tracer.ExecutionTracer(db=None)
        et._trace_id = uuid.uuid4()
        node_id = uuid.uuid4()

        mock_result = mock.MagicMock()
        mock_result.usage.return_value = None
        mock_result.all_messages.return_value = []

        # When record_agent_result is called
        with mock.patch.object(
            et, "record_agent_call", new_callable=mock.AsyncMock
        ) as mock_record:
            await et.record_agent_result(
                node_id=node_id,
                agent_name="alert_classifier",
                model_id="openai/gpt-4.1-mini",
                result=mock_result,
            )

        # Then record_agent_call is still called with token_usage=None (no crash)
        mock_record.assert_awaited_once()
        call_kwargs = mock_record.call_args.kwargs
        assert call_kwargs["token_usage"] is None


class TestRecordAgentResultAccumulation:
    @pytest.mark.asyncio
    async def test_accumulates_cost_breakdowns(self) -> None:
        # Given an ExecutionTracer without a database and two different agent results
        et = tracer.ExecutionTracer(db=None)
        node_id = uuid.uuid4()

        classifier_usage = mock.MagicMock()
        classifier_usage.request_tokens = 100
        classifier_usage.response_tokens = 50
        classifier_usage.total_tokens = 150

        analyser_usage = mock.MagicMock()
        analyser_usage.request_tokens = 200
        analyser_usage.response_tokens = 80
        analyser_usage.total_tokens = 280

        classifier_result = mock.MagicMock()
        classifier_result.usage.return_value = classifier_usage
        classifier_result.all_messages.return_value = []

        analyser_result = mock.MagicMock()
        analyser_result.usage.return_value = analyser_usage
        analyser_result.all_messages.return_value = []

        # When record_agent_result is called twice with different agents
        with mock.patch.object(tracer, "costing") as mock_costing:
            mock_costing.estimate_cost_usd.side_effect = [0.001, 0.004]
            await et.record_agent_result(
                node_id=node_id,
                agent_name="alert_classifier",
                model_id="openai/gpt-4.1-mini",
                result=classifier_result,
            )
            await et.record_agent_result(
                node_id=node_id,
                agent_name="root_cause_analyser",
                model_id="openai/gpt-4.1",
                result=analyser_result,
            )

        # Then _agent_cost_breakdowns has 2 entries with correct agent names, model IDs, and token counts
        assert len(et._agent_cost_breakdowns) == 2

        classifier_breakdown = et._agent_cost_breakdowns[0]
        assert classifier_breakdown["agent_name"] == "alert_classifier"
        assert classifier_breakdown["model_id"] == "openai/gpt-4.1-mini"
        assert classifier_breakdown["input_tokens"] == 100
        assert classifier_breakdown["output_tokens"] == 50
        assert classifier_breakdown["total_tokens"] == 150

        analyser_breakdown = et._agent_cost_breakdowns[1]
        assert analyser_breakdown["agent_name"] == "root_cause_analyser"
        assert analyser_breakdown["model_id"] == "openai/gpt-4.1"
        assert analyser_breakdown["input_tokens"] == 200
        assert analyser_breakdown["output_tokens"] == 80
        assert analyser_breakdown["total_tokens"] == 280


class TestCompletePipelineTokenAggregation:
    @pytest.mark.asyncio
    async def test_flushes_aggregate_token_usage_on_complete(self) -> None:
        # Given a tracer with two recorded agent results
        mock_db = mock.MagicMock()
        et = tracer.ExecutionTracer(db=mock_db)
        et._pipeline_run_id = uuid.uuid4()
        et._pipeline_started_at = datetime(2026, 4, 4, 10, 0, tzinfo=UTC)
        node_id = uuid.uuid4()

        classifier_usage = mock.MagicMock()
        classifier_usage.request_tokens = 100
        classifier_usage.response_tokens = 50
        classifier_usage.total_tokens = 150

        analyser_usage = mock.MagicMock()
        analyser_usage.request_tokens = 200
        analyser_usage.response_tokens = 80
        analyser_usage.total_tokens = 280

        classifier_result = mock.MagicMock()
        classifier_result.usage.return_value = classifier_usage
        classifier_result.all_messages.return_value = []

        analyser_result = mock.MagicMock()
        analyser_result.usage.return_value = analyser_usage
        analyser_result.all_messages.return_value = []

        with mock.patch.object(tracer, "costing") as mock_costing:
            mock_costing.estimate_cost_usd.side_effect = [0.001, 0.004]
            await et.record_agent_result(
                node_id=node_id,
                agent_name="alert_classifier",
                model_id="openai/gpt-4.1-mini",
                result=classifier_result,
            )
            await et.record_agent_result(
                node_id=node_id,
                agent_name="root_cause_analyser",
                model_id="openai/gpt-4.1",
                result=analyser_result,
            )

        # When complete_pipeline is called
        with mock.patch.object(tracer, "pipeline_ops") as mock_ops:
            mock_ops.complete_pipeline_run = mock.AsyncMock()
            await et.complete_pipeline(status="completed")

        # Then complete_pipeline_run was called with total_token_usage_json containing correct aggregates
        mock_ops.complete_pipeline_run.assert_awaited_once()
        call_kwargs = mock_ops.complete_pipeline_run.call_args.kwargs
        token_usage = call_kwargs["total_token_usage_json"]

        assert token_usage is not None
        assert token_usage["total_input_tokens"] == 300
        assert token_usage["total_output_tokens"] == 130
        assert token_usage["total_tokens"] == 430
        assert token_usage["total_cost_usd"] == pytest.approx(0.005)
        assert len(token_usage["agent_breakdowns"]) == 2

    @pytest.mark.asyncio
    async def test_no_token_usage_when_no_agent_calls(self) -> None:
        # Given a tracer with no recorded agent results
        mock_db = mock.MagicMock()
        et = tracer.ExecutionTracer(db=mock_db)
        et._pipeline_run_id = uuid.uuid4()
        et._pipeline_started_at = datetime(2026, 4, 4, 10, 0, tzinfo=UTC)

        # When complete_pipeline is called without any record_agent_result calls
        with mock.patch.object(tracer, "pipeline_ops") as mock_ops:
            mock_ops.complete_pipeline_run = mock.AsyncMock()
            await et.complete_pipeline(status="completed")

        # Then total_token_usage_json=None was passed
        mock_ops.complete_pipeline_run.assert_awaited_once()
        call_kwargs = mock_ops.complete_pipeline_run.call_args.kwargs
        assert call_kwargs["total_token_usage_json"] is None


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
