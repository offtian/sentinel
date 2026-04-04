"""
Unit tests for pipeline tracing write operations.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest import mock

import pytest

from sentinel.domain.pipeline import operations


class TestPersistPipelineRun:
    @pytest.mark.asyncio
    async def test_inserts_row_and_returns_uuid(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None
        started_at = datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC)

        # When a pipeline run is persisted with required fields
        result_id = await operations.persist_pipeline_run(
            db=mock_db,
            trace_id=uuid.uuid4(),
            pipeline_type="sre_investigation",
            started_at=started_at,
        )

        # Then a UUID is returned
        assert isinstance(result_id, uuid.UUID)

    @pytest.mark.asyncio
    async def test_calls_db_execute_once(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When a pipeline run is persisted
        await operations.persist_pipeline_run(
            db=mock_db,
            trace_id=uuid.uuid4(),
            pipeline_type="support_review",
            started_at=datetime(2026, 4, 1, 9, 0, 0, tzinfo=UTC),
        )

        # Then execute is called exactly once
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_default_status_is_running(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When a pipeline run is persisted without an explicit status
        await operations.persist_pipeline_run(
            db=mock_db,
            trace_id=uuid.uuid4(),
            pipeline_type="sre_investigation",
            started_at=datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC),
        )

        # Then the insert is called with status "running"
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        compiled = query.compile(compile_kwargs={"literal_binds": False})
        assert (
            "running" in str(compiled.params.values())
            or compiled.params.get("status") == "running"
        )

    @pytest.mark.asyncio
    async def test_optional_fields_are_passed_through(self) -> None:
        # Given a mock database and all optional fields
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None
        trace_id = uuid.uuid4()
        job_request_id = uuid.uuid4()
        started_at = datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC)

        # When a pipeline run is persisted with all optional fields
        result_id = await operations.persist_pipeline_run(
            db=mock_db,
            trace_id=trace_id,
            pipeline_type="sre_investigation",
            job_request_id=job_request_id,
            started_at=started_at,
            input_json={"alert_id": "PD-1"},
        )

        # Then a UUID is returned and execute is called
        assert isinstance(result_id, uuid.UUID)
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_receives_pipeline_runs_insert(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When a pipeline run is persisted
        await operations.persist_pipeline_run(
            db=mock_db,
            trace_id=uuid.uuid4(),
            pipeline_type="sre_investigation",
            started_at=datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC),
        )

        # Then execute receives a Core insert targeting pipeline_runs
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        assert "pipeline_runs" in str(query)


class TestCompletePipelineRun:
    @pytest.mark.asyncio
    async def test_calls_db_execute_once(self) -> None:
        # Given a mock database connection and an existing run id
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None
        run_id = uuid.uuid4()

        # When the pipeline run is completed
        await operations.complete_pipeline_run(
            db=mock_db,
            run_id=run_id,
            status="completed",
        )

        # Then execute is called exactly once
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_receives_pipeline_runs_update(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When the pipeline run is completed
        await operations.complete_pipeline_run(
            db=mock_db,
            run_id=uuid.uuid4(),
            status="failed",
            error_message="Node raised PipelineNodeFailed",
        )

        # Then execute receives a Core update targeting pipeline_runs
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        assert "pipeline_runs" in str(query)

    @pytest.mark.asyncio
    async def test_optional_fields_are_accepted(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When completed with all optional fields
        await operations.complete_pipeline_run(
            db=mock_db,
            run_id=uuid.uuid4(),
            status="completed",
            output_json={"root_cause": "OOM"},
            error_message=None,
            duration_ms=4200,
        )

        # Then execute is called once with no error
        mock_db.execute.assert_called_once()


class TestPersistNodeExecution:
    @pytest.mark.asyncio
    async def test_inserts_row_and_returns_uuid(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When a node execution is persisted with required fields
        result_id = await operations.persist_node_execution(
            db=mock_db,
            trace_id=uuid.uuid4(),
            pipeline_run_id=uuid.uuid4(),
            node_name="ClassifyAlert",
            node_order=0,
            started_at=datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC),
        )

        # Then a UUID is returned
        assert isinstance(result_id, uuid.UUID)

    @pytest.mark.asyncio
    async def test_calls_db_execute_once(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When a node execution is persisted
        await operations.persist_node_execution(
            db=mock_db,
            trace_id=uuid.uuid4(),
            pipeline_run_id=uuid.uuid4(),
            node_name="AnalyseRootCause",
            node_order=2,
            started_at=datetime(2026, 4, 1, 10, 1, 0, tzinfo=UTC),
        )

        # Then execute is called exactly once
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_default_status_is_running(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When a node execution is persisted without an explicit status
        await operations.persist_node_execution(
            db=mock_db,
            trace_id=uuid.uuid4(),
            pipeline_run_id=uuid.uuid4(),
            node_name="DetermineConfidence",
            node_order=3,
            started_at=datetime(2026, 4, 1, 10, 2, 0, tzinfo=UTC),
        )

        # Then the insert is called with status "running"
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        compiled = query.compile(compile_kwargs={"literal_binds": False})
        assert (
            "running" in str(compiled.params.values())
            or compiled.params.get("status") == "running"
        )

    @pytest.mark.asyncio
    async def test_execute_receives_node_executions_insert(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When a node execution is persisted
        await operations.persist_node_execution(
            db=mock_db,
            trace_id=uuid.uuid4(),
            pipeline_run_id=uuid.uuid4(),
            node_name="PublishFindings",
            node_order=4,
            started_at=datetime(2026, 4, 1, 10, 3, 0, tzinfo=UTC),
            input_json={"findings": []},
        )

        # Then execute receives a Core insert targeting node_executions
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        assert "node_executions" in str(query)


class TestCompleteNodeExecution:
    @pytest.mark.asyncio
    async def test_calls_db_execute_once(self) -> None:
        # Given a mock database connection and an existing node execution id
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None
        node_id = uuid.uuid4()

        # When the node execution is completed
        await operations.complete_node_execution(
            db=mock_db,
            node_id=node_id,
            status="completed",
        )

        # Then execute is called exactly once
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_receives_node_executions_update(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When the node execution is completed with a failed status
        await operations.complete_node_execution(
            db=mock_db,
            node_id=uuid.uuid4(),
            status="failed",
            error_message="LLM timeout",
        )

        # Then execute receives a Core update targeting node_executions
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        assert "node_executions" in str(query)

    @pytest.mark.asyncio
    async def test_optional_fields_are_accepted(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When completed with all optional fields
        await operations.complete_node_execution(
            db=mock_db,
            node_id=uuid.uuid4(),
            status="completed",
            output_json={"classification": "latency"},
            error_message=None,
            duration_ms=350,
        )

        # Then execute is called once with no error
        mock_db.execute.assert_called_once()


class TestPersistAgentCall:
    @pytest.mark.asyncio
    async def test_inserts_row_and_returns_uuid(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When an agent call is persisted with required fields
        result_id = await operations.persist_agent_call(
            db=mock_db,
            trace_id=uuid.uuid4(),
            node_execution_id=uuid.uuid4(),
            agent_name="AlertClassifier",
            started_at=datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC),
        )

        # Then a UUID is returned
        assert isinstance(result_id, uuid.UUID)

    @pytest.mark.asyncio
    async def test_calls_db_execute_once(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When an agent call is persisted
        await operations.persist_agent_call(
            db=mock_db,
            trace_id=uuid.uuid4(),
            node_execution_id=uuid.uuid4(),
            agent_name="RootCauseAnalyser",
            started_at=datetime(2026, 4, 1, 10, 1, 0, tzinfo=UTC),
        )

        # Then execute is called exactly once
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_receives_agent_calls_insert(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When an agent call is persisted
        await operations.persist_agent_call(
            db=mock_db,
            trace_id=uuid.uuid4(),
            node_execution_id=uuid.uuid4(),
            agent_name="AlertClassifier",
            started_at=datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC),
        )

        # Then execute receives a Core insert targeting agent_calls
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        assert "agent_calls" in str(query)

    @pytest.mark.asyncio
    async def test_optional_fields_are_passed_through(self) -> None:
        # Given a mock database and all optional fields
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None
        started_at = datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC)
        completed_at = datetime(2026, 4, 1, 10, 0, 2, tzinfo=UTC)

        # When an agent call is persisted with all optional fields
        result_id = await operations.persist_agent_call(
            db=mock_db,
            trace_id=uuid.uuid4(),
            node_execution_id=uuid.uuid4(),
            agent_name="AlertClassifier",
            model_id="openai/gpt-4.1-mini",
            messages_json=[{"role": "user", "content": "classify this alert"}],
            token_usage_json={"prompt_tokens": 100, "completion_tokens": 50},
            duration_ms=1800,
            started_at=started_at,
            completed_at=completed_at,
        )

        # Then a UUID is returned and execute is called
        assert isinstance(result_id, uuid.UUID)
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_default_model_id_is_empty_string(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When an agent call is persisted without a model_id
        await operations.persist_agent_call(
            db=mock_db,
            trace_id=uuid.uuid4(),
            node_execution_id=uuid.uuid4(),
            agent_name="AlertClassifier",
            started_at=datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC),
        )

        # Then the insert is called with model_id as empty string
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        compiled = query.compile(compile_kwargs={"literal_binds": False})
        assert compiled.params.get("model_id") == ""
