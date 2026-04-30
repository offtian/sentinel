from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class MentionEvent(BaseModel, frozen=True):
    """Parsed Slack app_mention event."""

    user_id: str
    channel: str
    thread_ts: str
    message_ts: str
    text: str


class MessageEvent(BaseModel, frozen=True):
    """Parsed Slack message event (DM or channel)."""

    user_id: str
    channel: str
    thread_ts: str
    message_ts: str
    text: str
    channel_type: str


def parse_mention_event(event: dict[str, Any]) -> MentionEvent:
    """Parse a raw Slack app_mention event dict into a typed model."""
    ts = event["ts"]
    return MentionEvent(
        user_id=event["user"],
        channel=event["channel"],
        thread_ts=event.get("thread_ts") or ts,
        message_ts=ts,
        text=event.get("text", ""),
    )


def parse_message_event(event: dict[str, Any]) -> MessageEvent:
    """Parse a raw Slack message event dict into a typed model."""
    ts = event["ts"]
    return MessageEvent(
        user_id=event["user"],
        channel=event["channel"],
        thread_ts=event.get("thread_ts") or ts,
        message_ts=ts,
        text=event.get("text", ""),
        channel_type=event.get("channel_type", ""),
    )
