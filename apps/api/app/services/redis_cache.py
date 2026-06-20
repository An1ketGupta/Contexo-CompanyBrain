"""Minimal Upstash Redis helper for opportunistic caching.

This is intentionally tiny: SET-with-TTL, GET, DEL, and a versioned-key
helper for invalidate-by-bump (V3 Day 5 #80). Anything fancier should
live in `services/rate_limit.py` (which already uses pipelines for the
sliding-window math) so the caching surface stays easy to reason about.

Why a separate module from `rate_limit.py`: rate-limit always fails open on
Upstash outages (the alternative is 503'ing /chat). Caches need the same
fail-open semantics — `cache_get` returns None on any error, `cache_set`
silently swallows. Callers MUST treat None as "cache miss, do the real work."

Versioned keys (V3 Day 5 #80)
─────────────────────────────
Pattern-DEL on Upstash REST is multi-roundtrip (SCAN+DEL) and gets throttled.
Instead we keep a per-org `cv:{org_id}` counter that callers fold into the
cache key (`docs:list:{org_id}:v{N}:…`). Invalidation = INCR the counter:
old keys become unreachable and self-expire on TTL. O(1) regardless of how
many cached payloads exist.

Used for:
  * Slack channel list (1h TTL, plain key — no versioning needed since the
    invalidator already knows the exact key)
  * Documents list cache (30s TTL, versioned)
  * Hybrid search results cache (60s TTL, versioned, chunk-id-only)
"""
from __future__ import annotations

import hashlib
import json
import random
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


# ── Versioned-key helpers (invalidate-by-bump) ─────────────────────────────

_CACHE_VERSION_PREFIX = "cv"
_CACHE_VERSION_TTL_SECONDS = 60 * 60 * 24 * 7  # 7d — far longer than any cache entry's TTL


async def get_cache_version(namespace: str) -> int:
    """Return the current version counter for `namespace` (e.g. an org_id).

    Returns 1 on cache miss (no row yet), Upstash being unreachable, or any
    transient error — every caller folds the version into a key, so a stale
    version just produces a deterministic cache miss the next read recovers.
    Never raises.
    """
    client = await _upstash()
    if client is None:
        return 1
    key = f"{_CACHE_VERSION_PREFIX}:{namespace}"
    try:
        resp = await client.post("/", json=["GET", key])
        resp.raise_for_status()
        raw = resp.json().get("result")
    except Exception as exc:
        log.warning("cache_version_get_failed", namespace=namespace, error=str(exc))
        return 1
    if raw is None:
        return 1
    try:
        return max(1, int(raw))
    except (ValueError, TypeError):
        return 1


async def bump_cache_version(namespace: str) -> int:
    """Increment and return the version counter for `namespace`.

    INCR is atomic in Redis, so concurrent invalidations from multiple workers
    just produce a strictly-increasing sequence — no lock needed. The EXPIRE
    refresh keeps the counter from drifting back to "missing" on long-quiet
    orgs (which would cause cached entries to suddenly become live again at
    version 1).

    Fail-open: on Upstash outage we return 0, the caller logs and moves on.
    Worst case: stale read for at most the entry's TTL.
    """
    client = await _upstash()
    if client is None:
        return 0
    key = f"{_CACHE_VERSION_PREFIX}:{namespace}"
    try:
        resp = await client.post(
            "/pipeline",
            json=[["INCR", key], ["EXPIRE", key, str(_CACHE_VERSION_TTL_SECONDS)]],
        )
        resp.raise_for_status()
        results = resp.json()
        new_val = int(results[0]["result"])
        return new_val
    except Exception as exc:
        log.warning("cache_version_bump_failed", namespace=namespace, error=str(exc))
        return 0


def hash_for_key(*parts: Any) -> str:
    """Stable short hash for use inside a cache key. Order-sensitive.

    Hash so we don't blow past Upstash's 512MB key-size limits with long
    query strings and never include user-controlled text verbatim in keys.
    """
    canonical = json.dumps(parts, default=str, sort_keys=True, separators=(",", ":"))
    return hashlib.blake2b(canonical.encode(), digest_size=10).hexdigest()


def jittered_ttl(base_ttl_seconds: int, *, jitter_pct: float = 0.1) -> int:
    """Add ±jitter_pct random spread to a TTL. Stampede protection.

    Without jitter, every entry created in a burst expires in the same second,
    causing a synchronized cache miss thundering herd. ±10% spreads the
    expirations over a few seconds.
    """
    if base_ttl_seconds <= 1:
        return base_ttl_seconds
    spread = int(base_ttl_seconds * jitter_pct)
    return base_ttl_seconds + random.randint(-spread, spread)
