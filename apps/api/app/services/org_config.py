"""Per-org configuration cache.

Today the only knob is `ai_instructions` (Day 9 / #67) — admin-supplied
text prepended to the LLM system prompt on every chat turn. We expect:

  * Hot-path read on every chat call (must stay <1ms).
  * Cold-path write from settings UI (a few times per org per year).

So we keep an in-process TTL cache keyed by org_id. 60s of staleness on
edit is fine — admins know "saved" doesn't mean "instant" for an LLM
config, and any process that handled the PATCH invalidates its own entry
immediately for the writer's next turn.

Cross-process consistency is best-effort: another worker process may
still serve a stale value for up to 60s. That's acceptable for the
write rate we expect; revisit if/when an org reports a confusing lag.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from app.database import get_service_client
from app.observability import get_logger

log = get_logger(__name__)

# Tunable. Short enough that an edit propagates in human time, long enough
# that a single chatty user can't melt the DB with config reads.
ORG_CONFIG_TTL_SECONDS = 60.0

# Hard cap on instructions length — mirrored by the API validator. Acts as
# defence-in-depth against a malformed DB row leaking unbounded context into
# the system prompt.
INSTRUCTIONS_MAX_CHARS = 500


@dataclass(frozen=True)
class OrgConfig:
    org_id: str
    ai_instructions: str | None
    fetched_at: float


_cache: dict[str, OrgConfig] = {}
_lock = asyncio.Lock()


def invalidate(org_id: str) -> None:
    """Drop the cached entry for a single org (called after writes)."""
    _cache.pop(org_id, None)


def invalidate_all() -> None:
    """Test/dev hook — wipe the entire cache."""
    _cache.clear()


async def get_org_config(org_id: str) -> OrgConfig:
    now = time.monotonic()
    cached = _cache.get(org_id)
    if cached and (now - cached.fetched_at) < ORG_CONFIG_TTL_SECONDS:
        return cached

    # Coalesce concurrent misses so a thundering-herd of chat requests on a
    # cold org_id doesn't fan out into N parallel DB calls.
    async with _lock:
        cached = _cache.get(org_id)
        if cached and (time.monotonic() - cached.fetched_at) < ORG_CONFIG_TTL_SECONDS:
            return cached

        svc = get_service_client()
        try:
            result = await asyncio.to_thread(
                lambda: svc.table("organizations")
                .select("ai_instructions")
                .eq("id", org_id)
                .maybe_single()
                .execute()
            )
            row = (result.data or {}) if result else {}
        except Exception as exc:
            # Fail open — we'd rather chat without org context than block on
            # a transient DB blip. Don't poison the cache with the failure.
            log.warning("org_config_fetch_failed", org_id=org_id, error=str(exc))
            return OrgConfig(org_id=org_id, ai_instructions=None, fetched_at=now)

        raw = (row.get("ai_instructions") or "").strip()
        instructions = raw[:INSTRUCTIONS_MAX_CHARS] or None
        cfg = OrgConfig(org_id=org_id, ai_instructions=instructions, fetched_at=time.monotonic())
        _cache[org_id] = cfg
        return cfg


async def get_org_instructions(org_id: str) -> str | None:
    """Convenience: just the instructions string (or None)."""
    cfg = await get_org_config(org_id)
    return cfg.ai_instructions
