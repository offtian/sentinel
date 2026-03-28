from __future__ import annotations

from slack_bolt.async_app import AsyncApp

from sentinel.settings import get_settings


# Single Bolt app instance imported by event_handlers and main.py.
# SLACK_SIGNING_SECRET is optional for Socket Mode (Slack verifies the connection
# at the WebSocket level), but still recommended so HTTP fallback works too.
_s = get_settings()
app = AsyncApp(
    token=_s.slack_bot_token or None,
    signing_secret=_s.slack_signing_secret or None,
)
