"""Tests for QualityVerdictRecord + ApprovalRecord SQLModel tables (RFC 12.3.8)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sentinel.data.sql import quality


class TestQualityVerdictRecordSchema:
    def test_tablename_is_quality_verdict(self) -> None:
        # Given the QualityVerdictRecord model

        # When inspecting __tablename__
        tablename = quality.QualityVerdictRecord.__tablename__

        # Then it equals "quality_verdict" (singular, matches RFC 12.3.8)
        assert tablename == "quality_verdict"

    def test_verdict_id_is_primary_key(self) -> None:
        # Given the QualityVerdictRecord model

        # When inspecting the verdict_id column
        sa_column = quality.QualityVerdictRecord.__table__.c.verdict_id

        # Then it is the primary key
        assert sa_column.primary_key is True

    def test_verdict_id_default_is_a_uuid(self) -> None:
        # Given the QualityVerdictRecord model

        # When invoking the default factory for verdict_id
        fields = quality.QualityVerdictRecord.model_fields
        default_factory = fields["verdict_id"].default_factory
        assert default_factory is not None
        value = default_factory()

        # Then a UUID is produced
        assert isinstance(value, uuid.UUID)

    def test_investigation_id_has_foreign_key_to_investigation_records(self) -> None:
        # Given the SQLAlchemy table for QualityVerdictRecord

        # When inspecting the investigation_id column foreign keys
        column = quality.QualityVerdictRecord.__table__.c.investigation_id
        foreign_keys = list(column.foreign_keys)

        # Then exactly one FK targets investigation_records.id
        assert len(foreign_keys) == 1
        target = foreign_keys[0]
        assert target.column.table.name == "investigation_records"
        assert target.column.name == "id"

    def test_investigation_id_foreign_key_is_named_explicitly(self) -> None:
        # Given the SQLAlchemy table for QualityVerdictRecord

        # When inspecting the FK constraint name
        column = quality.QualityVerdictRecord.__table__.c.investigation_id
        foreign_keys = list(column.foreign_keys)

        # Then the FK constraint carries the explicit name from the task brief
        assert foreign_keys[0].constraint.name == "fk_quality_verdict_investigation"

    def test_groundedness_pass_column_is_not_nullable(self) -> None:
        # Given the SQLAlchemy table for QualityVerdictRecord

        # When inspecting the groundedness_pass column
        column = quality.QualityVerdictRecord.__table__.c.groundedness_pass

        # Then it is non-nullable (every verdict carries a groundedness outcome)
        assert column.nullable is False

    def test_evidence_ref_count_column_is_not_nullable(self) -> None:
        # Given the SQLAlchemy table for QualityVerdictRecord

        # When inspecting the evidence_ref_count column
        column = quality.QualityVerdictRecord.__table__.c.evidence_ref_count

        # Then it is non-nullable
        assert column.nullable is False

    def test_confidence_score_column_is_not_nullable(self) -> None:
        # Given the SQLAlchemy table for QualityVerdictRecord

        # When inspecting the confidence_score column
        column = quality.QualityVerdictRecord.__table__.c.confidence_score

        # Then it is non-nullable
        assert column.nullable is False

    def test_verdict_reason_column_is_not_nullable(self) -> None:
        # Given the SQLAlchemy table for QualityVerdictRecord

        # When inspecting the verdict_reason column
        column = quality.QualityVerdictRecord.__table__.c.verdict_reason

        # Then it is non-nullable
        assert column.nullable is False

    def test_assessed_at_default_is_timezone_aware(self) -> None:
        # Given a QualityVerdictRecord with a default-factory assessed_at

        # When the default is invoked
        default_factory = quality.QualityVerdictRecord.model_fields["assessed_at"].default_factory
        assert default_factory is not None
        value = default_factory()

        # Then it's a UTC-aware datetime
        assert isinstance(value, datetime)
        assert value.tzinfo is not None

    def test_assessed_at_column_is_timestamptz_not_nullable(self) -> None:
        # Given the SQLAlchemy table for QualityVerdictRecord

        # When inspecting the assessed_at column
        column = quality.QualityVerdictRecord.__table__.c.assessed_at

        # Then it is a timezone-aware DateTime, not nullable
        assert column.type.timezone is True
        assert column.nullable is False


class TestQualityVerdictRecordConstruction:
    def test_minimal_construction_succeeds(self) -> None:
        # Given a minimum viable set of inputs (investigation_id from a sibling row)
        investigation_id = uuid.uuid4()

        # When constructing a QualityVerdictRecord
        record = quality.QualityVerdictRecord(
            investigation_id=investigation_id,
            groundedness_pass=True,
            evidence_ref_count=3,
            confidence_score=0.82,
            verdict_reason="all claims grounded in trace evidence",
        )

        # Then defaults populate verdict_id and assessed_at, and inputs round-trip
        assert isinstance(record.verdict_id, uuid.UUID)
        assert record.assessed_at.tzinfo is not None
        assert record.investigation_id == investigation_id
        assert record.groundedness_pass is True
        assert record.evidence_ref_count == 3
        assert record.confidence_score == 0.82
        assert record.verdict_reason == "all claims grounded in trace evidence"


class TestApprovalRecordSchema:
    def test_tablename_is_approval_record(self) -> None:
        # Given the ApprovalRecord model

        # When inspecting __tablename__
        tablename = quality.ApprovalRecord.__tablename__

        # Then it equals "approval_record" (singular, matches RFC 12.3.8)
        assert tablename == "approval_record"

    def test_id_is_primary_key(self) -> None:
        # Given the ApprovalRecord model

        # When inspecting the id column
        sa_column = quality.ApprovalRecord.__table__.c.id

        # Then it is the primary key
        assert sa_column.primary_key is True

    def test_id_default_is_a_uuid(self) -> None:
        # Given the ApprovalRecord model

        # When invoking the default factory for id
        fields = quality.ApprovalRecord.model_fields
        default_factory = fields["id"].default_factory
        assert default_factory is not None
        value = default_factory()

        # Then a UUID is produced
        assert isinstance(value, uuid.UUID)

    def test_verdict_id_has_foreign_key_to_quality_verdict(self) -> None:
        # Given the SQLAlchemy table for ApprovalRecord

        # When inspecting the verdict_id column foreign keys
        column = quality.ApprovalRecord.__table__.c.verdict_id
        foreign_keys = list(column.foreign_keys)

        # Then exactly one FK targets quality_verdict.verdict_id
        assert len(foreign_keys) == 1
        target = foreign_keys[0]
        assert target.column.table.name == "quality_verdict"
        assert target.column.name == "verdict_id"

    def test_verdict_id_foreign_key_is_named_explicitly(self) -> None:
        # Given the SQLAlchemy table for ApprovalRecord

        # When inspecting the FK constraint name
        column = quality.ApprovalRecord.__table__.c.verdict_id
        foreign_keys = list(column.foreign_keys)

        # Then the FK constraint carries the explicit name from the task brief
        assert foreign_keys[0].constraint.name == "fk_approval_record_verdict"

    def test_approver_column_is_not_nullable(self) -> None:
        # Given the SQLAlchemy table for ApprovalRecord

        # When inspecting the approver column
        column = quality.ApprovalRecord.__table__.c.approver

        # Then it is non-nullable
        assert column.nullable is False

    def test_decision_column_is_not_nullable(self) -> None:
        # Given the SQLAlchemy table for ApprovalRecord

        # When inspecting the decision column
        column = quality.ApprovalRecord.__table__.c.decision

        # Then it is non-nullable (kept as plain Text — no Postgres ENUM at F3)
        assert column.nullable is False

    def test_decided_at_default_is_timezone_aware(self) -> None:
        # Given an ApprovalRecord with a default-factory decided_at

        # When the default is invoked
        default_factory = quality.ApprovalRecord.model_fields["decided_at"].default_factory
        assert default_factory is not None
        value = default_factory()

        # Then it's a UTC-aware datetime
        assert isinstance(value, datetime)
        assert value.tzinfo is not None

    def test_decided_at_column_is_timestamptz_not_nullable(self) -> None:
        # Given the SQLAlchemy table for ApprovalRecord

        # When inspecting the decided_at column
        column = quality.ApprovalRecord.__table__.c.decided_at

        # Then it is a timezone-aware DateTime, not nullable
        assert column.type.timezone is True
        assert column.nullable is False


class TestApprovalRecordConstruction:
    def test_minimal_construction_succeeds(self) -> None:
        # Given a minimum viable set of inputs (verdict_id from a sibling row)
        verdict_id = uuid.uuid4()

        # When constructing an ApprovalRecord
        record = quality.ApprovalRecord(
            verdict_id=verdict_id,
            approver="alice@example.com",
            decision="approved",
        )

        # Then defaults populate id and decided_at, and inputs round-trip
        assert isinstance(record.id, uuid.UUID)
        assert record.decided_at.tzinfo is not None
        assert record.verdict_id == verdict_id
        assert record.approver == "alice@example.com"
        assert record.decision == "approved"
