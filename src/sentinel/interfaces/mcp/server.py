"""
FastMCP server exposing Sentinel's tools to external agents.

Run as a separate deployment or locally with::

    uv run python -m sentinel.interfaces.mcp.server

Exposes observability, documentation, and investigation tools
via the MCP (Model Context Protocol) streamable HTTP transport.

The server must be configured before use by calling ``configure()``
with an observability client and document-searcher builder.  This is
done by ``sentinel.main`` or the ``__main__`` block at the bottom of
this file.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from fastmcp import FastMCP
from starlette import requests as starlette_requests
from starlette import responses as starlette_responses
from starlette import types as starlette_types

from sentinel.data import db as async_db
from sentinel.domain import skills as skills_mod
from sentinel.interfaces.mcp.tools import documentation as doc_tools
from sentinel.interfaces.mcp.tools import investigation as inv_tools
from sentinel.interfaces.mcp.tools import observability as obs_tools
from sentinel.utils import logs


if TYPE_CHECKING:
    import databases

    from sentinel.domain.search import searcher
    from sentinel.domain.vendor_adapters.observability import base as obs_base


logger = logs.get_logger()

# ---------------------------------------------------------------------------
# Module-level state — set by ``configure()`` before the server starts.
# ---------------------------------------------------------------------------

_obs_client: obs_base.BaseObservabilityClient | None = None
_doc_searcher_builder: Callable[[], searcher.BaseDocumentSearcher | None] | None = None


def configure(
    *,
    observability_client: obs_base.BaseObservabilityClient | None = None,
    document_searcher_builder: Callable[[], searcher.BaseDocumentSearcher | None] | None = None,
) -> None:
    """
    Inject runtime dependencies from a higher layer (config/main).

    Must be called before the MCP server handles any tool requests.
    """
    global _obs_client, _doc_searcher_builder  # noqa: PLW0603
    _obs_client = observability_client
    _doc_searcher_builder = document_searcher_builder


# ---------------------------------------------------------------------------
# FastMCP server instance
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "Sentinel",
    instructions=(
        "Sentinel AI SRE platform tools. "
        "Query observability data (logs, metrics, traces), "
        "search documentation, and trigger investigations."
    ),
)


@mcp.tool()
async def query_logs(service: str, query: str = "error OR warn", minutes_back: int = 30) -> str:
    """Search recent logs for a service. Returns formatted log entries."""
    return await obs_tools.query_logs(
        obs_client=_obs_client,
        service=service,
        query=query,
        minutes_back=minutes_back,
    )


@mcp.tool()
async def query_metrics(service: str, metric_name: str = "cpu", minutes_back: int = 60) -> str:
    """Fetch metric time series for a service."""
    return await obs_tools.query_metrics(
        obs_client=_obs_client,
        service=service,
        metric_name=metric_name,
        minutes_back=minutes_back,
    )


@mcp.tool()
async def query_error_traces(service: str, minutes_back: int = 30) -> str:
    """Search distributed traces for error spans in a service."""
    return await obs_tools.query_error_traces(
        obs_client=_obs_client,
        service=service,
        minutes_back=minutes_back,
    )


@mcp.tool()
async def search_documentation(query: str, max_results: int = 5) -> str:
    """Search documentation across Confluence, Notion, and S3."""
    doc_searcher = _doc_searcher_builder() if _doc_searcher_builder else None
    return await doc_tools.search_documentation(
        document_searcher=doc_searcher,
        query=query,
        max_results=max_results,
    )


def _get_optional_db() -> databases.Database | None:
    """Return the database connection, or None if not configured."""
    try:
        return async_db.get_db()
    except RuntimeError:
        return None


@mcp.tool()
async def trigger_investigation(alert_source: str, alert_id: str, description: str = "") -> str:
    """Trigger an SRE investigation for an alert. Returns a job ID."""
    return await inv_tools.trigger_investigation(
        db=_get_optional_db(),
        alert_source=alert_source,
        alert_id=alert_id,
        description=description,
    )


@mcp.tool()
async def get_investigation_status(investigation_id: str) -> str:
    """Check the status of a running investigation."""
    return await inv_tools.get_investigation_status(
        db=_get_optional_db(),
        investigation_id=investigation_id,
    )


@mcp.tool()
async def list_skills() -> str:
    """
    List installed Skills (runbooks) available to Sentinel agents.

    Returns a JSON-encoded list of ``{name, version, description, applies_to}``
    entries sorted alphabetically by name. Skill bodies are deliberately
    excluded from the response to keep internal runbook content off the
    wire unless explicitly requested through a future fetch_skill tool.
    """
    handles = skills_mod.all_installed_skills()
    entries = [
        {
            "name": handle.name,
            "version": handle.version,
            "description": handle.description,
            "applies_to": list(handle.applies_to),
        }
        for handle in handles
    ]
    return json.dumps(entries)


# ---------------------------------------------------------------------------
# API key authentication middleware
# ---------------------------------------------------------------------------

_API_KEY_HEADER = "X-API-Key"


class _ApiKeyMiddleware:
    """
    ASGI middleware that validates an ``X-API-Key`` header.

    When *expected_key* is empty, authentication is disabled (local dev mode)
    and all requests pass through.
    """

    def __init__(
        self,
        app: starlette_types.ASGIApp,
        *,
        expected_key: str,
    ) -> None:
        self._app = app
        self._expected_key = expected_key

    async def __call__(
        self,
        scope: starlette_types.Scope,
        receive: starlette_types.Receive,
        send: starlette_types.Send,
    ) -> None:
        """
        Validate the API key for HTTP requests.

        Non-HTTP scopes (lifespan, websocket) pass through unconditionally.
        """
        if scope["type"] != "http" or not self._expected_key:
            await self._app(scope, receive, send)
            return

        request = starlette_requests.Request(scope)
        provided_key = request.headers.get(_API_KEY_HEADER, "")

        if not provided_key or not hmac.compare_digest(provided_key, self._expected_key):
            response = starlette_responses.PlainTextResponse("Unauthorized", status_code=401)
            await response(scope, receive, send)
            return

        await self._app(scope, receive, send)


def build_asgi_app(*, api_key: str = "") -> starlette_types.ASGIApp:
    """
    Build a Starlette ASGI app wrapping the FastMCP server with auth middleware.

    :param api_key: Expected API key value. Empty string disables auth.
    :returns: ASGI application ready to be served.
    """
    inner_app = mcp.http_app()
    return _ApiKeyMiddleware(inner_app, expected_key=api_key)


if __name__ == "__main__":
    # Import from higher layers only in __main__ — not reachable by import-linter.
    import importlib

    bootstrap_mod = importlib.import_module("sentinel.bootstrap")
    config_mod = importlib.import_module("sentinel.config")
    agents_mod = importlib.import_module("sentinel.interfaces.graphs.agents")

    bootstrap_mod.initialise()
    config = config_mod.get_config()
    config.load_agents(agent_module=agents_mod)
    configure(
        observability_client=config.observability_client,
        document_searcher_builder=config.build_document_searcher,
    )
    mcp.run(transport="streamable-http")
