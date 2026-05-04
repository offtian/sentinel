"""Tests for IncidentMemoryRecord and IncidentMemoryEmbeddingRecord SQLModel tables."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sentinel.data.sql import incident_memory, incident_memory_embeddings


class TestIncidentMemoryRecordSchema:
    def test_tablename_is_incident_memory(self) -> None:
        # Given the IncidentMemoryRecord model

        # When inspecting __tablename__
        tablename = incident_memory.IncidentMemoryRecord.__tablename__

        # Then it equals "incident_memory"
        assert tablename == "incident_memory"

    def test_memory_id_is_primary_key(self) -> None:
        # Given the IncidentMemoryRecord model

        # When inspecting the memory_id column
        column = incident_memory.IncidentMemoryRecord.__table__.c.memory_id

        # Then it is the primary key
        assert column.primary_key is True

    def test_tenant_id_column_is_not_nullable(self) -> None:
        # Given the SQLAlchemy table for IncidentMemoryRecord

        # When inspecting tenant_id
        column = incident_memory.IncidentMemoryRecord.__table__.c.tenant_id

        # Then it is non-nullable (multi-tenant scoping is mandatory)
        assert column.nullable is False

    def test_cluster_id_column_is_not_nullable(self) -> None:
        # Given the SQLAlchemy table for IncidentMemoryRecord

        # When inspecting cluster_id
        column = incident_memory.IncidentMemoryRecord.__table__.c.cluster_id

        # Then it is non-nullable
        assert column.nullable is False

    def test_alert_signature_column_is_16_chars(self) -> None:
        # Given the SQLAlchemy table for IncidentMemoryRecord

        # When inspecting alert_signature
        column = incident_memory.IncidentMemoryRecord.__table__.c.alert_signature

        # Then it is a 16-char VARCHAR (sha256[:16] convention)
        assert column.type.length == 16

    def test_tenant_cluster_occurred_index_present(self) -> None:
        # Given the table

        # When inspecting indexes
        index_names = {idx.name for idx in incident_memory.IncidentMemoryRecord.__table__.indexes}

        # Then the primary recall index is registered
        assert "ix_incident_memory_tenant_cluster_occurred" in index_names

    def test_signature_index_present(self) -> None:
        # Given the table

        # When inspecting indexes
        index_names = {idx.name for idx in incident_memory.IncidentMemoryRecord.__table__.indexes}

        # Then the exact-match signature index is registered
        assert "ix_incident_memory_signature" in index_names


class TestIncidentMemoryRecordConstruction:
    def test_minimal_construction_succeeds(self) -> None:
        # Given a complete row payload
        memory_id = uuid.uuid4()
        source_investigation_id = uuid.uuid4()

        # When constructing the record
        record = incident_memory.IncidentMemoryRecord(
            memory_id=memory_id,
            tenant_id="tenant-a",
            cluster_id="cluster-k",
            service="api-service",
            alert_signature="0123456789abcdef",
            alert_title="PodCrashLoop",
            alert_description="restarting every 30s",
            root_cause="OOMKilled",
            remediation="raise mem limit",
            confidence_score=0.85,
            source_investigation_id=source_investigation_id,
            occurred_at=datetime(2026, 5, 1, tzinfo=UTC),
        )

        # Then fields round-trip and created_at is tz-aware
        assert record.memory_id == memory_id
        assert record.tenant_id == "tenant-a"
        assert record.alert_signature == "0123456789abcdef"
        assert record.created_at.tzinfo is not None


class TestIncidentMemoryEmbeddingRecordSchema:
    def test_tablename_is_incident_memory_embeddings(self) -> None:
        # Given the IncidentMemoryEmbeddingRecord model

        # When inspecting __tablename__
        tablename = incident_memory_embeddings.IncidentMemoryEmbeddingRecord.__tablename__

        # Then it equals "incident_memory_embeddings"
        assert tablename == "incident_memory_embeddings"

    def test_memory_id_has_foreign_key_with_cascade(self) -> None:
        # Given the SQLAlchemy table

        # When inspecting the memory_id FK
        column = incident_memory_embeddings.IncidentMemoryEmbeddingRecord.__table__.c.memory_id
        foreign_keys = list(column.foreign_keys)

        # Then exactly one FK targets incident_memory.memory_id with ondelete CASCADE
        assert len(foreign_keys) == 1
        target = foreign_keys[0]
        assert target.column.table.name == "incident_memory"
        assert target.column.name == "memory_id"
        assert target.ondelete == "CASCADE"

    def test_dimension_lock_is_1536(self) -> None:
        # Given the module-level dimension constant

        # When reading INCIDENT_MEMORY_EMBEDDING_DIM
        dim = incident_memory_embeddings.INCIDENT_MEMORY_EMBEDDING_DIM

        # Then it matches the runbook embedder dimension lock
        assert dim == 1536

    def test_unique_constraint_on_identity_tuple(self) -> None:
        # Given the SQLAlchemy table

        # When inspecting unique constraints
        constraint_names = {
            c.name
            for c in incident_memory_embeddings.IncidentMemoryEmbeddingRecord.__table__.constraints
            if c.name
        }

        # Then the identity-tuple unique constraint is registered
        assert "uq_incident_memory_embeddings_identity" in constraint_names
