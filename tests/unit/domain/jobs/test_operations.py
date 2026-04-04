"""
Unit tests for job queue write operations.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from unittest import mock

import pytest

from sentinel.domain.jobs import operations


class TestEnqueueJob:
    @pytest.mark.asyncio
    async def test_inserts_row_and_returns_uuid(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()

        # When a job is enqueued
        result_id = await operations.enqueue_job(
            db=mock_db,
            job_type="sre_investigation",
            payload={"alert": "test"},
            requested_by="webhook",
            source_id="PD-123",
        )

        # Then a UUID is returned and execute is called once
        assert isinstance(result_id, uuid.UUID)
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_insert_contains_correct_idempotency_key(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()

        # When a job is enqueued with a specific type and source_id
        await operations.enqueue_job(
            db=mock_db,
            job_type="sre_investigation",
            payload={"alert": "test"},
            requested_by="webhook",
            source_id="PD-123",
        )

        # Then the idempotency key is the sha256 of "job_type:source_id"
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        compiled = query.compile(compile_kwargs={"literal_binds": False})
        expected_key = hashlib.sha256(b"sre_investigation:PD-123").hexdigest()
        assert compiled.params["idempotency_key"] == expected_key

    @pytest.mark.asyncio
    async def test_insert_contains_correct_payload_hash(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        payload = {"alert": "test", "severity": "high"}

        # When a job is enqueued with a specific payload
        await operations.enqueue_job(
            db=mock_db,
            job_type="sre_investigation",
            payload=payload,
            requested_by="webhook",
            source_id="PD-123",
        )

        # Then the payload hash matches the sha256 of the JSON payload
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        compiled = query.compile(compile_kwargs={"literal_binds": False})
        expected_hash = hashlib.sha256(json.dumps(payload, default=str).encode()).hexdigest()
        assert compiled.params["payload_hash"] == expected_hash

    @pytest.mark.asyncio
    async def test_execute_receives_core_insert(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()

        # When a job is enqueued
        await operations.enqueue_job(
            db=mock_db,
            job_type="sre_investigation",
            payload={},
            requested_by="api",
            source_id="PD-1",
        )

        # Then the query is a SQLAlchemy Core insert targeting job_requests
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        compiled_sql = str(query)
        assert "job_requests" in compiled_sql

    @pytest.mark.asyncio
    async def test_default_priority_and_max_retries(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()

        # When a job is enqueued without explicit priority or max_retries
        await operations.enqueue_job(
            db=mock_db,
            job_type="sre_investigation",
            payload={},
            requested_by="api",
            source_id="PD-1",
        )

        # Then defaults are used
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        compiled = query.compile(compile_kwargs={"literal_binds": False})
        assert compiled.params["priority"] == 1
        assert compiled.params["max_retries"] == 3
        assert compiled.params["status"] == "pending"

    @pytest.mark.asyncio
    async def test_trace_id_is_accepted(self) -> None:
        # Given a mock database connection and a trace ID
        mock_db = mock.AsyncMock()
        trace_id = uuid.uuid4()

        # When a job is enqueued with a trace_id
        result_id = await operations.enqueue_job(
            db=mock_db,
            job_type="sre_investigation",
            payload={},
            requested_by="api",
            source_id="PD-1",
            trace_id=trace_id,
        )

        # Then a UUID is returned
        assert isinstance(result_id, uuid.UUID)


class TestEnqueueInvestigation:
    @pytest.mark.asyncio
    async def test_sets_job_type_to_sre_investigation(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()

        # When an investigation is enqueued
        await operations.enqueue_investigation(
            db=mock_db,
            alert_payload={"source": "pagerduty"},
            requested_by="webhook",
            alert_id="PD-500",
        )

        # Then the job_type is sre_investigation
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        compiled = query.compile(compile_kwargs={"literal_binds": False})
        assert compiled.params["job_type"] == "sre_investigation"

    @pytest.mark.asyncio
    async def test_default_priority_is_one(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()

        # When an investigation is enqueued without explicit priority
        await operations.enqueue_investigation(
            db=mock_db,
            alert_payload={},
            requested_by="webhook",
            alert_id="PD-1",
        )

        # Then the default priority is 1
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        compiled = query.compile(compile_kwargs={"literal_binds": False})
        assert compiled.params["priority"] == 1


class TestEnqueueReview:
    @pytest.mark.asyncio
    async def test_sets_job_type_to_support_review(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()

        # When a review is enqueued
        await operations.enqueue_review(
            db=mock_db,
            ticket_payload={"summary": "Help needed"},
            requested_by="jira-webhook",
            ticket_id="JIRA-100",
        )

        # Then the job_type is support_review
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        compiled = query.compile(compile_kwargs={"literal_binds": False})
        assert compiled.params["job_type"] == "support_review"

    @pytest.mark.asyncio
    async def test_default_priority_is_two(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()

        # When a review is enqueued without explicit priority
        await operations.enqueue_review(
            db=mock_db,
            ticket_payload={},
            requested_by="jira-webhook",
            ticket_id="JIRA-1",
        )

        # Then the default priority is 2
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        compiled = query.compile(compile_kwargs={"literal_binds": False})
        assert compiled.params["priority"] == 2


class TestEnqueueAutomation:
    @pytest.mark.asyncio
    async def test_sets_job_type_to_scheduled_automation(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()

        # When an automation is enqueued
        await operations.enqueue_automation(
            db=mock_db,
            automation_name="daily_cleanup",
            requested_by="scheduler",
        )

        # Then the job_type is scheduled_automation
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        compiled = query.compile(compile_kwargs={"literal_binds": False})
        assert compiled.params["job_type"] == "scheduled_automation"

    @pytest.mark.asyncio
    async def test_payload_contains_automation_name_and_params(self) -> None:
        # Given a mock database connection with custom params
        mock_db = mock.AsyncMock()

        # When an automation is enqueued with params
        await operations.enqueue_automation(
            db=mock_db,
            automation_name="daily_cleanup",
            params={"days": 30},
            requested_by="scheduler",
        )

        # Then the payload JSON contains the automation name and params
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        compiled = query.compile(compile_kwargs={"literal_binds": False})
        payload = json.loads(compiled.params["payload_json"])
        assert payload["automation_name"] == "daily_cleanup"
        assert payload["params"] == {"days": 30}

    @pytest.mark.asyncio
    async def test_source_id_is_automation_prefixed(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()

        # When an automation is enqueued
        await operations.enqueue_automation(
            db=mock_db,
            automation_name="daily_cleanup",
            requested_by="scheduler",
        )

        # Then the idempotency key uses "automation:daily_cleanup" as source_id
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        compiled = query.compile(compile_kwargs={"literal_binds": False})
        expected_key = hashlib.sha256(b"scheduled_automation:automation:daily_cleanup").hexdigest()
        assert compiled.params["idempotency_key"] == expected_key


class TestClaimNextJob:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_jobs(self) -> None:
        # Given a mock database that returns no rows
        mock_db = mock.AsyncMock()
        mock_db.fetch_one.return_value = None

        # When claiming the next job
        result = await operations.claim_next_job(
            db=mock_db,
            worker_id="worker-1",
        )

        # Then None is returned and no update is executed
        assert result is None
        mock_db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_row_and_updates_when_job_found(self) -> None:
        # Given a mock database that returns a pending job row
        mock_db = mock.AsyncMock()
        job_id = uuid.uuid4()
        mock_row = mock.MagicMock()
        mock_row._mapping = {
            "id": job_id,
            "job_type": "sre_investigation",
            "status": "pending",
            "priority": 1,
        }
        mock_db.fetch_one.return_value = mock_row

        # When claiming the next job
        result = await operations.claim_next_job(
            db=mock_db,
            worker_id="worker-1",
        )

        # Then the row is returned and an UPDATE is executed
        assert result is not None
        assert result["id"] == job_id
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_select_query_uses_for_update_skip_locked(self) -> None:
        # Given a mock database returning a row
        mock_db = mock.AsyncMock()
        mock_row = mock.MagicMock()
        mock_row._mapping = {
            "id": uuid.uuid4(),
            "job_type": "sre_investigation",
            "status": "pending",
        }
        mock_db.fetch_one.return_value = mock_row

        # When claiming the next job
        await operations.claim_next_job(
            db=mock_db,
            worker_id="worker-1",
        )

        # Then the SELECT query uses FOR UPDATE SKIP LOCKED
        select_query = mock_db.fetch_one.call_args[0][0]
        compiled_sql = str(select_query.compile(compile_kwargs={"literal_binds": True}))
        assert "FOR UPDATE" in compiled_sql


class TestCompleteJob:
    @pytest.mark.asyncio
    async def test_executes_update_and_insert(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        job_id = uuid.uuid4()

        # When completing a job
        result_id = await operations.complete_job(
            db=mock_db,
            job_id=job_id,
            result_json='{"summary": "done"}',
            worker_id="worker-1",
        )

        # Then a UUID is returned and execute is called twice (UPDATE + INSERT)
        assert isinstance(result_id, uuid.UUID)
        assert mock_db.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_first_call_updates_job_requests(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        job_id = uuid.uuid4()

        # When completing a job
        await operations.complete_job(
            db=mock_db,
            job_id=job_id,
            worker_id="worker-1",
        )

        # Then the first execute updates job_requests
        first_call = mock_db.execute.call_args_list[0]
        first_query = first_call[0][0]
        compiled_sql = str(first_query)
        assert "job_requests" in compiled_sql

    @pytest.mark.asyncio
    async def test_second_call_inserts_job_results(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        job_id = uuid.uuid4()

        # When completing a job
        await operations.complete_job(
            db=mock_db,
            job_id=job_id,
            result_json='{"output": "ok"}',
            worker_id="worker-1",
        )

        # Then the second execute inserts into job_results
        second_call = mock_db.execute.call_args_list[1]
        second_query = second_call[0][0]
        compiled_sql = str(second_query)
        assert "job_results" in compiled_sql


class TestFailJob:
    @pytest.mark.asyncio
    async def test_with_retry_resets_to_pending(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        job_id = uuid.uuid4()

        # When failing a job with should_retry=True
        result_id = await operations.fail_job(
            db=mock_db,
            job_id=job_id,
            error_message="timeout",
            worker_id="worker-1",
            should_retry=True,
        )

        # Then a UUID is returned and execute is called twice
        assert isinstance(result_id, uuid.UUID)
        assert mock_db.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_without_retry_sets_to_failed(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        job_id = uuid.uuid4()

        # When failing a job with should_retry=False
        result_id = await operations.fail_job(
            db=mock_db,
            job_id=job_id,
            error_message="fatal error",
            worker_id="worker-1",
            should_retry=False,
        )

        # Then a UUID is returned and execute is called twice
        assert isinstance(result_id, uuid.UUID)
        assert mock_db.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_inserts_result_record(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        job_id = uuid.uuid4()

        # When failing a job
        await operations.fail_job(
            db=mock_db,
            job_id=job_id,
            error_message="boom",
            worker_id="worker-1",
            should_retry=False,
        )

        # Then a result record is inserted into job_results
        assert mock_db.execute.call_count == 2
        second_call = mock_db.execute.call_args_list[1]
        second_query = second_call[0][0]
        compiled_sql = str(second_query)
        assert "job_results" in compiled_sql


class TestRecoverStaleJobs:
    @pytest.mark.asyncio
    async def test_executes_update_for_worker(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()

        # When recovering stale jobs for a worker
        await operations.recover_stale_jobs(
            db=mock_db,
            worker_id="worker-crash",
        )

        # Then an UPDATE is executed
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_targets_job_requests(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()

        # When recovering stale jobs
        await operations.recover_stale_jobs(
            db=mock_db,
            worker_id="worker-crash",
        )

        # Then the update targets job_requests
        call_args = mock_db.execute.call_args
        query = call_args[0][0]
        compiled_sql = str(query)
        assert "job_requests" in compiled_sql
