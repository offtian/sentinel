from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MentionEvent(BaseModel, frozen=True):
    """Parsed Slack app_mention event."""

    user_id: str = Field(alias="user")
    channel: str
    thread_ts: str
    message_ts: str = Field(alias="ts")
    text: str = ""

    model_config = {"populate_by_name": True}


class MessageEvent(BaseModel, frozen=True):
    """Parsed Slack message event (DM or channel)."""

    user_id: str = Field(alias="user")
    channel: str
    thread_ts: str
    message_ts: str = Field(alias="ts")
    text: str = ""
    channel_type: str = ""

    model_config = {"populate_by_name": True}


def parse_mention_event(event: dict[str, Any]) -> MentionEvent:
    """Parse a raw Slack app_mention event dict into a typed model."""
    ts = event.get("ts", "")
    data = dict(event)
    if "thread_ts" not in data or not data["thread_ts"]:
        data["thread_ts"] = ts
    return MentionEvent.model_validate(data)


def parse_message_event(event: dict[str, Any]) -> MessageEvent:
    """Parse a raw Slack message event dict into a typed model."""
    ts = event.get("ts", "")
    data = dict(event)
    if "thread_ts" not in data or not data["thread_ts"]:
        data["thread_ts"] = ts
    return MessageEvent.model_validate(data)
