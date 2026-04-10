from __future__ import annotations

import asyncio

import uvicorn
from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler

from sentinel import bootstrap
from sentinel.interfaces.graphs import agents as agent_module
from sentinel.interfaces.slack import (
    event_handlers as _event_handlers,  # noqa: F401 — registers @app decorators
)
from sentinel.interfaces.slack.app import app as slack_app
from sentinel.plugins.config import boot as boot_config
from sentinel.settings import get_settings


async def _run_api() -> None:
    config = uvicorn.Config(
        "sentinel.interfaces.api.app:app",
        host="0.0.0.0",
        port=8000,
        reload=False,  # reload=True is incompatible with asyncio.gather
    )
    server = uvicorn.Server(config)
    await server.serve()


async def _run_slack() -> None:
    handler = AsyncSocketModeHandler(slack_app, get_settings().slack_app_token)
    await handler.start_async()  # type: ignore[no-untyped-call]


async def _main() -> None:
    # Initialise early so Slack handler has logging before the FastAPI
    # lifespan fires.  Idempotent — the lifespan's second call is a no-op.
    bootstrap.initialise()
    boot_config(agent_module=agent_module)

    if get_settings().slack_app_token:
        await asyncio.gather(_run_api(), _run_slack())
    else:
        await _run_api()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
