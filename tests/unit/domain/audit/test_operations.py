"""
Unit tests for audit log write operations.
"""

from __future__ import annotations

import json
import uuid
from unittest import mock

import pytest

from sentinel.domain.audit import operations


class TestRecordAuditEntry:
    @pytest.mark.asyncio
    async def test_inserts_row_and_returns_uuid(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When an audit entry is recorded with required fields
        result_id = await operations.record_audit_entry(
            db=mock_db,
            actor="system",
            action="investigate",
            resource_type="alert",
            resource_id="PD-12345",
            details={"key": "value"},
            input_hash="abc123",
        )

        # Then a UUID is returned
        assert isinstance(result_id, uuid.UUID)

    @pytest.mark.asyncio
    async def test_calls_db_execute_once(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When an audit entry is recorded
        await operations.record_audit_entry(
            db=mock_db,
            actor="user-42",
            action="approve",
            resource_type="investigation",
            resource_id="inv-99",
            details={"reason": "looks good"},
            input_hash="deadbeef",
        )

        # Then execute is called exactly once
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_receives_core_insert(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When an audit entry is recorded
        await operations.record_audit_entry(
            db=mock_db,
            actor="system",
            action="reject",
            resource_type="alert",
            resource_id="DD-1",
            details={},
            input_hash="cafebabe",
        )

        # Then the query is a SQLAlchemy Core insert targeting the correct table
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        compiled_sql = str(query)
        assert "audit_log" in compiled_sql

    @pytest.mark.asyncio
    async def test_details_dict_is_serialized_to_json_before_insert(self) -> None:
        # Given a mock database connection and a details dict
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None
        details_payload = {"alert_id": "PD-1", "severity": "critical", "count": 3}

        # When an audit entry is recorded with a details dict
        await operations.record_audit_entry(
            db=mock_db,
            actor="system",
            action="investigate",
            resource_type="alert",
            resource_id="PD-1",
            details=details_payload,
            input_hash="hash123",
        )

        # Then execute is called with the details serialized
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        compiled = query.compile(compile_kwargs={"literal_binds": False})
        details_json_value = compiled.params.get("details_json")
        assert isinstance(details_json_value, str)
        assert json.loads(details_json_value) == details_payload

    @pytest.mark.asyncio
    async def test_model_id_defaults_to_empty_string(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When an audit entry is recorded without specifying model_id
        await operations.record_audit_entry(
            db=mock_db,
            actor="system",
            action="classify",
            resource_type="alert",
            resource_id="PD-2",
            details={},
            input_hash="hash456",
        )

        # Then model_id defaults to empty string in the insert
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        compiled = query.compile(compile_kwargs={"literal_binds": False})
        assert compiled.params.get("model_id") == ""

    @pytest.mark.asyncio
    async def test_prompt_version_defaults_to_empty_string(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When an audit entry is recorded without specifying prompt_version
        await operations.record_audit_entry(
            db=mock_db,
            actor="system",
            action="classify",
            resource_type="alert",
            resource_id="PD-3",
            details={},
            input_hash="hash789",
        )

        # Then prompt_version defaults to empty string in the insert
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        compiled = query.compile(compile_kwargs={"literal_binds": False})
        assert compiled.params.get("prompt_version") == ""

    @pytest.mark.asyncio
    async def test_model_id_and_prompt_version_are_passed_when_provided(self) -> None:
        # Given a mock database connection and explicit model metadata
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When an audit entry is recorded with model_id and prompt_version
        await operations.record_audit_entry(
            db=mock_db,
            actor="pipeline",
            action="root_cause_analysis",
            resource_type="investigation",
            resource_id="inv-7",
            details={"confidence": 0.9},
            input_hash="feedface",
            model_id="openai/gpt-4.1",
            prompt_version="v2.3",
        )

        # Then both values are in the insert params
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        compiled = query.compile(compile_kwargs={"literal_binds": False})
        assert compiled.params.get("model_id") == "openai/gpt-4.1"
        assert compiled.params.get("prompt_version") == "v2.3"

    @pytest.mark.asyncio
    async def test_input_hash_is_passed_through(self) -> None:
        # Given a mock database connection and a specific input hash
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None
        expected_hash = "a" * 64

        # When an audit entry is recorded with that hash
        await operations.record_audit_entry(
            db=mock_db,
            actor="system",
            action="investigate",
            resource_type="alert",
            resource_id="PD-99",
            details={},
            input_hash=expected_hash,
        )

        # Then the input_hash is in the insert params
        call_args = mock_db.execute.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("query")
        compiled = query.compile(compile_kwargs={"literal_binds": False})
        assert compiled.params.get("input_hash") == expected_hash
