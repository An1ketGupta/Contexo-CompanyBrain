"""Read-only accessors for the `pricing_tiers` table.

Why a service module and not just a SQL query in the webhook handler:

  * The webhook handler hits price→plan lookup on every event. Caching it
    behind a TTL avoids a Supabase round-trip per webhook.
  * Two callers (webhook, /pricing marketing page, admin UI, future
    metered-billing path) all need the same data shape. Centralizing
    avoids three slightly-different schemas drifting apart.
  * Quota enforcement (Day 8+) reads `quota_*` from this module — the
    same source-of-truth the webhook uses to set `plan` on the org.

The cache is process-local and short (60s) so admin price edits roll out
quickly without a redeploy. For higher-traffic deployments a Redis-backed
cache would be the next step.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Final

from app.config import get_settings
from app.database import get_service_client
from app.observability import get_logger

log = get_logger(__name__)

# Sentinel for "no cap at this tier" (used by Business). Treating this as
# a literal constant rather than None makes the per-call quota check
# read as `usage < quota` after a single normalization step.
UNLIMITED: Final[int] = 2_147_483_647  # int32 max — never actually hit

_CACHE_TTL_SECONDS: Final[float] = 60.0


@dataclass(frozen=True, slots=True)
class PricingTier:
    plan: str
    interval: str  # 'month' | 'year'
    stripe_price_id: str
    stripe_product_id: str
    unit_amount_cents: int
    currency: str
    quota_documents: int  # UNLIMITED if NULL in DB
    quota_queries_monthly: int
    quota_seats: int
    is_active: bool


@dataclass(frozen=True, slots=True)
class PlanQuota:
    """Compact view of a plan's enforcement limits.

    Returned by `quota_for_plan()`. The `free` plan returns a zero-quota
    PlanQuota (any usage is over-limit) so the same callers don't need to
    special-case the no-subscription branch.
    """
    plan: str
    documents: int
    queries_monthly: int
    seats: int


# ── Cache ──────────────────────────────────────────────────────────────────

_lock = threading.Lock()
_cache: dict[str, PricingTier] = {}
_cache_loaded_at: float = 0.0
_cache_mode: str = ""


def _maybe_reload() -> None:
    global _cache, _cache_loaded_at, _cache_mode

    settings = get_settings()
    mode = settings.stripe_mode

    now = time.time()
    if (
        _cache
        and _cache_mode == mode
        and (now - _cache_loaded_at) < _CACHE_TTL_SECONDS
    ):
        return

    with _lock:
        # Double-checked locking — another thread may have refreshed
        # while we were waiting on the mutex.
        if (
            _cache
            and _cache_mode == mode
            and (time.time() - _cache_loaded_at) < _CACHE_TTL_SECONDS
        ):
            return

        svc = get_service_client()
        res = (
            svc.table("pricing_tiers")
            .select(
                "plan, interval, stripe_price_id, stripe_product_id, "
                "unit_amount_cents, currency, quota_documents, "
                "quota_queries_monthly, quota_seats, is_active"
            )
            .eq("stripe_mode", mode)
            .eq("is_active", True)
            .execute()
        )

        new_cache: dict[str, PricingTier] = {}
        for row in res.data or []:
            tier = PricingTier(
                plan=row["plan"],
                interval=row["interval"],
                stripe_price_id=row["stripe_price_id"],
                stripe_product_id=row["stripe_product_id"],
                unit_amount_cents=int(row["unit_amount_cents"]),
                currency=row["currency"],
                quota_documents=_or_unlimited(row.get("quota_documents")),
                quota_queries_monthly=_or_unlimited(row.get("quota_queries_monthly")),
                quota_seats=_or_unlimited(row.get("quota_seats")),
                is_active=bool(row["is_active"]),
            )
            new_cache[tier.stripe_price_id] = tier

        _cache = new_cache
        _cache_loaded_at = time.time()
        _cache_mode = mode


def _or_unlimited(value: int | None) -> int:
    return UNLIMITED if value is None else int(value)


# ── Public API ─────────────────────────────────────────────────────────────


def plan_for_price(stripe_price_id: str) -> str | None:
    """Map a Stripe Price ID to a plan name (or None when unknown).

    The webhook handler uses this on subscription events. Returning None
    rather than a default sentinel lets the handler decide whether the
    unknown price means a misconfiguration (log + alert) or a legitimate
    deprecated price (downgrade the org).
    """
    if not stripe_price_id:
        return None
    _maybe_reload()
    tier = _cache.get(stripe_price_id)
    return tier.plan if tier else None


def list_active_tiers() -> list[PricingTier]:
    """Return all active tiers for the configured stripe_mode.

    Sorted (plan, interval) so the marketing /pricing page renders a
    stable order.
    """
    _maybe_reload()
    return sorted(_cache.values(), key=lambda t: (t.plan, t.interval))


# Hardcoded fallback for `free` — the only plan that doesn't live in
# pricing_tiers. We give it zero quota so quota enforcement defaults to
# closed; orgs need an active subscription to get any usage allowance.
_FREE_PLAN_QUOTA: Final[PlanQuota] = PlanQuota(
    plan="free",
    documents=0,
    queries_monthly=0,
    seats=1,
)


def quota_for_plan(plan: str) -> PlanQuota:
    """Look up the quota for a plan name.

    Returns the `free` quota (zero usage) when the plan isn't recognized,
    so callers can use this without a branch on "did the org ever have a
    subscription". Use `is_unlimited(q.queries_monthly)` if you need to
    short-circuit the unlimited case.
    """
    if plan == "free" or not plan:
        return _FREE_PLAN_QUOTA

    _maybe_reload()
    # Multiple intervals (month + year) map to the same plan with the same
    # quotas — pick any active tier for this plan.
    for tier in _cache.values():
        if tier.plan == plan:
            return PlanQuota(
                plan=plan,
                documents=tier.quota_documents,
                queries_monthly=tier.quota_queries_monthly,
                seats=tier.quota_seats,
            )

    # Unknown plan = treat as free. Logged because this signals either a
    # stale row or a typo in a webhook handler — both deserve attention.
    log.warning("pricing_unknown_plan_fallback_to_free", plan=plan)
    return _FREE_PLAN_QUOTA


def is_unlimited(value: int) -> bool:
    return value >= UNLIMITED


def invalidate_cache() -> None:
    """Force-reload the cache on next access.

    Called from the seed script + admin tier-edit endpoint so changes take
    effect immediately without waiting for the TTL.
    """
    global _cache, _cache_loaded_at, _cache_mode
    with _lock:
        _cache = {}
        _cache_loaded_at = 0.0
        _cache_mode = ""
