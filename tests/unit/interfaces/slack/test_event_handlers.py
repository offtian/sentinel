from __future__ import annotations

from unittest import mock

import pytest

from sentinel.interfaces.slack import event_handlers


_MENTION_EVENT = {
    "type": "app_mention",
    "user": "U12345",
    "channel": "C99999",
    "ts": "1700000001.000000",
    "thread_ts": "1700000000.000000",
    "text": "<@UBOT> investigate CPU spike",
}

_DM_EVENT = {
    "type": "message",
    "user": "U54321",
    "channel": "D11111",
    "ts": "1700000002.000000",
    "text": "high latency on checkout",
    "channel_type": "im",
}


class TestHandleAppMention:
    @pytest.mark.asyncio
    async def test_passes_clean_text_to_handle_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given a mention event and a mocked _handle_request
        captured: dict = {}

        async def fake_handle(text, *, client, channel, thread_ts, user_id):
            captured["text"] = text
            captured["channel"] = channel

        monkeypatch.setattr(event_handlers, "_handle_request", fake_handle)
        fake_client = mock.AsyncMock()
        fake_ack = mock.AsyncMock()

        # When the handler is invoked
        await event_handlers.handle_app_mention(
            event=_MENTION_EVENT, client=fake_client, ack=fake_ack
        )

        # Then _handle_request receives the text and correct channel
        assert captured["channel"] == "C99999"
        assert "investigate CPU spike" in captured["text"]

    @pytest.mark.asyncio
    async def test_posts_error_message_on_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given _handle_request raises unexpectedly
        async def boom(*_, **__):
            raise RuntimeError("agent down")

        monkeypatch.setattr(event_handlers, "_handle_request", boom)
        monkeypatch.setattr(event_handlers.logs, "log_exception", mock.MagicMock())
        fake_client = mock.AsyncMock()
        fake_ack = mock.AsyncMock()

        # When the handler is invoked
        await event_handlers.handle_app_mention(
            event=_MENTION_EVENT, client=fake_client, ack=fake_ack
        )

        # Then a user-facing error is posted to the thread
        fake_client.chat_postMessage.assert_awaited_once()
        call_kwargs = fake_client.chat_postMessage.call_args.kwargs
        assert call_kwargs["channel"] == "C99999"
        assert "wrong" in call_kwargs["text"].lower() or "error" in call_kwargs["text"].lower()


class TestHandleDirectMessage:
    @pytest.mark.asyncio
    async def test_skips_bot_messages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given a message event from a bot
        bot_event = {**_DM_EVENT, "bot_id": "B999"}
        captured: list = []

        async def fake_handle(*_, **__):
            captured.append(True)

        monkeypatch.setattr(event_handlers, "_handle_request", fake_handle)
        fake_client = mock.AsyncMock()
        fake_ack = mock.AsyncMock()

        # When the handler is invoked
        await event_handlers.handle_direct_message(
            event=bot_event, client=fake_client, ack=fake_ack
        )

        # Then _handle_request is never called (bot messages are ignored)
        assert captured == []
