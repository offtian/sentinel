from __future__ import annotations

import pytest
from pydantic import ValidationError

from sentinel.vendors.slack import _parsers as slack_parsers


class TestParseMentionEvent:
    def test_extracts_user_channel_thread_and_text(self) -> None:
        # Given a raw Slack app_mention event dict
        raw = {
            "type": "app_mention",
            "user": "U12345",
            "channel": "C99999",
            "ts": "1700000001.000000",
            "thread_ts": "1700000000.000000",
            "text": "<@UBOT> investigate CPU spike",
        }

        # When parsed
        event = slack_parsers.parse_mention_event(raw)

        # Then structured fields are accessible
        assert event.user_id == "U12345"
        assert event.channel == "C99999"
        assert event.thread_ts == "1700000000.000000"
        assert event.message_ts == "1700000001.000000"
        assert event.text == "<@UBOT> investigate CPU spike"

    def test_thread_ts_falls_back_to_ts_when_absent(self) -> None:
        # Given a mention not inside a thread
        raw = {
            "type": "app_mention",
            "user": "U12345",
            "channel": "C99999",
            "ts": "1700000001.000000",
            "text": "@bot help",
        }

        # When parsed
        event = slack_parsers.parse_mention_event(raw)

        # Then thread_ts mirrors ts
        assert event.thread_ts == "1700000001.000000"


class TestParseMessageEvent:
    def test_extracts_standard_dm_fields(self) -> None:
        # Given a DM message event
        raw = {
            "type": "message",
            "user": "U54321",
            "channel": "D11111",
            "ts": "1700000002.000000",
            "text": "what's the root cause?",
            "channel_type": "im",
        }

        # When parsed
        event = slack_parsers.parse_message_event(raw)

        # Then fields are available
        assert event.user_id == "U54321"
        assert event.channel == "D11111"
        assert event.channel_type == "im"

    def test_missing_user_raises_validation_error(self) -> None:
        # Given a malformed event with no user field
        raw = {"type": "message", "channel": "C1", "ts": "1.0", "text": "hi"}

        # Then parsing raises ValidationError (not a silent empty string)
        with pytest.raises(ValidationError):
            slack_parsers.parse_message_event(raw)
