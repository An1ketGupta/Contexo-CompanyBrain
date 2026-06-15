"""Minimal Upstash Redis helper for opportunistic caching.

This is intentionally tiny: SET-with-TTL and GET only. Anything fancier
should live in `services/rate_limit.py` (which already uses pipelines for
the sliding-window math) so the caching surface stays easy to reason about.

Why a separate module from `rate_limit.py`: rate-limit always fails open on
Upstash outages (the alternative is 503'ing /chat). Caches need the same
fail-open semantics — `cache_get` returns None on any error, `cache_set`
silently swallows. Callers MUST treat None as "cache miss, do the real work."

Used for the Slack channel list (1h TTL) so a chat with the picker open
doesn't fan into one conversations.list call per click — Slack's tier 2
rate limits hit fast on noisy workspaces.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import get_settings
from app.observability import get_logger

log = get_logger(__name__)

_client: httpx.AsyncClient | None = None


async def _upstash() -> httpx.AsyncClient | None:
    """Lazy singleton — None when Upstash isn't configured (local dev)."""
    global _client
    settings = get_settings()
    if not settings.upstash_redis_rest_url or not settings.upstash_redis_rest_token:
        return None
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=settings.upstash_redis_rest_url.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.upstash_redis_rest_token}"},
            timeout=httpx.Timeout(3.0, connect=2.0),
        )
    return _client


async def cache_get_json(key: str) -> Any | None:
    """Return the JSON-decoded value at `key`, or None on miss/error.

    Returning None on errors means callers don't need a separate try/except;
    a transient Upstash blip degrades to "cache miss" instead of throwing.
    """
    client = await _upstash()
    if client is None:
        return None
    try:
        resp = await client.post("/", json=["GET", key])
        resp.raise_for_status()
        raw = resp.json().get("result")
    except Exception as exc:
        log.warning("cache_get_failed", key=key, error=str(exc))
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        # Stale/poisoned entry from a prior schema — pretend it's a miss so
        # the caller writes a fresh one.
        return None


async def cache_set_json(key: str, value: Any, *, ttl_seconds: int) -> None:
    """SET with TTL. Silently no-ops if Upstash is unreachable."""
    client = await _upstash()
    if client is None:
        return
    try:
        payload = json.dumps(value, default=str)
        await client.post("/", json=["SET", key, payload, "EX", str(ttl_seconds)])
    except Exception as exc:
        log.warning("cache_set_failed", key=key, error=str(exc))


async def cache_delete(key: str) -> None:
    client = await _upstash()
    if client is None:
        return
    try:
        await client.post("/", json=["DEL", key])
    except Exception as exc:
        log.warning("cache_delete_failed", key=key, error=str(exc))
