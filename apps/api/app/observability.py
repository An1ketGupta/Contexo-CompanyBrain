"""Process-wide observability bootstrap: structured logging + Sentry.

Called exactly once from `create_app()`. After this runs:

- Every `logging.getLogger(...)` call returns a structlog-configured logger.
  Stdlib loggers (uvicorn, httpx, supabase, etc.) flow through the same JSON
  pipeline so a single log shipper at the platform level captures everything.
- Sentry is wired with FastAPI + asyncio integrations and is ready to receive
  events. `before_send` strips a few noisy patterns (intentional 4xx, expected
  abort errors during SSE shutdown).
- `bind_request_context` and `bind_user_context` give downstream code a single
  place to attach request/user metadata to BOTH log records and Sentry scope.

We deliberately avoid OpenTelemetry here — Sentry's own performance traces
give us enough visibility for the scale we're at, and adding OTel would mean
another collector to run.
"""
from __future__ import annotations

import logging
import sys
from typing import Any

import sentry_sdk
import structlog
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from structlog.contextvars import bind_contextvars, clear_contextvars

from app.config import Settings

# ── Sentry ─────────────────────────────────────────────────────────────────────

def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Filter events Sentry would otherwise capture but that aren't actionable."""
    exc_info = hint.get("exc_info")
    if exc_info:
        exc_type, _exc_value, _tb = exc_info
        name = getattr(exc_type, "__name__", "")
        # Client aborts during SSE streams are normal — user clicked Stop or
        # navigated away. They surface as ClientDisconnect / CancelledError and
        # would otherwise drown the error feed.
        if name in {"ClientDisconnect", "CancelledError", "ConnectionResetError"}:
            return None

    # Drop intentional 4xx HTTPExceptions — they're user errors, not bugs.
    # 401/403/404/422/429 are part of normal API contract; only 5xx is interesting.
    if "extra" in event and "status_code" in event["extra"]:
        status_code = event["extra"]["status_code"]
        if isinstance(status_code, int) and 400 <= status_code < 500:
            return None

    return event


def _init_sentry(settings: Settings) -> None:
    if not settings.sentry_dsn:
        return

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        release=settings.release_version or None,
        # Performance traces — keep low in prod to stay within free tier.
        traces_sample_rate=settings.sentry_traces_sample_rate,
        profiles_sample_rate=settings.sentry_profiles_sample_rate,
        # PII would include user emails in request bodies — we tag user_id
        # explicitly via scope so we don't need it.
        send_default_pii=False,
        attach_stacktrace=True,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
            AsyncioIntegration(),
            # Capture WARNING+ as breadcrumbs, ERROR+ as events.
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        before_send=_before_send,
    )


# ── Structlog ──────────────────────────────────────────────────────────────────

def _configure_structlog(settings: Settings) -> None:
    """Configure structlog AND stdlib logging to share a single pipeline."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.log_format == "json":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        # Pretty console output for `uv run uvicorn ...` locally.
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging through structlog's processor formatter so logs from
    # uvicorn / supabase / httpx come out in the same shape.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # Quiet libraries that log at INFO by default and add little signal.
    for noisy in ("httpx", "httpcore", "hpack", "supabase", "postgrest", "gotrue"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ── Public API ────────────────────────────────────────────────────────────────

def init_observability(settings: Settings) -> None:
    """Idempotent process-wide bootstrap. Safe to call from create_app()."""
    _configure_structlog(settings)
    _init_sentry(settings)


def bind_request_context(
    *,
    request_id: str,
    method: str,
    path: str,
) -> None:
    """Bind per-request fields. Called by RequestContextMiddleware."""
    bind_contextvars(request_id=request_id, http_method=method, http_path=path)
    sentry_sdk.set_tag("request_id", request_id)


def bind_user_context(*, user_id: str | None, org_id: str | None) -> None:
    """Bind user/org. Called by verify_jwt after a successful auth check."""
    if user_id:
        bind_contextvars(user_id=user_id)
        sentry_sdk.set_user({"id": user_id})
    if org_id:
        bind_contextvars(org_id=org_id)
        sentry_sdk.set_tag("org_id", org_id)


def clear_request_context() -> None:
    """Called from middleware's finally-block so contextvars don't leak across
    requests when uvicorn reuses a worker."""
    clear_contextvars()


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Preferred logger factory in app code. Plain `logging.getLogger(...)`
    also works — it's piped through the same handler — but structlog gives
    you key-value `.info("event", key=value)` syntax."""
    return structlog.stdlib.get_logger(name)
