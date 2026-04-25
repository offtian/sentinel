"""
ASGI middleware for FastAPI ingress (F2.2 / F2.3).

``RequestIdMiddleware`` mints (or accepts) a UUID4 ``request_id`` for
every inbound request, exposes it on ``request.state.request_id`` for
downstream handlers, binds it to ``structlog.contextvars`` so log
records emitted during the request inherit the id, sets it as an
attribute on the current OTel span for trace correlation, and echoes
it back to the caller via the ``X-Request-Id`` response header.

The middleware is the only place ``request_id`` is minted. Webhook
handlers further compose an ``Envelope`` (RFC §3.1) onto this id once
``tenant_id`` / ``cluster_id`` / ``region`` / ``pii_class`` are known —
that work lives outside this module.
"""

from __future__ import annotations

import uuid

import structlog
from opentelemetry import trace as otel_trace
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from sentinel.utils import logs


_REQUEST_ID_HEADER = "X-Request-Id"
_REQUEST_ID_CONTEXTVAR = "request_id"
_REQUEST_ID_SPAN_ATTRIBUTE = "request_id"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Mint or accept a ``request_id`` and propagate it to observability."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = _resolve_request_id(request=request)

        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(
            **{_REQUEST_ID_CONTEXTVAR: str(request_id)},
        )
        otel_trace.get_current_span().set_attribute(
            _REQUEST_ID_SPAN_ATTRIBUTE,
            str(request_id),
        )

        try:
            response: Response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars(_REQUEST_ID_CONTEXTVAR)

        response.headers[_REQUEST_ID_HEADER] = str(request_id)
        return response


def _resolve_request_id(*, request: Request) -> uuid.UUID:
    """
    Return the UUID4 to use for this request.

    Parses the inbound ``X-Request-Id`` header when present and valid;
    mints a fresh UUID4 when absent or malformed. Malformed values are
    logged so operators can spot upstream callers sending bad ids.
    """
    raw = request.headers.get(_REQUEST_ID_HEADER)
    if raw is None:
        return uuid.uuid4()
    try:
        return uuid.UUID(raw)
    except ValueError:
        logs.log_event("request_id_invalid", params={"received": raw})
        return uuid.uuid4()
