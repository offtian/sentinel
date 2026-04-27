"""Tests for RunbookMatchRecord and RunbookFeedbackRecord SQLModel tables (RFC 12.3.2 + F6 §8)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import JSONB

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


class TestRunbookMatchRecordF6Extensions:
    """F6 schema extensions: candidates audit trail + LLM disambiguator outputs (spec §8.1)."""

    def test_runbook_id_is_nullable(self) -> None:
        # Given the SQLAlchemy table for RunbookMatchRecord (F6 makes runbook_id nullable for no-match rows)

        # When inspecting the runbook_id column
        column = runbooks.RunbookMatchRecord.__table__.c.runbook_id

        # Then it permits NULL (no-match rows have no chosen runbook)
        assert column.nullable is True

    def test_runbook_version_sha_is_nullable(self) -> None:
        # Given the SQLAlchemy table for RunbookMatchRecord (F6 makes the legacy version_sha nullable)

        # When inspecting the runbook_version_sha column
        column = runbooks.RunbookMatchRecord.__table__.c.runbook_version_sha

        # Then it permits NULL (no-match rows have no chosen runbook)
        assert column.nullable is True

    def test_runbook_content_sha_is_32_char_nullable(self) -> None:
        # Given the SQLAlchemy table for RunbookMatchRecord

        # When inspecting the new runbook_content_sha column
        column = runbooks.RunbookMatchRecord.__table__.c.runbook_content_sha

        # Then it is a nullable VARCHAR(32) matching the truncated sha256[:32] contract
        assert column.nullable is True
        assert column.type.length == 32

    def test_tag_score_column_is_integer_nullable(self) -> None:
        # Given the SQLAlchemy table for RunbookMatchRecord

        # When inspecting the new tag_score column
        column = runbooks.RunbookMatchRecord.__table__.c.tag_score

        # Then it is a nullable INTEGER (Stage 1 tag overlap count; null on rescue / no-match paths)
        assert column.nullable is True
        assert column.type.python_type is int

    def test_llm_choice_column_is_255_char_nullable(self) -> None:
        # Given the SQLAlchemy table for RunbookMatchRecord

        # When inspecting the new llm_choice column
        column = runbooks.RunbookMatchRecord.__table__.c.llm_choice

        # Then it is a nullable VARCHAR(255) (chosen runbook_id or literal "no_match")
        assert column.nullable is True
        assert column.type.length == 255

    def test_llm_justification_column_is_text_nullable(self) -> None:
        # Given the SQLAlchemy table for RunbookMatchRecord

        # When inspecting the new llm_justification column
        column = runbooks.RunbookMatchRecord.__table__.c.llm_justification

        # Then it is nullable Text (LLM-emitted single-line rationale, ≤200 chars by contract)
        assert column.nullable is True

    def test_candidates_json_uses_jsonb(self) -> None:
        # Given the SQLAlchemy table for RunbookMatchRecord

        # When inspecting the new candidates_json column
        column = runbooks.RunbookMatchRecord.__table__.c.candidates_json

        # Then the column type is JSONB (postgres dialect) and nullable
        assert isinstance(column.type, JSONB)
        assert column.nullable is True

    def test_no_match_row_construction_succeeds(self) -> None:
        # Given an envelope request_id and a no-match outcome (no chosen runbook)
        request_id = uuid.uuid4()
        candidates = [
            {
                "runbook_id": "rb-other",
                "content_sha": "b" * 32,
                "tag_score": 1,
                "matched_via": "exact_tag",
            }
        ]

        # When constructing a no-match RunbookMatchRecord
        record = runbooks.RunbookMatchRecord(
            request_id=request_id,
            runbook_id=None,
            runbook_version_sha=None,
            runbook_content_sha=None,
            tag_score=0,
            llm_choice="no_match",
            llm_justification="No candidate runbook matched the alert tags",
            candidates_json=candidates,
            match_method="no_match",
            match_confidence=0.0,
        )

        # Then the regulator-audit fields round-trip and the row carries the audit candidates
        assert record.runbook_id is None
        assert record.runbook_version_sha is None
        assert record.llm_choice == "no_match"
        assert record.candidates_json == candidates
        assert record.match_method == "no_match"


class TestRunbookFeedbackRecordSchema:
    """Feedback row written by the approval gate (RFC 12.3 + F6 spec §8.2)."""

    def test_tablename_is_runbook_feedback(self) -> None:
        # Given the RunbookFeedbackRecord model

        # When inspecting __tablename__
        tablename = runbooks.RunbookFeedbackRecord.__tablename__

        # Then it equals "runbook_feedback" (singular, matches F6 spec §8.2)
        assert tablename == "runbook_feedback"

    def test_feedback_id_is_primary_key(self) -> None:
        # Given the RunbookFeedbackRecord model

        # When inspecting the feedback_id column
        column = runbooks.RunbookFeedbackRecord.__table__.c.feedback_id

        # Then it is the primary key
        assert column.primary_key is True

    def test_feedback_id_default_is_a_uuid(self) -> None:
        # Given the RunbookFeedbackRecord model

        # When invoking the default factory for feedback_id
        default_factory = runbooks.RunbookFeedbackRecord.model_fields[
            "feedback_id"
        ].default_factory
        assert default_factory is not None
        value = default_factory()

        # Then a UUID is produced
        assert isinstance(value, uuid.UUID)

    def test_request_id_has_foreign_key_to_alert_request(self) -> None:
        # Given the SQLAlchemy table for RunbookFeedbackRecord

        # When inspecting the request_id FK
        column = runbooks.RunbookFeedbackRecord.__table__.c.request_id
        foreign_keys = list(column.foreign_keys)

        # Then exactly one FK targets alert_request.request_id with the canonical name
        assert len(foreign_keys) == 1
        target = foreign_keys[0]
        assert target.column.table.name == "alert_request"
        assert target.column.name == "request_id"
        assert target.constraint.name == "fk_runbook_feedback_alert_request"

    def test_runbook_id_is_255_char_not_null(self) -> None:
        # Given the SQLAlchemy table for RunbookFeedbackRecord

        # When inspecting the runbook_id column
        column = runbooks.RunbookFeedbackRecord.__table__.c.runbook_id

        # Then it is non-nullable VARCHAR(255)
        assert column.nullable is False
        assert column.type.length == 255

    def test_runbook_content_sha_is_32_char_not_null(self) -> None:
        # Given the SQLAlchemy table for RunbookFeedbackRecord

        # When inspecting the runbook_content_sha column
        column = runbooks.RunbookFeedbackRecord.__table__.c.runbook_content_sha

        # Then it is non-nullable VARCHAR(32) (regulator must know which content was the target)
        assert column.nullable is False
        assert column.type.length == 32

    def test_sentiment_column_is_not_nullable(self) -> None:
        # Given the SQLAlchemy table for RunbookFeedbackRecord

        # When inspecting the sentiment column
        column = runbooks.RunbookFeedbackRecord.__table__.c.sentiment

        # Then it is non-nullable VARCHAR(32) (one of positive/negative/wrong_runbook by Literal)
        assert column.nullable is False
        assert column.type.length == 32

    def test_reason_column_is_text_nullable(self) -> None:
        # Given the SQLAlchemy table for RunbookFeedbackRecord

        # When inspecting the reason column
        column = runbooks.RunbookFeedbackRecord.__table__.c.reason

        # Then it is nullable Text (free-form explanation from the human)
        assert column.nullable is True

    def test_submitted_at_is_timestamptz_not_null(self) -> None:
        # Given the SQLAlchemy table for RunbookFeedbackRecord

        # When inspecting the submitted_at column
        column = runbooks.RunbookFeedbackRecord.__table__.c.submitted_at

        # Then it is a timezone-aware DateTime, not nullable
        assert column.type.timezone is True
        assert column.nullable is False

    def test_submitted_at_default_is_timezone_aware(self) -> None:
        # Given the RunbookFeedbackRecord model

        # When invoking the default factory for submitted_at
        default_factory = runbooks.RunbookFeedbackRecord.model_fields[
            "submitted_at"
        ].default_factory
        assert default_factory is not None
        value = default_factory()

        # Then it's a UTC-aware datetime
        assert isinstance(value, datetime)
        assert value.tzinfo is not None

    def test_submitted_by_is_255_char_nullable(self) -> None:
        # Given the SQLAlchemy table for RunbookFeedbackRecord

        # When inspecting the submitted_by column
        column = runbooks.RunbookFeedbackRecord.__table__.c.submitted_by

        # Then it is nullable VARCHAR(255) (system-generated rows have no actor)
        assert column.nullable is True
        assert column.type.length == 255


class TestRunbookFeedbackRecordIndexes:
    def test_runbook_id_index_present(self) -> None:
        # Given the SQLAlchemy table for RunbookFeedbackRecord

        # When inspecting indexes
        index_names = {idx.name for idx in runbooks.RunbookFeedbackRecord.__table__.indexes}

        # Then ix_runbook_feedback_runbook_id is registered (per-runbook digest queries)
        assert "ix_runbook_feedback_runbook_id" in index_names

    def test_request_id_index_present(self) -> None:
        # Given the SQLAlchemy table for RunbookFeedbackRecord

        # When inspecting indexes
        index_names = {idx.name for idx in runbooks.RunbookFeedbackRecord.__table__.indexes}

        # Then ix_runbook_feedback_request_id is registered (per-request lookup from approval gate)
        assert "ix_runbook_feedback_request_id" in index_names


class TestRunbookFeedbackRecordConstruction:
    def test_minimal_construction_succeeds(self) -> None:
        # Given a request_id from a sibling alert_request and a 32-char content sha
        request_id = uuid.uuid4()

        # When constructing a negative-feedback row from the approval gate
        record = runbooks.RunbookFeedbackRecord(
            request_id=request_id,
            runbook_id="rb-k8s-crashloop",
            runbook_content_sha="c" * 32,
            sentiment="negative",
            reason="Wrong remediation suggested",
            submitted_by="ollie.tian",
        )

        # Then defaults populate feedback_id and submitted_at; supplied fields round-trip
        assert isinstance(record.feedback_id, uuid.UUID)
        assert record.submitted_at.tzinfo is not None
        assert record.request_id == request_id
        assert record.sentiment == "negative"
        assert record.submitted_by == "ollie.tian"

    def test_system_submitted_row_omits_submitter(self) -> None:
        # Given a system-generated wrong_runbook event (no human actor)

        # When constructing the row without a submitter
        record = runbooks.RunbookFeedbackRecord(
            request_id=uuid.uuid4(),
            runbook_id="rb-k8s-crashloop",
            runbook_content_sha="d" * 32,
            sentiment="wrong_runbook",
        )

        # Then submitted_by is None and reason defaults to None
        assert record.submitted_by is None
        assert record.reason is None
