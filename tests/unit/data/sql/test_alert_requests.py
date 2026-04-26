"""Tests for AlertRequestRecord SQLModel table definition (RFC 12.3.1)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import JSONB

from sentinel.data.sql import alert_requests


class TestAlertRequestRecordSchema:
    def test_tablename_is_alert_request(self) -> None:
        # Given the AlertRequestRecord model

        # When inspecting __tablename__
        tablename = alert_requests.AlertRequestRecord.__tablename__

        # Then it equals "alert_request" (singular, matches RFC 12.3.1)
        assert tablename == "alert_request"

    def test_request_id_is_primary_key(self) -> None:
        # Given an AlertRequestRecord model

        # When inspecting the request_id field on the SA table
        sa_column = alert_requests.AlertRequestRecord.__table__.c.request_id

        # Then it is the primary key
        assert sa_column.primary_key is True

    def test_request_id_default_is_a_uuid(self) -> None:
        # Given the AlertRequestRecord model

        # When invoking the default factory for request_id
        fields = alert_requests.AlertRequestRecord.model_fields
        default_factory = fields["request_id"].default_factory
        assert default_factory is not None
        value = default_factory()

        # Then a UUID is produced
        assert isinstance(value, uuid.UUID)

    def test_tenant_id_is_indexed(self) -> None:
        # Given an AlertRequestRecord model

        # When checking the tenant_id field metadata
        field_info = alert_requests.AlertRequestRecord.model_fields["tenant_id"]

        # Then the field is indexed
        assert len(field_info.metadata) > 0
        assert any(getattr(m, "index", None) is True for m in field_info.metadata)

    def test_received_at_default_is_timezone_aware(self) -> None:
        # Given an AlertRequestRecord with a default-factory received_at

        # When the default is invoked
        default_factory = alert_requests.AlertRequestRecord.model_fields[
            "received_at"
        ].default_factory
        assert default_factory is not None
        value = default_factory()

        # Then it's a UTC-aware datetime
        assert isinstance(value, datetime)
        assert value.tzinfo is not None

    def test_received_at_column_is_timestamptz(self) -> None:
        # Given the SQLAlchemy table for AlertRequestRecord

        # When inspecting the received_at column
        column = alert_requests.AlertRequestRecord.__table__.c.received_at

        # Then it is a timezone-aware DateTime, not nullable
        assert column.type.timezone is True
        assert column.nullable is False

    def test_redacted_annotations_uses_jsonb(self) -> None:
        # Given the SQLAlchemy table for AlertRequestRecord

        # When inspecting the redacted_annotations column type
        column = alert_requests.AlertRequestRecord.__table__.c.redacted_annotations

        # Then the column type is JSONB (postgres dialect)
        assert isinstance(column.type, JSONB)

    def test_provider_column_is_not_nullable(self) -> None:
        # Given the SQLAlchemy table for AlertRequestRecord

        # When inspecting the provider column
        column = alert_requests.AlertRequestRecord.__table__.c.provider

        # Then it is non-nullable (Text-shaped, no Postgres ENUM yet)
        assert column.nullable is False

    def test_dedup_status_column_is_not_nullable(self) -> None:
        # Given the SQLAlchemy table for AlertRequestRecord

        # When inspecting the dedup_status column
        column = alert_requests.AlertRequestRecord.__table__.c.dedup_status

        # Then it is non-nullable
        assert column.nullable is False

    def test_alert_id_field_exists(self) -> None:
        # Given the AlertRequestRecord model

        # When checking the alert_id field
        fields = alert_requests.AlertRequestRecord.model_fields

        # Then alert_id exists
        assert "alert_id" in fields

    def test_severity_field_exists(self) -> None:
        # Given the AlertRequestRecord model

        # When checking the severity field
        fields = alert_requests.AlertRequestRecord.model_fields

        # Then severity exists
        assert "severity" in fields


class TestAlertRequestRecordIndexes:
    def test_composite_tenant_received_index_present(self) -> None:
        # Given the SQLAlchemy table for AlertRequestRecord

        # When inspecting indexes
        indexes = alert_requests.AlertRequestRecord.__table__.indexes
        index_names = {idx.name for idx in indexes}

        # Then the composite (tenant_id, received_at desc) index is registered
        assert "ix_alert_request_tenant_received" in index_names

    def test_composite_provider_alert_id_index_present(self) -> None:
        # Given the SQLAlchemy table for AlertRequestRecord

        # When inspecting indexes
        indexes = alert_requests.AlertRequestRecord.__table__.indexes
        index_names = {idx.name for idx in indexes}

        # Then the composite (provider, alert_id) dedup-lookup index is registered
        assert "ix_alert_request_provider_alert_id" in index_names

    def test_composite_tenant_received_starts_with_tenant(self) -> None:
        # Given the SQLAlchemy table for AlertRequestRecord
        indexes = {idx.name: idx for idx in alert_requests.AlertRequestRecord.__table__.indexes}

        # When inspecting the composite tenant_received index
        idx = indexes["ix_alert_request_tenant_received"]
        leading_column = next(iter(idx.expressions))

        # Then tenant_id is the leading expression (B-tree prefix matters)
        assert getattr(leading_column, "name", None) == "tenant_id"

    def test_composite_provider_alert_id_columns(self) -> None:
        # Given the SQLAlchemy table for AlertRequestRecord
        indexes = {idx.name: idx for idx in alert_requests.AlertRequestRecord.__table__.indexes}

        # When inspecting the dedup-lookup composite index
        idx = indexes["ix_alert_request_provider_alert_id"]
        column_names = [c.name for c in idx.columns]

        # Then it covers provider and alert_id (in that order)
        assert column_names == ["provider", "alert_id"]


class TestAlertRequestRecordConstruction:
    def test_minimal_construction_succeeds(self) -> None:
        # Given a minimum viable set of inputs

        # When constructing an AlertRequestRecord
        record = alert_requests.AlertRequestRecord(
            tenant_id="pm-a",
            provider="pagerduty",
            alert_id="P12345",
            severity="critical",
            dedup_status="new",
        )

        # Then defaults populate request_id and received_at
        assert isinstance(record.request_id, uuid.UUID)
        assert record.received_at.tzinfo is not None
        assert record.redacted_annotations is None

    def test_redacted_annotations_accepts_dict(self) -> None:
        # Given a JSON-shaped annotations dict
        annotations = {"team": "platform", "env": "prod"}

        # When constructing with redacted_annotations
        record = alert_requests.AlertRequestRecord(
            tenant_id="pm-b",
            provider="datadog",
            alert_id="DD-9",
            severity="warning",
            dedup_status="duplicate",
            redacted_annotations=annotations,
        )

        # Then the dict round-trips
        assert record.redacted_annotations == annotations
