"""One JSON shape for every error response.

```
{
  "code":        "rate_limited",     # stable identifier, frontend switches on this
  "message":     "Wait 42 seconds.", # human-readable, safe to show users
  "request_id":  "req_abc123",       # match against backend logs / Sentry
  "retry_after": 42                  # optional, seconds — only on 429
  "details":     {...}               # optional, validation errors etc
}
```

Why not RFC 7807 verbatim: we drop `type` and `title` (they're not actionable
for our SPA) and add `request_id` (which 7807 doesn't define). Same idea,
narrower contract.

Handler registration order matters: most specific first. Without an exception
handler for `Exception`, FastAPI would still log + 500 — but it wouldn't carry
our envelope, so the frontend's typed parser would fall through to a generic
toast and we'd lose the request_id link.
"""
from __future__ import annotations

from typing import Any

import sentry_sdk
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.observability import get_logger

log = get_logger(__name__)


# ── Envelope ──────────────────────────────────────────────────────────────────

class ErrorEnvelope(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    retry_after: int | None = None
    details: dict[str, Any] | None = None


# ── Custom domain exceptions ──────────────────────────────────────────────────

class AppError(Exception):
    """Base for errors we throw on purpose. Subclasses encode their HTTP status
    and the stable error code in one place so handlers don't need a big switch."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        self.retry_after = retry_after


class RateLimited(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"


class QuotaExceeded(AppError):
    """Hit a plan-level monthly cap. Distinct from rate-limited so the UI can
    nudge the user to upgrade rather than just wait."""

    status_code = status.HTTP_402_PAYMENT_REQUIRED
    code = "quota_exceeded"


class NoOrganization(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "no_organization"


class UpstreamUnavailable(AppError):
    """LLM/embedding provider failed or timed out."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "upstream_unavailable"


class ModerationBlocked(AppError):
    """Input rejected by the moderation catalog (prompt injection, jailbreak,
    etc.). 400 because the *request* is malformed-by-policy. Distinct code so
    the frontend can render an amber 'this query was blocked' panel instead
    of the red generic error UI."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "moderation_blocked"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _envelope_response(
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str | None,
    retry_after: int | None = None,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    body = ErrorEnvelope(
        code=code,
        message=message,
        request_id=request_id,
        retry_after=retry_after,
        details=details,
    ).model_dump(exclude_none=True)

    headers: dict[str, str] = {}
    if request_id:
        headers["X-Request-ID"] = request_id
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)

    return JSONResponse(status_code=status_code, content=body, headers=headers)


# ── HTTPException code mapping ────────────────────────────────────────────────

# Map HTTP status → stable error code. Anything not listed falls through to
# `http_error` so the frontend can still render something sensible.
_STATUS_TO_CODE = {
    400: "bad_request",
    401: "unauthorized",
    402: "quota_exceeded",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    502: "upstream_unavailable",
    503: "service_unavailable",
    504: "gateway_timeout",
}


def _code_for_status(status_code: int) -> str:
    return _STATUS_TO_CODE.get(status_code, "http_error")


# ── Handler registration ──────────────────────────────────────────────────────

def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        rid = _request_id(request)
        # 5xx subclasses still get captured by Sentry. 4xx is user-facing and
        # would noise up the dashboard, so we deliberately skip Sentry capture.
        if exc.status_code >= 500:
            sentry_sdk.capture_exception(exc)
            log.exception(
                "app_error",
                code=exc.code,
                status_code=exc.status_code,
                message=exc.message,
            )
        else:
            log.info(
                "app_error",
                code=exc.code,
                status_code=exc.status_code,
                message=exc.message,
            )
        return _envelope_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            request_id=rid,
            retry_after=exc.retry_after,
            details=exc.details,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        rid = _request_id(request)
        # `detail` from HTTPException is either a string or arbitrary JSON.
        if isinstance(exc.detail, str):
            message = exc.detail
            details = None
        else:
            message = str(exc.detail) if exc.detail is not None else ""
            details = exc.detail if isinstance(exc.detail, dict) else None

        retry_after = None
        if exc.headers and "Retry-After" in exc.headers:
            try:
                retry_after = int(exc.headers["Retry-After"])
            except (ValueError, TypeError):
                retry_after = None

        if exc.status_code >= 500:
            sentry_sdk.capture_exception(exc)
            log.exception("http_exception", status_code=exc.status_code, message=message)

        return _envelope_response(
            status_code=exc.status_code,
            code=_code_for_status(exc.status_code),
            message=message or "Request failed.",
            request_id=rid,
            retry_after=retry_after,
            details=details,
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        rid = _request_id(request)
        # FastAPI's default ValidationError JSON shape is fine for debugging;
        # we tuck it into `details` and present a generic top-level message.
        return _envelope_response(
            status_code=422,
            code="validation_error",
            message="The request body didn't match the expected shape.",
            request_id=rid,
            details={"errors": exc.errors()},
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """Catch-all. Never let a raw 500 escape without our envelope."""
        rid = _request_id(request)
        sentry_sdk.capture_exception(exc)
        log.exception("unhandled_exception", exc_type=type(exc).__name__)
        return _envelope_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="internal_error",
            message=(
                "Something broke on our side. "
                f"Quote request id {rid} when you reach out for help."
                if rid
                else "Something broke on our side. Please try again."
            ),
            request_id=rid,
        )
