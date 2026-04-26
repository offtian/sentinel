"""Tests for AuditLogRecord SQLModel WORM extension fields (RFC 12.3.10)."""

from __future__ import annotations

import uuid

from sqlalchemy.dialects.postgresql import UUID

from sentinel.data.sql import audit


class TestAuditLogRecordWormFields:
    def test_request_id_field_exists(self) -> None:
        # Given the AuditLogRecord model

        # When checking for the request_id field
        fields = audit.AuditLogRecord.model_fields

        # Then request_id is registered (link back to alert_request)
        assert "request_id" in fields

    def test_request_id_is_nullable_for_backfill_safety(self) -> None:
        # Given the SQLAlchemy table for AuditLogRecord

        # When inspecting the request_id column nullability
        column = audit.AuditLogRecord.__table__.c.request_id

        # Then it is nullable (existing rows pre-RFC 12.3.10 carry no request_id)
        assert column.nullable is True

    def test_request_id_uses_postgres_uuid_type(self) -> None:
        # Given the SQLAlchemy table for AuditLogRecord

        # When inspecting the request_id column type
        column = audit.AuditLogRecord.__table__.c.request_id

        # Then the type is the postgres UUID dialect (matches alert_request.request_id)
        assert isinstance(column.type, UUID)

    def test_request_id_is_indexed(self) -> None:
        # Given the SQLAlchemy table for AuditLogRecord

        # When inspecting the request_id column index attribute
        column = audit.AuditLogRecord.__table__.c.request_id

        # Then it is indexed (chain lookup by request_id is the hot path)
        assert column.index is True

    def test_prev_hash_field_exists(self) -> None:
        # Given the AuditLogRecord model

        # When checking for the prev_hash field
        fields = audit.AuditLogRecord.model_fields

        # Then prev_hash is registered
        assert "prev_hash" in fields

    def test_prev_hash_column_is_nullable(self) -> None:
        # Given the SQLAlchemy table for AuditLogRecord

        # When inspecting the prev_hash column

        column = audit.AuditLogRecord.__table__.c.prev_hash

        # Then it is nullable (the first row in a chain has no predecessor)
        assert column.nullable is True

    def test_row_hash_field_exists(self) -> None:
        # Given the AuditLogRecord model

        # When checking for the row_hash field
        fields = audit.AuditLogRecord.model_fields

        # Then row_hash is registered
        assert "row_hash" in fields

    def test_row_hash_column_is_nullable_python_side(self) -> None:
        # Given the SQLAlchemy table for AuditLogRecord

        # When inspecting the row_hash column nullability
        column = audit.AuditLogRecord.__table__.c.row_hash

        # Then it is nullable in the Python schema (the BEFORE INSERT trigger
        # populates it server-side; Python writes the row first)
        assert column.nullable is True


class TestAuditLogRecordConstruction:
    def test_minimal_construction_succeeds_without_worm_fields(self) -> None:
        # Given the minimum viable inputs for an audit row

        # When constructing an AuditLogRecord without request_id/prev_hash/row_hash
        record = audit.AuditLogRecord(
            actor="system",
            action="investigate",
            resource_type="alert",
            resource_id="PD-1",
            details_json="{}",
            input_hash="a" * 64,
        )

        # Then the WORM fields default to None (Python writes; trigger fills row_hash)
        assert record.request_id is None
        assert record.prev_hash is None
        assert record.row_hash is None

    def test_request_id_round_trips_when_provided(self) -> None:
        # Given a request_id that links the audit row to an alert_request envelope
        request_id = uuid.uuid4()

        # When constructing an AuditLogRecord with that request_id
        record = audit.AuditLogRecord(
            actor="system",
            action="classify",
            resource_type="alert",
            resource_id="PD-2",
            details_json="{}",
            input_hash="b" * 64,
            request_id=request_id,
        )

        # Then the request_id round-trips
        assert record.request_id == request_id

    def test_prev_hash_round_trips_when_provided(self) -> None:
        # Given a prior row's row_hash to chain into

        prior_hash = "c" * 64

        # When constructing an AuditLogRecord linking to a predecessor
        record = audit.AuditLogRecord(
            actor="system",
            action="approve",
            resource_type="investigation",
            resource_id="inv-1",
            details_json="{}",
            input_hash="d" * 64,
            prev_hash=prior_hash,
        )

        # Then prev_hash round-trips for the trigger to consume
        assert record.prev_hash == prior_hash
