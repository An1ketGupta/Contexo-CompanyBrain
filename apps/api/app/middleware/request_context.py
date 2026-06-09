"""Request lifecycle middleware: mint/forward request IDs, bind log context.

Runs early (registered last → executed first) so every downstream handler,
exception handler, and log line has `request_id` in scope. We deliberately
accept an inbound `X-Request-ID` if Next.js sends one — that's how a single
ID joins the browser breadcrumb, edge middleware, server log, and FastAPI log
into one searchable trace.

We do NOT trust an inbound ID blindly: if it doesn't match a conservative
character class we mint our own. Otherwise a malicious client could inject
log-line breaks or path-traversal-looking strings into structured logs.
"""
from __future__ import annotations

import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.observability import (
    bind_request_context,
    clear_request_context,
    get_logger,
)

log = get_logger(__name__)

# Accept caller-provided IDs that look like UUIDs, hex strings, or our own
# `req_<token>` prefix. Anything else we replace with a freshly minted ID.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]{8,128}$")


def _new_request_id() -> str:
    # Short, URL-safe, easy to quote in support emails: req_<24 hex>.
    return f"req_{uuid.uuid4().hex[:24]}"


def _coerce_request_id(value: str | None) -> str:
    if value and _SAFE_ID.match(value):
        return value
    return _new_request_id()


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = _coerce_request_id(request.headers.get("x-request-id"))
        request.state.request_id = request_id

        bind_request_context(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            # Always echo the request ID back so the browser can capture it
            # even on success paths (used for the "Last request" breadcrumb).
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            # One log line per request. Errors get their own log line from the
            # exception handler, so this one is fine at INFO.
            log.info(
                "http_request",
                status_code=status_code,
                duration_ms=round(duration_ms, 2),
            )
            clear_request_context()
