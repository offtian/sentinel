"""
Unit tests for audit log persistence via the databases library.
"""

from __future__ import annotations

import json
import uuid
from unittest import mock

import pytest

from sentinel.data import audit as audit_persistence


class TestRecordAuditEntry:
    @pytest.mark.asyncio
    async def test_inserts_row_and_returns_uuid(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When an audit entry is recorded with required fields
        result_id = await audit_persistence.record_audit_entry(
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
        await audit_persistence.record_audit_entry(
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
    async def test_sql_targets_audit_log_table(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When an audit entry is recorded
        await audit_persistence.record_audit_entry(
            db=mock_db,
            actor="system",
            action="reject",
            resource_type="alert",
            resource_id="DD-1",
            details={},
            input_hash="cafebabe",
        )

        # Then the SQL references the correct table
        call_kwargs = mock_db.execute.call_args.kwargs
        assert "audit_log" in call_kwargs["query"]

    @pytest.mark.asyncio
    async def test_details_dict_is_serialized_to_json_string(self) -> None:
        # Given a mock database connection and a details dict
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None
        details_payload = {"alert_id": "PD-1", "severity": "critical", "count": 3}

        # When an audit entry is recorded with a details dict
        await audit_persistence.record_audit_entry(
            db=mock_db,
            actor="system",
            action="investigate",
            resource_type="alert",
            resource_id="PD-1",
            details=details_payload,
            input_hash="hash123",
        )

        # Then the details_json value is a JSON-serialized string of the dict
        values = mock_db.execute.call_args.kwargs["values"]
        assert isinstance(values["details_json"], str)
        assert json.loads(values["details_json"]) == details_payload

    @pytest.mark.asyncio
    async def test_model_id_defaults_to_empty_string(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When an audit entry is recorded without specifying model_id
        await audit_persistence.record_audit_entry(
            db=mock_db,
            actor="system",
            action="classify",
            resource_type="alert",
            resource_id="PD-2",
            details={},
            input_hash="hash456",
        )

        # Then model_id defaults to empty string
        values = mock_db.execute.call_args.kwargs["values"]
        assert values["model_id"] == ""

    @pytest.mark.asyncio
    async def test_prompt_version_defaults_to_empty_string(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When an audit entry is recorded without specifying prompt_version
        await audit_persistence.record_audit_entry(
            db=mock_db,
            actor="system",
            action="classify",
            resource_type="alert",
            resource_id="PD-3",
            details={},
            input_hash="hash789",
        )

        # Then prompt_version defaults to empty string
        values = mock_db.execute.call_args.kwargs["values"]
        assert values["prompt_version"] == ""

    @pytest.mark.asyncio
    async def test_model_id_and_prompt_version_are_passed_when_provided(self) -> None:
        # Given a mock database connection and explicit model metadata
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When an audit entry is recorded with model_id and prompt_version
        await audit_persistence.record_audit_entry(
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

        # Then both values are present in the INSERT values dict
        values = mock_db.execute.call_args.kwargs["values"]
        assert values["model_id"] == "openai/gpt-4.1"
        assert values["prompt_version"] == "v2.3"

    @pytest.mark.asyncio
    async def test_values_dict_contains_all_required_columns(self) -> None:
        # Given a mock database connection
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None

        # When an audit entry is recorded
        await audit_persistence.record_audit_entry(
            db=mock_db,
            actor="system",
            action="investigate",
            resource_type="alert",
            resource_id="PD-10",
            details={"foo": "bar"},
            input_hash="aabbccdd",
        )

        # Then the values dict contains all expected column keys
        values = mock_db.execute.call_args.kwargs["values"]
        assert "id" in values
        assert "timestamp" in values
        assert "actor" in values
        assert "action" in values
        assert "resource_type" in values
        assert "resource_id" in values
        assert "details_json" in values
        assert "input_hash" in values
        assert "model_id" in values
        assert "prompt_version" in values

    @pytest.mark.asyncio
    async def test_input_hash_is_passed_through(self) -> None:
        # Given a mock database connection and a specific input hash
        mock_db = mock.AsyncMock()
        mock_db.execute.return_value = None
        expected_hash = "a" * 64

        # When an audit entry is recorded with that hash
        await audit_persistence.record_audit_entry(
            db=mock_db,
            actor="system",
            action="investigate",
            resource_type="alert",
            resource_id="PD-99",
            details={},
            input_hash=expected_hash,
        )

        # Then the input_hash value in the INSERT matches the provided hash
        values = mock_db.execute.call_args.kwargs["values"]
        assert values["input_hash"] == expected_hash
