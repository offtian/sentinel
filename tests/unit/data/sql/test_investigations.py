"""Tests for InvestigationRecord SQLModel RFC investigation extension fields (RFC 12.3.4)."""

from __future__ import annotations

import uuid

from sqlalchemy.dialects.postgresql import UUID

# Import sibling modules so their tables are registered with SQLModel.metadata
# before FK targets resolve via column.foreign_keys -> column.table.name.
from sentinel.data.sql import alert_requests, investigations, runbooks  # noqa: F401


class TestInvestigationRecordF3Extensions:
    def test_request_id_field_exists(self) -> None:
        # Given the InvestigationRecord model

        # When checking for the request_id field
        fields = investigations.InvestigationRecord.model_fields

        # Then request_id is registered (envelope link to alert_request)
        assert "request_id" in fields

    def test_request_id_column_is_nullable(self) -> None:
        # Given the SQLAlchemy table for InvestigationRecord

        # When inspecting the request_id column nullability
        column = investigations.InvestigationRecord.__table__.c.request_id

        # Then it is nullable (rows pre-RFC 12.3.4 carry no envelope id)
        assert column.nullable is True

    def test_request_id_uses_postgres_uuid_type(self) -> None:
        # Given the SQLAlchemy table for InvestigationRecord

        # When inspecting the request_id column type
        column = investigations.InvestigationRecord.__table__.c.request_id

        # Then the type is the postgres UUID dialect (matches alert_request.request_id)
        assert isinstance(column.type, UUID)

    def test_request_id_is_indexed(self) -> None:
        # Given the SQLAlchemy table for InvestigationRecord

        # When inspecting the request_id column index attribute
        column = investigations.InvestigationRecord.__table__.c.request_id

        # Then it is indexed (envelope-keyed lookup is hot path)
        assert column.index is True

    def test_request_id_has_foreign_key_to_alert_request(self) -> None:
        # Given the SQLAlchemy table for InvestigationRecord

        # When inspecting the request_id column foreign keys
        column = investigations.InvestigationRecord.__table__.c.request_id
        foreign_keys = list(column.foreign_keys)

        # Then exactly one FK targets alert_request.request_id
        assert len(foreign_keys) == 1
        target = foreign_keys[0]
        assert target.column.table.name == "alert_request"
        assert target.column.name == "request_id"

    def test_request_id_foreign_key_constraint_name(self) -> None:
        # Given the SQLAlchemy table for InvestigationRecord

        # When inspecting the FK constraint name
        column = investigations.InvestigationRecord.__table__.c.request_id
        foreign_keys = list(column.foreign_keys)

        # Then the FK constraint carries the explicit name from the migration
        assert foreign_keys[0].constraint.name == "fk_investigation_alert_request"

    def test_runbook_match_id_field_exists(self) -> None:
        # Given the InvestigationRecord model

        # When checking for the runbook_match_id field
        fields = investigations.InvestigationRecord.model_fields

        # Then runbook_match_id is registered (FK to runbook_match.match_id)
        assert "runbook_match_id" in fields

    def test_runbook_match_id_column_is_nullable(self) -> None:
        # Given the SQLAlchemy table for InvestigationRecord

        # When inspecting the runbook_match_id column nullability
        column = investigations.InvestigationRecord.__table__.c.runbook_match_id

        # Then it is nullable (foundations runbook-match wiring lands in F6)
        assert column.nullable is True

    def test_runbook_match_id_has_foreign_key_to_runbook_match(self) -> None:
        # Given the SQLAlchemy table for InvestigationRecord

        # When inspecting the runbook_match_id column foreign keys
        column = investigations.InvestigationRecord.__table__.c.runbook_match_id
        foreign_keys = list(column.foreign_keys)

        # Then exactly one FK targets runbook_match.match_id
        assert len(foreign_keys) == 1
        target = foreign_keys[0]
        assert target.column.table.name == "runbook_match"
        assert target.column.name == "match_id"

    def test_runbook_match_id_foreign_key_constraint_name(self) -> None:
        # Given the SQLAlchemy table for InvestigationRecord

        # When inspecting the FK constraint name
        column = investigations.InvestigationRecord.__table__.c.runbook_match_id
        foreign_keys = list(column.foreign_keys)

        # Then the FK constraint carries the explicit name from the migration
        assert foreign_keys[0].constraint.name == "fk_investigation_runbook_match"

    def test_model_id_primary_field_exists(self) -> None:
        # Given the InvestigationRecord model

        # When checking for the model_id_primary field
        fields = investigations.InvestigationRecord.model_fields

        # Then model_id_primary is registered (LLM model identifier)
        assert "model_id_primary" in fields

    def test_model_id_primary_column_is_nullable(self) -> None:
        # Given the SQLAlchemy table for InvestigationRecord

        # When inspecting the model_id_primary column
        column = investigations.InvestigationRecord.__table__.c.model_id_primary

        # Then it is nullable (foundations writers populate it later)
        assert column.nullable is True

    def test_iteration_count_field_exists(self) -> None:
        # Given the InvestigationRecord model

        # When checking for the iteration_count field
        fields = investigations.InvestigationRecord.model_fields

        # Then iteration_count is registered (loop counter)
        assert "iteration_count" in fields

    def test_iteration_count_column_is_not_nullable(self) -> None:
        # Given the SQLAlchemy table for InvestigationRecord

        # When inspecting the iteration_count column
        column = investigations.InvestigationRecord.__table__.c.iteration_count

        # Then it is non-nullable (default 0 backfills existing rows)
        assert column.nullable is False

    def test_iteration_count_has_server_default_zero(self) -> None:
        # Given the SQLAlchemy table for InvestigationRecord

        # When inspecting the iteration_count column server default
        column = investigations.InvestigationRecord.__table__.c.iteration_count

        # Then a server_default of "0" exists so existing rows backfill cleanly
        assert column.server_default is not None
        assert "0" in str(column.server_default.arg)

    def test_iteration_count_python_default_is_zero(self) -> None:
        # Given the InvestigationRecord model

        # When constructing without iteration_count
        record = investigations.InvestigationRecord(
            alert_source="pagerduty",
            alert_id="P-1",
            alert_title="Test alert",
            severity="critical",
            service="payments",
        )

        # Then iteration_count defaults to 0 on the Python side
        assert record.iteration_count == 0

    def test_terminated_reason_field_exists(self) -> None:
        # Given the InvestigationRecord model

        # When checking for the terminated_reason field
        fields = investigations.InvestigationRecord.model_fields

        # Then terminated_reason is registered (loop termination reason)
        assert "terminated_reason" in fields

    def test_terminated_reason_column_is_nullable(self) -> None:
        # Given the SQLAlchemy table for InvestigationRecord

        # When inspecting the terminated_reason column
        column = investigations.InvestigationRecord.__table__.c.terminated_reason

        # Then it is nullable (only set when an investigation terminates early)
        assert column.nullable is True

    def test_loop_cap_hit_field_exists(self) -> None:
        # Given the InvestigationRecord model

        # When checking for the loop_cap_hit field
        fields = investigations.InvestigationRecord.model_fields

        # Then loop_cap_hit is registered (loop cap signal)
        assert "loop_cap_hit" in fields

    def test_loop_cap_hit_column_is_not_nullable(self) -> None:
        # Given the SQLAlchemy table for InvestigationRecord

        # When inspecting the loop_cap_hit column
        column = investigations.InvestigationRecord.__table__.c.loop_cap_hit

        # Then it is non-nullable (default false backfills existing rows)
        assert column.nullable is False

    def test_loop_cap_hit_has_server_default_false(self) -> None:
        # Given the SQLAlchemy table for InvestigationRecord

        # When inspecting the loop_cap_hit column server default
        column = investigations.InvestigationRecord.__table__.c.loop_cap_hit

        # Then a server_default of "false" exists so existing rows backfill cleanly
        assert column.server_default is not None
        assert "false" in str(column.server_default.arg).lower()

    def test_loop_cap_hit_python_default_is_false(self) -> None:
        # Given the InvestigationRecord model

        # When constructing without loop_cap_hit
        record = investigations.InvestigationRecord(
            alert_source="datadog",
            alert_id="DD-1",
            alert_title="Other alert",
            severity="warning",
            service="orders",
        )

        # Then loop_cap_hit defaults to False on the Python side
        assert record.loop_cap_hit is False


class TestInvestigationRecordConstruction:
    def test_minimal_construction_succeeds_without_extension_fields(self) -> None:
        # Given the minimum viable inputs for an investigation row

        # When constructing an InvestigationRecord without RFC 12.3.4 fields
        record = investigations.InvestigationRecord(
            alert_source="pagerduty",
            alert_id="P-2",
            alert_title="Crashloop",
            severity="critical",
            service="payments",
        )

        # Then the new fields default to safe values
        assert record.request_id is None
        assert record.runbook_match_id is None
        assert record.model_id_primary is None
        assert record.iteration_count == 0
        assert record.terminated_reason is None
        assert record.loop_cap_hit is False

    def test_request_id_round_trips_when_provided(self) -> None:
        # Given a request_id linking the investigation to an alert_request envelope
        request_id = uuid.uuid4()

        # When constructing an InvestigationRecord with that request_id
        record = investigations.InvestigationRecord(
            alert_source="pagerduty",
            alert_id="P-3",
            alert_title="Linked alert",
            severity="warning",
            service="orders",
            request_id=request_id,
        )

        # Then the request_id round-trips
        assert record.request_id == request_id

    def test_runbook_match_id_round_trips_when_provided(self) -> None:
        # Given a runbook_match_id from a sibling row in runbook_match
        match_id = uuid.uuid4()

        # When constructing an InvestigationRecord linking to a runbook match
        record = investigations.InvestigationRecord(
            alert_source="datadog",
            alert_id="DD-3",
            alert_title="Linked match",
            severity="warning",
            service="orders",
            runbook_match_id=match_id,
        )

        # Then runbook_match_id round-trips
        assert record.runbook_match_id == match_id

    def test_model_id_primary_round_trips_when_provided(self) -> None:
        # Given a model identifier from agent invocation context
        model_id = "openai/gpt-4.1"

        # When constructing an InvestigationRecord with that model id
        record = investigations.InvestigationRecord(
            alert_source="pagerduty",
            alert_id="P-4",
            alert_title="Agent-driven alert",
            severity="critical",
            service="checkout",
            model_id_primary=model_id,
        )

        # Then model_id_primary round-trips
        assert record.model_id_primary == model_id

    def test_terminated_reason_round_trips_when_provided(self) -> None:
        # Given a termination reason describing why the loop ended
        reason = "loop_cap_reached"

        # When constructing an InvestigationRecord with that reason and loop cap hit
        record = investigations.InvestigationRecord(
            alert_source="pagerduty",
            alert_id="P-5",
            alert_title="Capped",
            severity="critical",
            service="checkout",
            terminated_reason=reason,
            loop_cap_hit=True,
            iteration_count=10,
        )

        # Then the loop telemetry round-trips
        assert record.terminated_reason == reason
        assert record.loop_cap_hit is True
        assert record.iteration_count == 10
