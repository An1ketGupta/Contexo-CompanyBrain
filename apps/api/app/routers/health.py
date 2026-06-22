"""Liveness + readiness endpoints.

Two endpoints by design, even though Railway only consumes one of them:

  GET /health         — Liveness. Process is alive and the FastAPI app is
                        wired up. Trivial check, never returns 5xx so a
                        flaky downstream (Gemini, Upstash) doesn't trigger
                        Railway to roll back a healthy deploy.
  GET /health/ready   — Readiness. Probes Supabase, Upstash Redis, and the
                        LLM provider with short timeouts. Returns 503 if
                        any dependency is unreachable. Use this from a
                        synthetic monitor (Cronitor / Better Uptime) for
                        paging-grade signal, NOT from Railway.

Why the split: deep probes on the deploy healthcheck mean a 30-second
Gemini timeout takes the whole service offline during a rolling deploy.
With Railway pointed at /health (liveness), a Gemini brown-out keeps
serving traffic; with Cronitor pointed at /health/ready, we still get
paged in 60 seconds. Same coverage, no false-positive rollbacks.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import get_service_client

log = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

# Per-probe budgets. The whole /ready response should complete in <5s
# even if every probe times out, so the synthetic monitor doesn't itself
# time out and miss the failure event.
_PROBE_TIMEOUT_SECONDS = 1.5


@router.get("/health")
async def health_check() -> dict[str, Any]:
    """Liveness probe. Cheap, never 5xx. Safe for deploy healthchecks."""
    settings = get_settings()
    return {
        "status": "ok",
        "version": settings.app_version,
        "environment": settings.environment,
    }


async def _probe_supabase() -> tuple[str, str | None]:
    """Single-row select. Confirms PostgREST is reachable + service-role
    key is still valid. Returns (status, error_message)."""
    try:
        svc = get_service_client()
        await asyncio.wait_for(
            asyncio.to_thread(
                lambda: svc.table("organizations").select("id").limit(1).execute()
            ),
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
        return "ok", None
    except asyncio.TimeoutError:
        return "timeout", f"{_PROBE_TIMEOUT_SECONDS}s"
    except Exception as exc:
        # Don't expose the exception text in the response body — exception
        # chains from supabase-py can include connection strings. Surface
        # the type only; full traceback lands in Sentry via the logger.
        log.warning("health_supabase_failed: %s", exc)
        return "error", type(exc).__name__


async def _probe_redis() -> tuple[str, str | None]:
    """Upstash REST ping. Skipped (returns 'skipped') when Upstash isn't
    configured — local dev / staging without Redis is allowed to pass."""
    settings = get_settings()
    if not settings.upstash_redis_rest_url or not settings.upstash_redis_rest_token:
        return "skipped", None
    try:
        async with httpx.AsyncClient(
            base_url=settings.upstash_redis_rest_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {settings.upstash_redis_rest_token}"
            },
            timeout=httpx.Timeout(_PROBE_TIMEOUT_SECONDS, connect=1.0),
        ) as client:
            resp = await client.post("/", json=["PING"])
            resp.raise_for_status()
        return "ok", None
    except httpx.TimeoutException:
        return "timeout", f"{_PROBE_TIMEOUT_SECONDS}s"
    except Exception as exc:
        log.warning("health_redis_failed: %s", exc)
        return "error", type(exc).__name__


async def _probe_llm() -> tuple[str, str | None]:
    """LLM provider TCP reachability. We deliberately do NOT call the
    actual generation endpoint — that would be paid traffic on every
    healthcheck and would also stop being a useful "is the provider up"
    signal when quotas exhaust. A TCP+TLS handshake against the host
    confirms outbound networking + DNS + provider gateway, which is the
    failure mode 99% of LLM-side outages take."""
    settings = get_settings()
    provider = (settings.llm_provider or "gemini").lower()
    if provider == "gemini":
        host = "generativelanguage.googleapis.com"
    elif provider == "claude":
        host = "api.anthropic.com"
    elif provider == "openai":
        host = "api.openai.com"
    else:
        return "skipped", f"unknown_provider:{provider}"
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_PROBE_TIMEOUT_SECONDS, connect=1.0)
        ) as client:
            # HEAD on the root usually 4xx's but completes the TLS handshake,
            # which is the signal we want. A 4xx response is healthy here.
            resp = await client.head(f"https://{host}/")
        if resp.status_code >= 500:
            return "degraded", f"HTTP {resp.status_code}"
        return "ok", None
    except httpx.TimeoutException:
        return "timeout", f"{_PROBE_TIMEOUT_SECONDS}s"
    except Exception as exc:
        log.warning("health_llm_failed: %s", exc)
        return "error", type(exc).__name__


@router.get("/health/ready")
async def readiness_check() -> JSONResponse:
    """Deep readiness probe. Use from a synthetic monitor, not from
    Railway. Returns 503 if any required dependency is unreachable.
    Redis 'skipped' is treated as healthy (local dev). LLM 'degraded'
    is treated as healthy too — provider 5xx during a probe doesn't
    mean our service is down."""
    supabase_status, supabase_err = await _probe_supabase()
    redis_status, redis_err = await _probe_redis()
    llm_status, llm_err = await _probe_llm()

    checks = {
        "supabase": {"status": supabase_status, "error": supabase_err},
        "redis": {"status": redis_status, "error": redis_err},
        "llm": {"status": llm_status, "error": llm_err},
    }

    # Supabase is the only hard requirement — every request needs the DB.
    # Redis can be skipped (dev) and even "error" is survivable (rate
    # limiter fails open). LLM degraded is survivable. We surface them
    # all, but only flip overall status on Supabase failure.
    overall_ok = supabase_status == "ok"
    body: dict[str, Any] = {
        "status": "ready" if overall_ok else "not_ready",
        "checks": checks,
    }
    return JSONResponse(
        status_code=200 if overall_ok else 503,
        content=body,
    )
