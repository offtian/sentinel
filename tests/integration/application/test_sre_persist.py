from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from sentinel.application.sre import persist


def _make_session() -> MagicMock:
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.execute = AsyncMock()
    return session


class TestSaveInvestigation:
    async def test_saves_record_with_all_fields(self):
        # Given a session and full investigation data
        session = _make_session()
        started = datetime(2024, 1, 1, tzinfo=UTC)
        completed = datetime(2024, 1, 1, 0, 5, tzinfo=UTC)

        # When saving the investigation
        with patch("sentinel.application.sre.persist.logs"):
            record = await persist.save_investigation(
                session,
                alert_source="pagerduty",
                alert_id="P123ABC",
                alert_title="High CPU usage",
                severity="high",
                service="api-service",
                root_cause="OOM killer triggered",
                remediation="Increase memory limit",
                confidence_score=0.85,
                findings_json={"errors": "5x spike"},
                started_at=started,
                completed_at=completed,
            )

        # Then the record is added and committed
        session.add.assert_called_once()
        session.commit.assert_awaited_once()
        session.refresh.assert_awaited_once()

        assert record.alert_id == "P123ABC"
        assert record.service == "api-service"
        assert record.root_cause == "OOM killer triggered"
        assert record.status == "completed"

    async def test_saves_record_with_minimal_fields(self):
        # Given only required investigation data
        # When saving the investigation
        session = _make_session()

        with patch("sentinel.application.sre.persist.logs"):
            record = await persist.save_investigation(
                session,
                alert_source="datadog",
                alert_id="DD-99",
                alert_title="Disk full",
                severity="critical",
                service="storage",
            )

        # Then default values are applied
        assert record.root_cause is None
        assert record.remediation is None
        assert record.confidence_score is None
        assert record.status == "completed"


class TestGetInvestigation:
    async def test_returns_record_when_found(self):
        # Given a session that returns a record
        session = _make_session()
        record_id = uuid.uuid4()
        mock_record = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_record
        session.execute.return_value = mock_result

        # When fetching by ID
        with patch("sentinel.application.sre.persist.logs"):
            result = await persist.get_investigation(session, record_id=record_id)

        # Then the record is returned
        assert result == mock_record

    async def test_returns_none_when_not_found(self):
        # Given a session that finds nothing
        session = _make_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        # When fetching a non-existent ID
        result = await persist.get_investigation(session, record_id=uuid.uuid4())

        # Then None is returned
        assert result is None


class TestGetInvestigationsForService:
    async def test_returns_list_for_service(self):
        # Given a session with two investigation records for a service
        session = _make_session()
        mock_record_1 = MagicMock()
        mock_record_2 = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_record_1, mock_record_2]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        session.execute.return_value = mock_result

        # When fetching investigations for a service
        results = await persist.get_investigations_for_service(session, service="api-service")

        # Then both records are returned
        assert len(results) == 2

    async def test_returns_empty_list_when_no_records(self):
        # Given a session with no records
        session = _make_session()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        session.execute.return_value = mock_result

        # When fetching investigations for an unknown service
        results = await persist.get_investigations_for_service(session, service="unknown-svc")

        # Then an empty list is returned
        assert results == []
