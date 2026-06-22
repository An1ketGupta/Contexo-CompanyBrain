"""Two-tier rate limiting against Upstash Redis (REST).

We enforce TWO independent budgets on every chat call:

    1. Per-user sliding window  → DOS protection. Catches runaway scripts.
       Sliding-window in Redis sorted-set. ~20 req/min/user by default.
    2. Per-org monthly counter   → enforces pricing plan. INCR + EXPIRE,
       keyed by calendar month so it resets on the 1st without a cron job.

Why two: a single per-org/min limit was lenient enough that one buggy script
inside a tenant could burn the whole org's monthly quota in 30 seconds. And
a single monthly limit means we'd 429 a normal user at the end of the month
when the actual problem was that their teammate ran a loop yesterday.

Order of checks: per-user first (cheap fail-fast for abuse), then per-org
(authoritative budget). Both fail-open if Upstash is unreachable — we'd
rather serve real users than 503 the whole product on an infra blip.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import httpx

from app.config import get_settings
from app.errors import QuotaExceeded, RateLimited
from app.observability import get_logger

log = get_logger(__name__)


# ── Plan budgets ─────────────────────────────────────────────────────────────
#
# Day 7+: the source of truth lives in `pricing_tiers` (seeded by
# scripts/stripe_seed.py) and is read through `services.billing.plan_limits`.
# This file used to ship a hardcoded dict that drifted from the table
# (e.g., it knew about 'growth' but the table was renamed to 'team') —
# routing the read through plan_limits eliminates that class of bug.


def monthly_budget_for(plan: str | None) -> int | None:
    """Backwards-compatible alias for `monthly_query_limit`.

    Kept because it's referenced from `get_usage_snapshot` and the public
    API rate limiter; switching them to the new name is a Day 9 cleanup.
    Returns None for unlimited (Business / unknown unlimited plan).
    """
    from app.services.billing.plan_limits import monthly_query_limit

    return monthly_query_limit(plan)


# ── Upstash REST client ──────────────────────────────────────────────────────

_client: httpx.AsyncClient | None = None


async def _upstash() -> httpx.AsyncClient | None:
    """Lazy singleton HTTP client to Upstash. None if Upstash isn't configured
    (local dev) — callers must treat that as fail-open."""
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


# ── Sliding-window per-user limiter (DOS protection) ─────────────────────────

@dataclass(frozen=True)
class SlidingWindowResult:
    allowed: bool
    remaining: int
    reset_seconds: int


async def _sliding_window_check(
    *, namespace: str, identifier: str, limit: int, window_seconds: int
) -> SlidingWindowResult:
    client = await _upstash()
    if client is None:
        return SlidingWindowResult(allowed=True, remaining=limit, reset_seconds=0)

    key = f"rl:{namespace}:{identifier}"
    now_ms = int(time.time() * 1000)
    window_ms = window_seconds * 1000
    cutoff = now_ms - window_ms
    member = f"{now_ms}-{uuid.uuid4().hex[:8]}"

    # Pipelined: GC expired entries → count → reserve our slot → set TTL.
    # If we end up over-budget we ZREM the slot below so a denied call doesn't
    # consume window capacity.
    pipeline = [
        ["ZREMRANGEBYSCORE", key, "0", str(cutoff)],
        ["ZCARD", key],
        ["ZADD", key, str(now_ms), member],
        ["EXPIRE", key, str(window_seconds)],
    ]

    try:
        resp = await client.post("/pipeline", json=pipeline)
        resp.raise_for_status()
        results = resp.json()
    except Exception as exc:
        log.warning("ratelimit_upstash_unavailable", error=str(exc), tier="per_user")
        return SlidingWindowResult(allowed=True, remaining=limit, reset_seconds=0)

    try:
        count_before = int(results[1].get("result", 0))
    except (KeyError, TypeError, ValueError, IndexError):
        log.warning("ratelimit_upstash_bad_shape", results=results)
        return SlidingWindowResult(allowed=True, remaining=limit, reset_seconds=0)

    if count_before >= limit:
        # Roll back our slot — the call is denied, so it shouldn't shift the window.
        try:
            await client.post("/", json=["ZREM", key, member])
        except Exception:
            pass
        return SlidingWindowResult(allowed=False, remaining=0, reset_seconds=window_seconds)

    remaining = max(limit - (count_before + 1), 0)
    return SlidingWindowResult(allowed=True, remaining=remaining, reset_seconds=window_seconds)


# ── Monthly counter per org (plan enforcement) ───────────────────────────────

@dataclass(frozen=True)
class MonthlyCounterResult:
    allowed: bool
    used: int
    limit: int | None       # None = unlimited
    seconds_until_reset: int


def _month_key(now: datetime | None = None) -> str:
    n = now or datetime.now(UTC)
    return f"{n.year}{n.month:02d}"


def _seconds_until_next_month(now: datetime | None = None) -> int:
    n = now or datetime.now(UTC)
    year, month = (n.year + 1, 1) if n.month == 12 else (n.year, n.month + 1)
    next_month = datetime(year, month, 1, tzinfo=UTC)
    return max(int((next_month - n).total_seconds()), 1)


async def _monthly_check_and_increment(
    *, org_id: str, limit: int | None
) -> MonthlyCounterResult:
    """INCR a month-scoped key, EXPIRE on first hit. If we exceed the budget,
    DECR to release the slot so the count we report is accurate."""
    if limit is None:
        # Unlimited plan — skip the round-trip entirely.
        return MonthlyCounterResult(
            allowed=True, used=0, limit=None, seconds_until_reset=0
        )

    client = await _upstash()
    if client is None:
        # Fail-open: if Upstash is down we can't enforce quotas, so we don't.
        return MonthlyCounterResult(
            allowed=True, used=0, limit=limit, seconds_until_reset=0
        )

    key = f"quota:chat:{org_id}:{_month_key()}"
    ttl = _seconds_until_next_month()

    try:
        resp = await client.post(
            "/pipeline",
            json=[
                ["INCR", key],
                ["EXPIRE", key, str(ttl), "NX"],  # only set TTL on first INCR
            ],
        )
        resp.raise_for_status()
        results = resp.json()
        used = int(results[0].get("result", 0))
    except Exception as exc:
        log.warning("ratelimit_upstash_unavailable", error=str(exc), tier="monthly")
        return MonthlyCounterResult(
            allowed=True, used=0, limit=limit, seconds_until_reset=0
        )

    if used > limit:
        # Over budget — give the slot back so `used` reported to other callers
        # stays accurate, and so the very next millisecond we don't double-decrement.
        try:
            await client.post("/", json=["DECR", key])
        except Exception:
            pass
        return MonthlyCounterResult(
            allowed=False, used=limit, limit=limit, seconds_until_reset=ttl
        )

    return MonthlyCounterResult(
        allowed=True, used=used, limit=limit, seconds_until_reset=ttl
    )


# ── Read-only usage probe (powers the sidebar meter) ─────────────────────────


@dataclass(frozen=True)
class UsageSnapshot:
    """Read-only view of the current month's chat quota for an org.

    `limit=None` → unlimited (business plan). `source="redis"` when the count
    came from Upstash; `"local"` when Upstash was unreachable and we returned
    a best-effort zero so the UI still renders.
    """

    plan: str
    used: int
    limit: int | None
    seconds_until_reset: int
    source: Literal["redis", "local"]


async def get_usage_snapshot(org_id: str) -> UsageSnapshot:
    """Read the live monthly counter without incrementing it.

    Shares the same Upstash key as the limiter (`quota:chat:{org}:{YYYYMM}`)
    so the meter never disagrees with the gate that actually enforces the
    plan. If Upstash is down, we surface `source="local"` and a zero count
    rather than failing — the UI degrades to a placeholder, but the user
    still sees their plan and reset window.
    """
    plan = await get_org_plan(org_id)
    limit = monthly_budget_for(plan)
    ttl = _seconds_until_next_month()

    client = await _upstash()
    if client is None:
        return UsageSnapshot(
            plan=plan, used=0, limit=limit, seconds_until_reset=ttl, source="local"
        )

    key = f"quota:chat:{org_id}:{_month_key()}"
    try:
        resp = await client.post("/", json=["GET", key])
        resp.raise_for_status()
        result = resp.json().get("result")
        used = int(result) if result is not None else 0
    except Exception as exc:
        log.warning("usage_snapshot_upstash_failed", org_id=org_id, error=str(exc))
        return UsageSnapshot(
            plan=plan, used=0, limit=limit, seconds_until_reset=ttl, source="local"
        )

    return UsageSnapshot(
        plan=plan, used=used, limit=limit, seconds_until_reset=ttl, source="redis"
    )


# ── Public API ────────────────────────────────────────────────────────────────

async def get_org_plan(org_id: str) -> str:
    """Read the org's current plan. Service-role lookup since this runs
    inside the request handler before any RLS-scoped client work."""
    import asyncio

    from app.database import get_service_client
    try:
        result = await asyncio.to_thread(
            lambda: get_service_client()
            .table("organizations")
            .select("plan")
            .eq("id", org_id)
            .maybe_single()
            .execute()
        )
        plan = (result.data or {}).get("plan") if result else None
        return plan or "free"
    except Exception as exc:
        log.warning("plan_lookup_failed", org_id=org_id, error=str(exc))
        # Fail closed-ish: treat as the smallest plan so we don't accidentally
        # give a free-tier user unlimited quota on a transient lookup error.
        return "free"


async def enforce_chat_quota(*, user_id: str, org_id: str) -> None:
    """One call to gate /chat and /chat/stream. Raises RateLimited or
    QuotaExceeded — both carry our error envelope + Retry-After when relevant.

    Caller invariant: both ids are non-empty (verify_jwt guarantees it).
    """
    settings = get_settings()

    # Tier 1 — per-user/minute. Cheap and catches the most common foot-gun.
    user_result = await _sliding_window_check(
        namespace="chat:user",
        identifier=user_id,
        limit=settings.rate_limit_chat_per_user_per_minute,
        window_seconds=60,
    )
    if not user_result.allowed:
        raise RateLimited(
            message=(
                f"You're sending requests too quickly. "
                f"Wait {user_result.reset_seconds} seconds and try again."
            ),
            retry_after=user_result.reset_seconds,
        )

    # Tier 2 — per-org/month. Authoritative budget for the pricing plan.
    plan = await get_org_plan(org_id)
    budget = monthly_budget_for(plan)
    monthly = await _monthly_check_and_increment(org_id=org_id, limit=budget)

    # Quota emails are fire-and-forget; an enqueue failure must NOT block chat.
    if monthly.limit is not None:
        # 80% warning: once per calendar month per user via dedupe_key=YYYY-MM.
        if monthly.allowed and monthly.used >= int(monthly.limit * 0.8):
            try:
                await _enqueue_quota_email(
                    event_type="quota_warning",
                    user_id=user_id,
                    org_id=org_id,
                    plan=plan,
                    used=monthly.used,
                    limit=monthly.limit,
                    seconds_until_reset=monthly.seconds_until_reset,
                )
            except Exception as exc:
                log.warning("quota_warning_dispatch_failed", error=str(exc))

    if not monthly.allowed:
        # 402 with a specific code so the frontend can route to /pricing
        # instead of showing a generic "try again later" toast.
        days_left = max(monthly.seconds_until_reset // 86_400, 1)
        try:
            await _enqueue_quota_email(
                event_type="quota_exceeded",
                user_id=user_id,
                org_id=org_id,
                plan=plan,
                used=monthly.used,
                limit=monthly.limit or 0,
                seconds_until_reset=monthly.seconds_until_reset,
            )
        except Exception as exc:
            log.warning("quota_exceeded_dispatch_failed", error=str(exc))

        raise QuotaExceeded(
            message=(
                f"You've used all {monthly.limit} AI tasks on your {plan} plan "
                f"this month. Resets in {days_left} day{'s' if days_left != 1 else ''}, "
                "or upgrade to keep going."
            ),
            retry_after=monthly.seconds_until_reset,
            details={
                "plan": plan,
                "used": monthly.used,
                "limit": monthly.limit,
                "resets_in_seconds": monthly.seconds_until_reset,
            },
        )


async def _enqueue_quota_email(
    *,
    event_type: str,
    user_id: str,
    org_id: str,
    plan: str,
    used: int,
    limit: int,
    seconds_until_reset: int,
) -> None:
    """Best-effort dispatch. Resolves the recipient's email server-side so we
    don't trust the JWT to be fresh. Dedupes per calendar month."""
    from datetime import datetime

    from app.config import get_settings as _gs
    from app.database import get_service_client as _svc
    from app.services.email import send_email_event

    settings = _gs()
    svc = _svc()

    import asyncio as _aio

    try:
        au = await _aio.to_thread(lambda: svc.auth.admin.get_user_by_id(user_id))
        email = getattr(getattr(au, "user", None), "email", None)
    except Exception:
        email = None
    if not email:
        return

    month_key = datetime.now(UTC).strftime("%Y-%m")
    billing_url = f"{settings.app_url.rstrip('/')}/settings/billing"

    if event_type == "quota_warning":
        data = {"used": used, "limit": limit, "plan": plan, "billing_url": billing_url}
    else:  # quota_exceeded
        days = max(seconds_until_reset // 86_400, 1)
        data = {
            "limit": limit,
            "plan": plan,
            "billing_url": billing_url,
            "resets_in_days": days,
        }

    await send_email_event(
        event_type=event_type,  # type: ignore[arg-type]
        to=email,
        user_id=user_id,
        org_id=org_id,
        dedupe_key=month_key,
        data=data,
    )
