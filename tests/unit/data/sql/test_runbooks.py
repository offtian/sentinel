"""Tests for RunbookMatchRecord SQLModel table definition (RFC 12.3.2)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sentinel.data.sql import runbooks


class TestRunbookMatchRecordSchema:
    def test_tablename_is_runbook_match(self) -> None:
        # Given the RunbookMatchRecord model

        # When inspecting __tablename__
        tablename = runbooks.RunbookMatchRecord.__tablename__

        # Then it equals "runbook_match" (singular, matches RFC 12.3.2)
        assert tablename == "runbook_match"

    def test_match_id_is_primary_key(self) -> None:
        # Given a RunbookMatchRecord model

        # When inspecting the match_id column
        sa_column = runbooks.RunbookMatchRecord.__table__.c.match_id

        # Then it is the primary key
        assert sa_column.primary_key is True

    def test_match_id_default_is_a_uuid(self) -> None:
        # Given the RunbookMatchRecord model

        # When invoking the default factory for match_id
        fields = runbooks.RunbookMatchRecord.model_fields
        default_factory = fields["match_id"].default_factory
        assert default_factory is not None
        value = default_factory()

        # Then a UUID is produced
        assert isinstance(value, uuid.UUID)

    def test_request_id_has_foreign_key_to_alert_request(self) -> None:
        # Given the SQLAlchemy table for RunbookMatchRecord

        # When inspecting the request_id column foreign keys
        column = runbooks.RunbookMatchRecord.__table__.c.request_id
        foreign_keys = list(column.foreign_keys)

        # Then exactly one FK targets alert_request.request_id
        assert len(foreign_keys) == 1
        target = foreign_keys[0]
        assert target.column.table.name == "alert_request"
        assert target.column.name == "request_id"

    def test_request_id_foreign_key_is_named_explicitly(self) -> None:
        # Given the SQLAlchemy table for RunbookMatchRecord

        # When inspecting the FK constraint name
        column = runbooks.RunbookMatchRecord.__table__.c.request_id
        foreign_keys = list(column.foreign_keys)

        # Then the FK constraint carries the explicit name
        assert foreign_keys[0].constraint.name == "fk_runbook_match_alert_request"

    def test_runbook_version_sha_is_32_chars(self) -> None:
        # Given the SQLAlchemy table for RunbookMatchRecord

        # When inspecting the runbook_version_sha column type
        column = runbooks.RunbookMatchRecord.__table__.c.runbook_version_sha

        # Then the length is 32 (matches RFC 12.3.2 truncated content hash)
        assert column.type.length == 32

    def test_match_method_column_is_not_nullable(self) -> None:
        # Given the SQLAlchemy table for RunbookMatchRecord

        # When inspecting the match_method column
        column = runbooks.RunbookMatchRecord.__table__.c.match_method

        # Then it is non-nullable (Text-shaped, no Postgres ENUM yet)
        assert column.nullable is False

    def test_match_confidence_field_exists(self) -> None:
        # Given the RunbookMatchRecord model

        # When checking model fields
        fields = runbooks.RunbookMatchRecord.model_fields

        # Then match_confidence exists
        assert "match_confidence" in fields

    def test_matched_at_default_is_timezone_aware(self) -> None:
        # Given a RunbookMatchRecord with a default-factory matched_at

        # When the default is invoked
        default_factory = runbooks.RunbookMatchRecord.model_fields["matched_at"].default_factory
        assert default_factory is not None
        value = default_factory()

        # Then it's a UTC-aware datetime
        assert isinstance(value, datetime)
        assert value.tzinfo is not None

    def test_matched_at_column_is_timestamptz(self) -> None:
        # Given the SQLAlchemy table for RunbookMatchRecord

        # When inspecting the matched_at column
        column = runbooks.RunbookMatchRecord.__table__.c.matched_at

        # Then it is a timezone-aware DateTime, not nullable
        assert column.type.timezone is True
        assert column.nullable is False


class TestRunbookMatchRecordConstruction:
    def test_minimal_construction_succeeds(self) -> None:
        # Given a minimum viable set of inputs (request_id from a sibling row)
        request_id = uuid.uuid4()

        # When constructing a RunbookMatchRecord
        record = runbooks.RunbookMatchRecord(
            request_id=request_id,
            runbook_id="rb-k8s-crashloop",
            runbook_version_sha="a" * 32,
            match_method="tag",
            match_confidence=0.95,
        )

        # Then defaults populate match_id and matched_at
        assert isinstance(record.match_id, uuid.UUID)
        assert record.matched_at.tzinfo is not None
        assert record.request_id == request_id
        assert record.match_confidence == 0.95
