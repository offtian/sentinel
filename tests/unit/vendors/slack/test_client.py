from __future__ import annotations

from unittest import mock

import pytest
from slack_sdk.errors import SlackApiError

from sentinel.vendors.slack import _client as slack_client_mod


class TestAsyncSlackClient:
    def test_is_configured_returns_false_when_no_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given no Slack bot token in settings
        fake_settings = mock.MagicMock()
        fake_settings.slack_bot_token = ""
        monkeypatch.setattr(slack_client_mod, "settings", fake_settings)

        client = slack_client_mod.AsyncSlackClient(token=None)

        # Then is_configured is False
        assert client.is_configured is False

    def test_is_configured_returns_true_when_token_present(self) -> None:
        # Given a valid token
        client = slack_client_mod.AsyncSlackClient(token="xoxb-fake-token")  # noqa: S106

        # Then is_configured is True
        assert client.is_configured is True

    @pytest.mark.asyncio
    async def test_post_message_calls_sdk(self) -> None:
        # Given a client with a mocked raw SDK client
        mock_sdk = mock.AsyncMock()
        mock_sdk.chat_postMessage.return_value = {"ok": True, "ts": "1.0"}

        client = slack_client_mod.AsyncSlackClient(token="xoxb-test")  # noqa: S106
        client._sdk = mock_sdk  # inject mock

        # When posting a message
        await client.post_message(channel="C1", text="hello", blocks=[])

        # Then the SDK's chat_postMessage is called
        mock_sdk.chat_postMessage.assert_awaited_once_with(
            channel="C1",
            text="hello",
            blocks=[],
        )

    @pytest.mark.asyncio
    async def test_post_message_wraps_sdk_error(self) -> None:
        # Given a client whose SDK raises SlackApiError
        mock_sdk = mock.AsyncMock()
        mock_sdk.chat_postMessage.side_effect = SlackApiError(
            message="channel_not_found", response={"ok": False, "error": "channel_not_found"}
        )
        client = slack_client_mod.AsyncSlackClient(token="xoxb-test")  # noqa: S106
        client._sdk = mock_sdk

        # Then post_message raises SlackClientError (our own error type)
        with pytest.raises(slack_client_mod.SlackClientError, match="channel_not_found"):
            await client.post_message(channel="C1", text="hi", blocks=[])
