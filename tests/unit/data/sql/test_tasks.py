"""Tests for InvestigationTaskRecord + TaskStatusChangeRecord SQLModel tables (RFC 12.3.7)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import JSONB

from sentinel.data.sql import tasks


class TestInvestigationTaskRecordSchema:
    def test_tablename_is_investigation_task(self) -> None:
        # Given the InvestigationTaskRecord model

        # When inspecting __tablename__
        tablename = tasks.InvestigationTaskRecord.__tablename__

        # Then it equals "investigation_task" (singular, matches RFC 12.3.7)
        assert tablename == "investigation_task"

    def test_task_id_is_primary_key(self) -> None:
        # Given the InvestigationTaskRecord model

        # When inspecting the task_id column
        sa_column = tasks.InvestigationTaskRecord.__table__.c.task_id

        # Then it is the primary key
        assert sa_column.primary_key is True

    def test_task_id_default_is_a_uuid(self) -> None:
        # Given the InvestigationTaskRecord model

        # When invoking the default factory for task_id
        fields = tasks.InvestigationTaskRecord.model_fields
        default_factory = fields["task_id"].default_factory
        assert default_factory is not None
        value = default_factory()

        # Then a UUID is produced
        assert isinstance(value, uuid.UUID)

    def test_investigation_id_has_foreign_key_to_investigation_records(self) -> None:
        # Given the SQLAlchemy table for InvestigationTaskRecord

        # When inspecting the investigation_id column foreign keys
        column = tasks.InvestigationTaskRecord.__table__.c.investigation_id
        foreign_keys = list(column.foreign_keys)

        # Then exactly one FK targets investigation_records.id
        assert len(foreign_keys) == 1
        target = foreign_keys[0]
        assert target.column.table.name == "investigation_records"
        assert target.column.name == "id"

    def test_investigation_id_foreign_key_is_named_explicitly(self) -> None:
        # Given the SQLAlchemy table for InvestigationTaskRecord

        # When inspecting the FK constraint name
        column = tasks.InvestigationTaskRecord.__table__.c.investigation_id
        foreign_keys = list(column.foreign_keys)

        # Then the FK constraint carries the explicit name from the task brief
        assert foreign_keys[0].constraint.name == "fk_investigation_task_investigation"

    def test_task_text_column_is_not_nullable(self) -> None:
        # Given the SQLAlchemy table for InvestigationTaskRecord

        # When inspecting the task_text column
        column = tasks.InvestigationTaskRecord.__table__.c.task_text

        # Then it is non-nullable
        assert column.nullable is False

    def test_created_at_default_is_timezone_aware(self) -> None:
        # Given an InvestigationTaskRecord with a default-factory created_at

        # When the default is invoked
        default_factory = tasks.InvestigationTaskRecord.model_fields["created_at"].default_factory
        assert default_factory is not None
        value = default_factory()

        # Then it's a UTC-aware datetime
        assert isinstance(value, datetime)
        assert value.tzinfo is not None

    def test_created_at_column_is_timestamptz_not_nullable(self) -> None:
        # Given the SQLAlchemy table for InvestigationTaskRecord

        # When inspecting the created_at column
        column = tasks.InvestigationTaskRecord.__table__.c.created_at

        # Then it is a timezone-aware DateTime, not nullable
        assert column.type.timezone is True
        assert column.nullable is False

    def test_completed_at_column_is_timestamptz_nullable(self) -> None:
        # Given the SQLAlchemy table for InvestigationTaskRecord

        # When inspecting the completed_at column
        column = tasks.InvestigationTaskRecord.__table__.c.completed_at

        # Then it is a timezone-aware DateTime, nullable (open tasks have no completion)
        assert column.type.timezone is True
        assert column.nullable is True

    def test_evidence_refs_uses_jsonb_and_is_nullable(self) -> None:
        # Given the SQLAlchemy table for InvestigationTaskRecord

        # When inspecting the evidence_refs column
        column = tasks.InvestigationTaskRecord.__table__.c.evidence_refs

        # Then it is JSONB and nullable for the foundations slice
        assert isinstance(column.type, JSONB)
        assert column.nullable is True


class TestInvestigationTaskRecordConstruction:
    def test_minimal_construction_succeeds(self) -> None:
        # Given a minimum viable set of inputs (investigation_id from a sibling row)
        investigation_id = uuid.uuid4()

        # When constructing an InvestigationTaskRecord
        record = tasks.InvestigationTaskRecord(
            investigation_id=investigation_id,
            task_text="Inspect kube-system pod restarts",
        )

        # Then defaults populate task_id and created_at, and optional fields are None
        assert isinstance(record.task_id, uuid.UUID)
        assert record.created_at.tzinfo is not None
        assert record.investigation_id == investigation_id
        assert record.completed_at is None
        assert record.evidence_refs is None

    def test_evidence_refs_accepts_dict(self) -> None:
        # Given a JSON-shaped evidence dict
        evidence = {"trace_ids": ["abc"], "log_query": "level=error"}

        # When constructing with evidence_refs
        record = tasks.InvestigationTaskRecord(
            investigation_id=uuid.uuid4(),
            task_text="Correlate trace with error logs",
            evidence_refs=evidence,
        )

        # Then the dict round-trips
        assert record.evidence_refs == evidence


class TestTaskStatusChangeRecordSchema:
    def test_tablename_is_task_status_change(self) -> None:
        # Given the TaskStatusChangeRecord model

        # When inspecting __tablename__
        tablename = tasks.TaskStatusChangeRecord.__tablename__

        # Then it equals "task_status_change" (singular, matches RFC 12.3.7)
        assert tablename == "task_status_change"

    def test_id_is_primary_key(self) -> None:
        # Given the TaskStatusChangeRecord model

        # When inspecting the id column
        sa_column = tasks.TaskStatusChangeRecord.__table__.c.id

        # Then it is the primary key
        assert sa_column.primary_key is True

    def test_id_default_is_a_uuid(self) -> None:
        # Given the TaskStatusChangeRecord model

        # When invoking the default factory for id
        fields = tasks.TaskStatusChangeRecord.model_fields
        default_factory = fields["id"].default_factory
        assert default_factory is not None
        value = default_factory()

        # Then a UUID is produced
        assert isinstance(value, uuid.UUID)

    def test_task_id_has_foreign_key_to_investigation_task(self) -> None:
        # Given the SQLAlchemy table for TaskStatusChangeRecord

        # When inspecting the task_id column foreign keys
        column = tasks.TaskStatusChangeRecord.__table__.c.task_id
        foreign_keys = list(column.foreign_keys)

        # Then exactly one FK targets investigation_task.task_id
        assert len(foreign_keys) == 1
        target = foreign_keys[0]
        assert target.column.table.name == "investigation_task"
        assert target.column.name == "task_id"

    def test_task_id_foreign_key_is_named_explicitly(self) -> None:
        # Given the SQLAlchemy table for TaskStatusChangeRecord

        # When inspecting the FK constraint name
        column = tasks.TaskStatusChangeRecord.__table__.c.task_id
        foreign_keys = list(column.foreign_keys)

        # Then the FK constraint carries the explicit name from the task brief
        assert foreign_keys[0].constraint.name == "fk_task_status_change_task"

    def test_from_status_column_is_nullable(self) -> None:
        # Given the SQLAlchemy table for TaskStatusChangeRecord

        # When inspecting the from_status column
        column = tasks.TaskStatusChangeRecord.__table__.c.from_status

        # Then it is nullable (the first transition has no prior status)
        assert column.nullable is True

    def test_to_status_column_is_not_nullable(self) -> None:
        # Given the SQLAlchemy table for TaskStatusChangeRecord

        # When inspecting the to_status column
        column = tasks.TaskStatusChangeRecord.__table__.c.to_status

        # Then it is non-nullable (every status change targets a status)
        assert column.nullable is False

    def test_at_default_is_timezone_aware(self) -> None:
        # Given a TaskStatusChangeRecord with a default-factory at

        # When the default is invoked
        default_factory = tasks.TaskStatusChangeRecord.model_fields["at"].default_factory
        assert default_factory is not None
        value = default_factory()

        # Then it's a UTC-aware datetime
        assert isinstance(value, datetime)
        assert value.tzinfo is not None

    def test_at_column_is_timestamptz_not_nullable(self) -> None:
        # Given the SQLAlchemy table for TaskStatusChangeRecord

        # When inspecting the at column
        column = tasks.TaskStatusChangeRecord.__table__.c.at

        # Then it is a timezone-aware DateTime, not nullable
        assert column.type.timezone is True
        assert column.nullable is False

    def test_reason_column_is_nullable(self) -> None:
        # Given the SQLAlchemy table for TaskStatusChangeRecord

        # When inspecting the reason column
        column = tasks.TaskStatusChangeRecord.__table__.c.reason

        # Then it is nullable (status changes do not always carry a reason)
        assert column.nullable is True


class TestTaskStatusChangeRecordConstruction:
    def test_minimal_construction_succeeds(self) -> None:
        # Given a minimum viable set of inputs (task_id from a sibling row, first transition)
        task_id = uuid.uuid4()

        # When constructing a TaskStatusChangeRecord with no prior status
        record = tasks.TaskStatusChangeRecord(
            task_id=task_id,
            from_status=None,
            to_status="open",
        )

        # Then defaults populate id and at, and optional fields are None
        assert isinstance(record.id, uuid.UUID)
        assert record.at.tzinfo is not None
        assert record.task_id == task_id
        assert record.from_status is None
        assert record.reason is None

    def test_full_transition_records_prior_status_and_reason(self) -> None:
        # Given a transition from open to completed with a reason
        task_id = uuid.uuid4()

        # When constructing a TaskStatusChangeRecord with all fields populated
        record = tasks.TaskStatusChangeRecord(
            task_id=task_id,
            from_status="open",
            to_status="completed",
            reason="evidence collected",
        )

        # Then the prior status and reason round-trip
        assert record.from_status == "open"
        assert record.to_status == "completed"
        assert record.reason == "evidence collected"
