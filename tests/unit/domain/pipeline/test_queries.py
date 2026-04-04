"""
Unit tests for pipeline tracing read operations.
"""

from __future__ import annotations

import uuid
from unittest import mock

import pytest

from sentinel.domain.pipeline import queries


class TestFetchPipelineRun:
    @pytest.mark.asyncio
    async def test_returns_dict_when_row_exists(self) -> None:
        # Given a mock database that returns one row
        mock_db = mock.AsyncMock()
        trace_id = uuid.uuid4()
        mock_row = mock.MagicMock()
        mock_row._mapping = {
            "id": uuid.uuid4(),
            "trace_id": trace_id,
            "pipeline_type": "sre_investigation",
            "status": "running",
        }
        mock_db.fetch_one.return_value = mock_row

        # When fetching the pipeline run by trace_id
        result = await queries.fetch_pipeline_run(
            db=mock_db,
            trace_id=trace_id,
        )

        # Then the row is returned as a dict
        assert result is not None
        assert result["pipeline_type"] == "sre_investigation"

    @pytest.mark.asyncio
    async def test_returns_none_when_row_absent(self) -> None:
        # Given a mock database that returns no row
        mock_db = mock.AsyncMock()
        mock_db.fetch_one.return_value = None

        # When fetching a non-existent pipeline run
        result = await queries.fetch_pipeline_run(
            db=mock_db,
            trace_id=uuid.uuid4(),
        )

        # Then None is returned
        assert result is None

    @pytest.mark.asyncio
    async def test_calls_fetch_one_once(self) -> None:
        # Given a mock database
        mock_db = mock.AsyncMock()
        mock_db.fetch_one.return_value = None

        # When fetching a pipeline run by trace_id
        await queries.fetch_pipeline_run(
            db=mock_db,
            trace_id=uuid.uuid4(),
        )

        # Then fetch_one is called exactly once
        mock_db.fetch_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_result_contains_all_mapped_fields(self) -> None:
        # Given a mock database that returns a fully-populated row
        mock_db = mock.AsyncMock()
        run_id = uuid.uuid4()
        trace_id = uuid.uuid4()
        mock_row = mock.MagicMock()
        mock_row._mapping = {
            "id": run_id,
            "trace_id": trace_id,
            "pipeline_type": "support_review",
            "status": "completed",
            "duration_ms": 3000,
        }
        mock_db.fetch_one.return_value = mock_row

        # When fetching the pipeline run
        result = await queries.fetch_pipeline_run(
            db=mock_db,
            trace_id=trace_id,
        )

        # Then all fields from _mapping are present
        assert result is not None
        assert result["id"] == run_id
        assert result["status"] == "completed"
        assert result["duration_ms"] == 3000


class TestFetchNodeExecutions:
    @pytest.mark.asyncio
    async def test_returns_list_of_dicts_ordered_by_node_order(self) -> None:
        # Given a mock database returning two node execution rows
        mock_db = mock.AsyncMock()
        pipeline_run_id = uuid.uuid4()
        first_row = mock.MagicMock()
        first_row._mapping = {
            "id": uuid.uuid4(),
            "pipeline_run_id": pipeline_run_id,
            "node_name": "ClassifyAlert",
            "node_order": 0,
            "status": "completed",
        }
        second_row = mock.MagicMock()
        second_row._mapping = {
            "id": uuid.uuid4(),
            "pipeline_run_id": pipeline_run_id,
            "node_name": "AnalyseRootCause",
            "node_order": 1,
            "status": "completed",
        }
        mock_db.fetch_all.return_value = [first_row, second_row]

        # When fetching node executions for the pipeline run
        rows = await queries.fetch_node_executions(
            db=mock_db,
            pipeline_run_id=pipeline_run_id,
        )

        # Then both rows are returned as dicts
        assert len(rows) == 2
        assert rows[0]["node_name"] == "ClassifyAlert"
        assert rows[1]["node_name"] == "AnalyseRootCause"

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_records(self) -> None:
        # Given a mock database that returns no rows
        mock_db = mock.AsyncMock()
        mock_db.fetch_all.return_value = []

        # When fetching node executions for a run with no records
        rows = await queries.fetch_node_executions(
            db=mock_db,
            pipeline_run_id=uuid.uuid4(),
        )

        # Then an empty list is returned
        assert rows == []

    @pytest.mark.asyncio
    async def test_calls_fetch_all_once(self) -> None:
        # Given a mock database
        mock_db = mock.AsyncMock()
        mock_db.fetch_all.return_value = []

        # When fetching node executions for a pipeline run
        await queries.fetch_node_executions(
            db=mock_db,
            pipeline_run_id=uuid.uuid4(),
        )

        # Then fetch_all is called exactly once
        mock_db.fetch_all.assert_called_once()


class TestFetchAgentCalls:
    @pytest.mark.asyncio
    async def test_returns_list_of_dicts_for_node_execution(self) -> None:
        # Given a mock database returning two agent call rows
        mock_db = mock.AsyncMock()
        node_execution_id = uuid.uuid4()
        first_call_row = mock.MagicMock()
        first_call_row._mapping = {
            "id": uuid.uuid4(),
            "node_execution_id": node_execution_id,
            "agent_name": "AlertClassifier",
            "model_id": "openai/gpt-4.1-mini",
        }
        retry_call_row = mock.MagicMock()
        retry_call_row._mapping = {
            "id": uuid.uuid4(),
            "node_execution_id": node_execution_id,
            "agent_name": "AlertClassifier",
            "model_id": "openai/gpt-4.1",
        }
        mock_db.fetch_all.return_value = [first_call_row, retry_call_row]

        # When fetching agent calls for the node execution
        rows = await queries.fetch_agent_calls(
            db=mock_db,
            node_execution_id=node_execution_id,
        )

        # Then both rows are returned as dicts
        assert len(rows) == 2
        assert rows[0]["agent_name"] == "AlertClassifier"

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_records(self) -> None:
        # Given a mock database that returns no rows
        mock_db = mock.AsyncMock()
        mock_db.fetch_all.return_value = []

        # When fetching agent calls for a node execution with no records
        rows = await queries.fetch_agent_calls(
            db=mock_db,
            node_execution_id=uuid.uuid4(),
        )

        # Then an empty list is returned
        assert rows == []

    @pytest.mark.asyncio
    async def test_calls_fetch_all_once(self) -> None:
        # Given a mock database
        mock_db = mock.AsyncMock()
        mock_db.fetch_all.return_value = []

        # When fetching agent calls for a node execution
        await queries.fetch_agent_calls(
            db=mock_db,
            node_execution_id=uuid.uuid4(),
        )

        # Then fetch_all is called exactly once
        mock_db.fetch_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_result_contains_all_mapped_fields(self) -> None:
        # Given a mock database that returns a fully-populated row
        mock_db = mock.AsyncMock()
        node_execution_id = uuid.uuid4()
        call_id = uuid.uuid4()
        mock_row = mock.MagicMock()
        mock_row._mapping = {
            "id": call_id,
            "node_execution_id": node_execution_id,
            "agent_name": "RootCauseAnalyser",
            "model_id": "openai/gpt-4.1",
            "duration_ms": 2100,
        }
        mock_db.fetch_all.return_value = [mock_row]

        # When fetching agent calls
        rows = await queries.fetch_agent_calls(
            db=mock_db,
            node_execution_id=node_execution_id,
        )

        # Then all fields from _mapping are present in the result
        assert rows[0]["id"] == call_id
        assert rows[0]["duration_ms"] == 2100
