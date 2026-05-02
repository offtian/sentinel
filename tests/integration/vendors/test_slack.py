from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from sentinel.vendors import slack
from sentinel.vendors.slack import _client as slack_client_mod


def _make_configured_client() -> MagicMock:
    mock_client = MagicMock(spec=slack_client_mod.AsyncSlackClient)
    mock_client.is_configured = True
    mock_client.post_message = AsyncMock(return_value="1234567890.123456")
    return mock_client


class TestPostInvestigationSummary:
    async def test_skips_when_no_channel_configured(self):
        # Given no Slack channel configured (settings is a singleton, not callable,
        # so attributes must be set directly on the mock, not on .return_value)
        # When posting an investigation summary
        with patch("sentinel.vendors.slack.settings") as mock_gs:
            mock_gs.sre_slack_channel = ""
            with patch("sentinel.vendors.slack.logs") as mock_logs:
                await slack.post_investigation_summary(
                    alert_id="P123",
                    alert_title="CPU spike",
                    root_cause="OOM killer",
                    remediation="Increase memory limit",
                    confidence_label="High",
                    findings_summary="Error rate spiked 5x",
                )

        # Then we log a skip event but do not raise
        mock_logs.log_event.assert_called_once()
        call_args = mock_logs.log_event.call_args
        assert call_args[0][0] == "slack_post_skipped"

    async def test_posts_to_slack_when_configured(self):
        # Given a configured Slack channel and a mocked client singleton
        mock_client = _make_configured_client()

        with (
            patch.object(slack_client_mod, "_singleton", mock_client),
            patch("sentinel.vendors.slack.settings") as mock_gs,
            patch("sentinel.vendors.slack.logs"),
        ):
            mock_gs.sre_slack_channel = "#sre-alerts"

            # When posting an investigation summary
            await slack.post_investigation_summary(
                alert_id="P123",
                alert_title="High CPU usage",
                root_cause="Database connection pool exhausted",
                remediation="Restart the service",
                confidence_label="High",
                findings_summary="Error rate spiked 5x at 14:32 UTC",
            )

        # Then the Slack client is called once with the correct channel and text
        mock_client.post_message.assert_called_once()
        call_kwargs = mock_client.post_message.call_args.kwargs
        assert call_kwargs["channel"] == "#sre-alerts"
        assert "High CPU usage" in call_kwargs["text"]

    async def test_uses_explicit_channel_override(self):
        # Given an explicit channel override parameter and a configured client
        mock_client = _make_configured_client()

        with (
            patch.object(slack_client_mod, "_singleton", mock_client),
            patch("sentinel.vendors.slack.settings") as mock_gs,
            patch("sentinel.vendors.slack.logs"),
        ):
            mock_gs.sre_slack_channel = "#default-channel"

            # When posting with a channel override
            await slack.post_investigation_summary(
                channel="#override-channel",
                alert_id="P123",
                alert_title="Test",
                root_cause=None,
                remediation=None,
                confidence_label=None,
                findings_summary="",
            )

        # Then the override channel is used
        call_kwargs = mock_client.post_message.call_args.kwargs
        assert call_kwargs["channel"] == "#override-channel"

    async def test_silently_handles_slack_exception(self):
        # Given a Slack client that raises a SlackClientError during post
        mock_client = _make_configured_client()
        mock_client.post_message = AsyncMock(
            side_effect=slack_client_mod.SlackClientError("Slack API down")
        )

        with (
            patch.object(slack_client_mod, "_singleton", mock_client),
            patch("sentinel.vendors.slack.settings") as mock_gs,
            patch("sentinel.vendors.slack.logs") as mock_logs,
        ):
            mock_gs.sre_slack_channel = "#sre-alerts"

            # When posting a summary
            # Then no exception is raised (errors are logged and swallowed)
            await slack.post_investigation_summary(
                alert_id="P123",
                alert_title="Test",
                root_cause=None,
                remediation=None,
                confidence_label=None,
                findings_summary="",
            )

        mock_logs.log_exception.assert_called_once()


class TestPostSupportSuggestion:
    async def test_skips_when_no_token_configured(self):
        # Given no support Slack channel configured
        # When posting a support suggestion
        with patch("sentinel.vendors.slack.settings") as mock_gs:
            mock_gs.support_slack_channel = ""
            with patch("sentinel.vendors.slack.logs") as mock_logs:
                await slack.post_support_suggestion(
                    ticket_key="SUPPORT-42",
                    ticket_summary="Cannot log in",
                    suggested_response="Please reset your password.",
                    confidence_label="High",
                    category="account",
                )

        # Then we log a skip event
        mock_logs.log_event.assert_called_once()

    async def test_posts_to_slack_when_configured(self):
        # Given a configured support Slack channel and a mocked client singleton
        mock_client = _make_configured_client()

        with (
            patch.object(slack_client_mod, "_singleton", mock_client),
            patch("sentinel.vendors.slack.settings") as mock_gs,
            patch("sentinel.vendors.slack.logs"),
        ):
            mock_gs.support_slack_channel = "#support"

            # When posting a support suggestion
            await slack.post_support_suggestion(
                ticket_key="SUPPORT-42",
                ticket_summary="Cannot log in",
                suggested_response="Please reset your password.",
                confidence_label="High",
                category="account",
            )

        # Then the Slack client is called with the correct channel and ticket key
        mock_client.post_message.assert_called_once()
        call_kwargs = mock_client.post_message.call_args.kwargs
        assert call_kwargs["channel"] == "#support"
        assert "SUPPORT-42" in call_kwargs["text"]


class TestBuildInvestigationBlocks:
    def test_includes_all_fields(self):
        # Given full investigation data
        # When building blocks
        blocks = slack.investigation_summary_blocks(
            alert_id="P123",
            alert_title="High CPU",
            root_cause="OOM killer triggered",
            remediation="Increase memory",
            confidence_label="High",
            findings_summary="CPU hit 95%",
        )

        # Then all key fields appear in the blocks
        block_text = str(blocks)
        assert "P123" in block_text
        assert "High CPU" in block_text
        assert "OOM killer triggered" in block_text
        assert "Increase memory" in block_text
        assert "CPU hit 95%" in block_text

    def test_high_confidence_uses_green_circle(self):
        # Given a High confidence label
        # When building blocks
        blocks = slack.investigation_summary_blocks(
            alert_id="X",
            alert_title="Test",
            root_cause=None,
            remediation=None,
            confidence_label="High",
            findings_summary="",
        )

        # Then the green circle emoji is used
        assert ":large_green_circle:" in str(blocks)

    def test_low_confidence_uses_red_circle(self):
        # Given an unknown confidence label
        # When building blocks
        blocks = slack.investigation_summary_blocks(
            alert_id="X",
            alert_title="Test",
            root_cause=None,
            remediation=None,
            confidence_label="Unknown",
            findings_summary="",
        )

        # Then the red circle emoji is used
        assert ":red_circle:" in str(blocks)

    def test_no_remediation_block_when_none(self):
        # Given no remediation
        # When building blocks
        blocks = slack.investigation_summary_blocks(
            alert_id="X",
            alert_title="Test",
            root_cause="Some root cause",
            remediation=None,
            confidence_label="Medium",
            findings_summary="",
        )

        # Then the blocks do not include a remediation section
        block_text = str(blocks)
        assert "Remediation" not in block_text
