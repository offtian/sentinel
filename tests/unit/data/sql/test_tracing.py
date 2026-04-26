"""Tests for AgentCallRecord SQLModel RFC tool_call extension fields (RFC 12.3.6)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import JSONB

from sentinel.data.sql import tracing


class TestAgentCallRecordF3Extensions:
    def test_tool_name_field_exists(self) -> None:
        # Given the AgentCallRecord model

        # When checking for the tool_name field
        fields = tracing.AgentCallRecord.model_fields

        # Then tool_name is registered (only set when call wraps a tool invocation)
        assert "tool_name" in fields

    def test_tool_name_column_is_nullable(self) -> None:
        # Given the SQLAlchemy table for AgentCallRecord

        # When inspecting the tool_name column
        column = tracing.AgentCallRecord.__table__.c.tool_name

        # Then it is nullable (existing rows are LLM-call records, not tool calls)
        assert column.nullable is True

    def test_capability_token_field_exists(self) -> None:
        # Given the AgentCallRecord model

        # When checking for the capability_token field
        fields = tracing.AgentCallRecord.model_fields

        # Then capability_token is registered (RFC 12.3.6 capability binding)
        assert "capability_token" in fields

    def test_capability_token_column_is_nullable(self) -> None:
        # Given the SQLAlchemy table for AgentCallRecord

        # When inspecting the capability_token column
        column = tracing.AgentCallRecord.__table__.c.capability_token

        # Then it is nullable (foundations writers fill it in F7)
        assert column.nullable is True

    def test_evidence_object_ids_field_exists(self) -> None:
        # Given the AgentCallRecord model

        # When checking for the evidence_object_ids field
        fields = tracing.AgentCallRecord.model_fields

        # Then evidence_object_ids is registered (object-store keys for evidence)
        assert "evidence_object_ids" in fields

    def test_evidence_object_ids_column_is_jsonb(self) -> None:
        # Given the SQLAlchemy table for AgentCallRecord

        # When inspecting the evidence_object_ids column type
        column = tracing.AgentCallRecord.__table__.c.evidence_object_ids

        # Then the column is JSONB (postgres dialect, list of object-store keys)
        assert isinstance(column.type, JSONB)

    def test_evidence_object_ids_column_is_nullable(self) -> None:
        # Given the SQLAlchemy table for AgentCallRecord

        # When inspecting the evidence_object_ids column nullability
        column = tracing.AgentCallRecord.__table__.c.evidence_object_ids

        # Then it is nullable (rows without evidence carry NULL)
        assert column.nullable is True

    def test_succeeded_field_exists(self) -> None:
        # Given the AgentCallRecord model

        # When checking for the succeeded field
        fields = tracing.AgentCallRecord.model_fields

        # Then succeeded is registered (success/failure signal)
        assert "succeeded" in fields

    def test_succeeded_column_is_nullable(self) -> None:
        # Given the SQLAlchemy table for AgentCallRecord

        # When inspecting the succeeded column nullability
        column = tracing.AgentCallRecord.__table__.c.succeeded

        # Then it is nullable (existing rows have no success signal until backfill)
        assert column.nullable is True

    def test_tenant_id_field_exists(self) -> None:
        # Given the AgentCallRecord model

        # When checking for the tenant_id field
        fields = tracing.AgentCallRecord.model_fields

        # Then tenant_id is registered (envelope tenant)
        assert "tenant_id" in fields

    def test_tenant_id_column_is_nullable(self) -> None:
        # Given the SQLAlchemy table for AgentCallRecord

        # When inspecting the tenant_id column nullability
        column = tracing.AgentCallRecord.__table__.c.tenant_id

        # Then it is nullable (foundations writers populate from active envelope)
        assert column.nullable is True

    def test_tenant_id_is_indexed(self) -> None:
        # Given the SQLAlchemy table for AgentCallRecord

        # When inspecting the tenant_id column index attribute
        column = tracing.AgentCallRecord.__table__.c.tenant_id

        # Then it is indexed (per-tenant tool-call lookups)
        assert column.index is True


class TestAgentCallRecordConstruction:
    def test_minimal_construction_succeeds_without_extension_fields(self) -> None:
        # Given the minimum viable inputs for an agent-call row

        # When constructing an AgentCallRecord without RFC 12.3.6 fields
        record = tracing.AgentCallRecord(
            trace_id=uuid.uuid4(),
            node_execution_id=uuid.uuid4(),
            agent_name="root_cause",
            started_at=datetime.now(tz=UTC),
        )

        # Then the new fields default to None
        assert record.tool_name is None
        assert record.capability_token is None
        assert record.evidence_object_ids is None
        assert record.succeeded is None
        assert record.tenant_id is None

    def test_evidence_object_ids_round_trips_as_list(self) -> None:
        # Given a list of evidence object-store keys

        evidence_keys = ["s3://evidence/abc", "s3://evidence/def"]

        # When constructing an AgentCallRecord with evidence_object_ids
        record = tracing.AgentCallRecord(
            trace_id=uuid.uuid4(),
            node_execution_id=uuid.uuid4(),
            agent_name="kubectl_tool",
            started_at=datetime.now(tz=UTC),
            evidence_object_ids=evidence_keys,
        )

        # Then the list round-trips
        assert record.evidence_object_ids == evidence_keys

    def test_tool_call_fields_round_trip_when_provided(self) -> None:
        # Given a tool-call row populated with capability/tenant/success metadata

        # When constructing an AgentCallRecord with the full tool-call shape
        record = tracing.AgentCallRecord(
            trace_id=uuid.uuid4(),
            node_execution_id=uuid.uuid4(),
            agent_name="kubectl_tool",
            started_at=datetime.now(tz=UTC),
            tool_name="kubectl.get_pods",
            capability_token="cap-token-abc",  # noqa: S106
            succeeded=True,
            tenant_id="pm-a",
        )

        # Then all tool-call fields round-trip
        assert record.tool_name == "kubectl.get_pods"
        assert record.capability_token == "cap-token-abc"  # noqa: S105
        assert record.succeeded is True
        assert record.tenant_id == "pm-a"
