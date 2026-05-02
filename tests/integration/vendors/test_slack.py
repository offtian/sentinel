from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from sentinel.vendors import slack


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
        # Given a configured Slack channel and token with a mocked client
        mock_slack_client = MagicMock()
        mock_slack_client.chat_postMessage = AsyncMock(return_value={"ok": True})

        with (
            patch.object(slack, "_client", None),
            patch("sentinel.vendors.slack.settings") as mock_gs,
            patch("sentinel.vendors.slack.logs"),
            patch("sentinel.vendors.slack.AsyncWebClient", return_value=mock_slack_client),
        ):
            mock_gs.return_value.sre_slack_channel = "#sre-alerts"
            mock_gs.return_value.slack_bot_token = "xoxb-token"  # noqa: S105

            await slack.post_investigation_summary(
                alert_id="P123",
                alert_title="High CPU usage",
                root_cause="Database connection pool exhausted",
                remediation="Restart the service",
                confidence_label="High",
                findings_summary="Error rate spiked 5x at 14:32 UTC",
            )

        # Then the Slack API is called once
        mock_slack_client.chat_postMessage.assert_called_once()
        call_kwargs = mock_slack_client.chat_postMessage.call_args.kwargs
        assert call_kwargs["channel"] == "#sre-alerts"
        assert "High CPU usage" in call_kwargs["text"]

    async def test_uses_explicit_channel_override(self):
        # Given an explicit channel override parameter
        # When posting an investigation summary
        mock_slack_client = MagicMock()
        mock_slack_client.chat_postMessage = AsyncMock(return_value={"ok": True})

        with (
            patch.object(slack, "_client", None),
            patch("sentinel.vendors.slack.settings") as mock_gs,
            patch("sentinel.vendors.slack.logs"),
            patch("sentinel.vendors.slack.AsyncWebClient", return_value=mock_slack_client),
        ):
            mock_gs.return_value.sre_slack_channel = "#default-channel"
            mock_gs.return_value.slack_bot_token = "xoxb-token"  # noqa: S105

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
        call_kwargs = mock_slack_client.chat_postMessage.call_args.kwargs
        assert call_kwargs["channel"] == "#override-channel"

    async def test_silently_handles_slack_exception(self):
        # Given a Slack client that raises during the API call
        # When posting a summary
        mock_slack_client = MagicMock()
        mock_slack_client.chat_postMessage = AsyncMock(side_effect=Exception("Slack API down"))

        with (
            patch.object(slack, "_client", None),
            patch("sentinel.vendors.slack.settings") as mock_gs,
            patch("sentinel.vendors.slack.logs") as mock_logs,
            patch("sentinel.vendors.slack.AsyncWebClient", return_value=mock_slack_client),
        ):
            mock_gs.return_value.sre_slack_channel = "#sre-alerts"
            mock_gs.return_value.slack_bot_token = "xoxb-token"  # noqa: S105

            # Then no exception is raised (errors are swallowed with log)
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
        # Given no Slack channel configured
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
        # Given a configured support Slack channel with a mocked client
        mock_slack_client = MagicMock()
        mock_slack_client.chat_postMessage = AsyncMock(return_value={"ok": True})

        with (
            patch.object(slack, "_client", None),
            patch("sentinel.vendors.slack.settings") as mock_gs,
            patch("sentinel.vendors.slack.logs"),
            patch("sentinel.vendors.slack.AsyncWebClient", return_value=mock_slack_client),
        ):
            mock_gs.return_value.support_slack_channel = "#support"
            mock_gs.return_value.slack_bot_token = "xoxb-token"  # noqa: S105

            await slack.post_support_suggestion(
                ticket_key="SUPPORT-42",
                ticket_summary="Cannot log in",
                suggested_response="Please reset your password.",
                confidence_label="High",
                category="account",
            )

        # Then the Slack API is called
        mock_slack_client.chat_postMessage.assert_called_once()
        call_kwargs = mock_slack_client.chat_postMessage.call_args.kwargs
        assert call_kwargs["channel"] == "#support"
        assert "SUPPORT-42" in call_kwargs["text"]


class TestBuildInvestigationBlocks:
    def test_includes_all_fields(self):
        # Given full investigation data
        # When building blocks
        blocks = slack._build_investigation_blocks(
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
        blocks = slack._build_investigation_blocks(
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
        blocks = slack._build_investigation_blocks(
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
        blocks = slack._build_investigation_blocks(
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
