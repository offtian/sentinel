from __future__ import annotations

from typing import Any

from sentinel.interfaces.graphs.common import StatusUpdateClient
from sentinel.utils import logs


class SlackStatusUpdateClient(StatusUpdateClient):
    """
    Posts live pipeline status messages into a Slack thread.

    Strategy:
      1. On the first update, post a new message in the thread and save its ts.
      2. On subsequent updates, edit that same message in-place so the thread
         stays clean (one "thinking" message, updated as the graph progresses).
      3. The event handler replaces the final message with Block Kit output.
    """

    def __init__(
        self,
        *,
        client: Any,
        channel: str,
        thread_ts: str,
    ) -> None:
        self._client = client
        self._channel = channel
        self._thread_ts = thread_ts
        self._status_ts: str | None = None

    async def update_status(self, message: str) -> None:
        try:
            if self._status_ts is None:
                response = await self._client.chat_postMessage(
                    channel=self._channel,
                    thread_ts=self._thread_ts,
                    text=f"⏳ {message}",
                )
                self._status_ts = response["ts"]
            else:
                await self._client.chat_update(
                    channel=self._channel,
                    ts=self._status_ts,
                    text=f"⏳ {message}",
                )
        except Exception as exc:
            logs.log_exception(exc)

    async def replace_with_result(self, *, text: str, blocks: list[dict[str, object]]) -> None:
        """Replace the live status message with the final formatted result."""
        try:
            if self._status_ts:
                await self._client.chat_update(
                    channel=self._channel,
                    ts=self._status_ts,
                    text=text,
                    blocks=blocks,
                )
            else:
                await self._client.chat_postMessage(
                    channel=self._channel,
                    thread_ts=self._thread_ts,
                    text=text,
                    blocks=blocks,
                )
        except Exception as exc:
            logs.log_exception(exc)
