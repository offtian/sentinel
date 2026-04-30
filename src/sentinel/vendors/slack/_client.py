from __future__ import annotations

from slack_sdk.errors import SlackApiError as _SlackSdkApiError
from slack_sdk.web.async_client import AsyncWebClient

from sentinel.settings import settings
from sentinel.utils import logs


class SlackClientError(Exception):
    """Raised when the Slack SDK returns an API error."""


class AsyncSlackClient:
    """Type-safe wrapper around ``slack_sdk.web.async_client.AsyncWebClient``."""

    def __init__(self, *, token: str | None) -> None:
        self._token = token
        self._sdk: AsyncWebClient | None = AsyncWebClient(token=token) if token else None

    @property
    def is_configured(self) -> bool:
        """Return True when a bot token was supplied."""
        return self._sdk is not None

    async def post_message(
        self,
        *,
        channel: str,
        text: str,
        blocks: list[dict[str, object]],
    ) -> str | None:
        """
        Post a message to ``channel``.

        Return the message ``ts``, or None if the client is not configured.

        :raises SlackClientError: on Slack API errors.
        """
        if self._sdk is None:
            logs.log_event(
                "slack_post_skipped",
                params={"reason": "No token configured", "channel": channel},
            )
            return None
        try:
            response = await self._sdk.chat_postMessage(
                channel=channel,
                text=text,
                blocks=blocks,
            )
            return response.get("ts")
        except _SlackSdkApiError as exc:
            raise SlackClientError(str(exc.response.get("error", exc))) from exc

    async def update_message(
        self,
        *,
        channel: str,
        ts: str,
        text: str,
        blocks: list[dict[str, object]],
    ) -> None:
        """
        Edit an existing message in-place.

        :raises SlackClientError: on Slack API errors.
        """
        if self._sdk is None:
            return
        try:
            await self._sdk.chat_update(
                channel=channel,
                ts=ts,
                text=text,
                blocks=blocks,
            )
        except _SlackSdkApiError as exc:
            raise SlackClientError(str(exc.response.get("error", exc))) from exc


_singleton: AsyncSlackClient | None = None


def get_client() -> AsyncSlackClient:
    """Return a module-level singleton ``AsyncSlackClient`` (no-op when unconfigured)."""
    global _singleton  # noqa: PLW0603
    if _singleton is None:
        _singleton = AsyncSlackClient(token=settings.slack_bot_token or None)
    return _singleton
