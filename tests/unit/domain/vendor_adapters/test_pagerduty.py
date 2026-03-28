from __future__ import annotations

from unittest.mock import MagicMock, patch

from sentinel.domain.vendor_adapters import pagerduty


class TestPagerDutyClientIsConfigured:
    def test_configured_when_api_key_provided(self):
        # Given a PagerDutyClient with an explicit api_key
        # When checking is_configured
        client = pagerduty.PagerDutyClient(api_key="secret-key")

        # Then it reports as configured
        assert client.is_configured is True

    def test_not_configured_when_api_key_empty(self):
        # Given a PagerDutyClient with an empty api_key
        # When checking is_configured
        client = pagerduty.PagerDutyClient(api_key="")

        # Then it reports as not configured
        assert client.is_configured is False


class TestPagerDutyClientAddIncidentNote:
    async def test_returns_none_when_not_configured(self):
        # Given an unconfigured client
        # When adding an incident note
        client = pagerduty.PagerDutyClient(api_key="")
        result = await client.add_incident_note("P123", "Some note")

        # Then None is returned (skipped)
        assert result is None

    async def test_returns_note_dict_on_success(self):
        # Given a configured client with a mocked session
        # When adding an incident note
        mock_response = MagicMock()
        mock_response.json.return_value = {"note": {"id": "note-1", "content": "Root cause found"}}

        mock_session = MagicMock()
        mock_session.post.return_value = mock_response

        client = pagerduty.PagerDutyClient(api_key="secret-key")
        with patch.object(client, "_get_session", return_value=mock_session):
            result = await client.add_incident_note("P123ABC", "Root cause found")

        # Then the note dict is returned
        assert result is not None
        assert result["id"] == "note-1"

    async def test_returns_none_on_sdk_exception(self):
        # Given a configured client whose session raises
        # When adding an incident note
        client = pagerduty.PagerDutyClient(api_key="secret-key")
        mock_session = MagicMock()
        mock_session.post.side_effect = Exception("Network error")

        with patch.object(client, "_get_session", return_value=mock_session):
            result = await client.add_incident_note("P123ABC", "Some note")

        # Then None is returned gracefully
        assert result is None


class TestPagerDutyClientUpdateIncidentStatus:
    async def test_returns_false_when_not_configured(self):
        # Given an unconfigured client
        # When updating incident status
        client = pagerduty.PagerDutyClient(api_key="")
        result = await client.update_incident_status(
            incident_id="P123",
            status="resolved",
            requester_email="ops@example.com",
        )

        # Then False is returned
        assert result is False

    async def test_returns_true_on_success(self):
        # Given a configured client with a successful session response
        # When updating incident status
        mock_response = MagicMock()
        mock_session = MagicMock()
        mock_session.put.return_value = mock_response

        client = pagerduty.PagerDutyClient(api_key="secret-key")
        with patch.object(client, "_get_session", return_value=mock_session):
            result = await client.update_incident_status(
                incident_id="P123",
                status="resolved",
                requester_email="ops@example.com",
            )

        # Then True is returned
        assert result is True

    async def test_returns_false_on_exception(self):
        # Given a configured client whose session raises
        # When updating incident status
        client = pagerduty.PagerDutyClient(api_key="secret-key")
        mock_session = MagicMock()
        mock_session.put.side_effect = Exception("Timeout")

        with patch.object(client, "_get_session", return_value=mock_session):
            result = await client.update_incident_status(
                incident_id="P123",
                status="resolved",
                requester_email="ops@example.com",
            )

        # Then False is returned gracefully
        assert result is False


class TestPagerDutyClientFormatInvestigationNote:
    def test_formats_full_note(self):
        # Given a full investigation result
        # When formatting as a PagerDuty note
        client = pagerduty.PagerDutyClient(api_key="key")
        note = client.format_investigation_note(
            root_cause="Database connection pool exhausted",
            remediation="1. Increase pool size\n2. Restart service",
            confidence_label="High",
            findings_summary="Error rate spiked 5x at 14:32 UTC",
        )

        # Then all sections appear in the note
        assert "Sentinel Investigation Results" in note
        assert "High" in note
        assert "Database connection pool exhausted" in note
        assert "Increase pool size" in note
        assert "Error rate spiked" in note

    def test_formats_note_without_optional_fields(self):
        # Given minimal investigation data
        # When formatting as a note
        client = pagerduty.PagerDutyClient(api_key="key")
        note = client.format_investigation_note(
            root_cause=None,
            remediation=None,
            confidence_label=None,
            findings_summary="",
        )

        # Then it renders without error
        assert "Sentinel Investigation Results" in note
