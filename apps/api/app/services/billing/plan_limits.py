"""Single source of truth for plan-derived enforcement caps.

Every quota check in the app should route through here so quotas stay
consistent across the four enforcement points:

  * /chat              — monthly query budget (services/rate_limit.py)
  * /documents/upload  — per-org document count (routers/documents.py)
  * /invitations       — seat cap including pending invites (routers/invitations.py)
  * /settings/billing  — UI display of usage vs. limit (Next.js)

Day 6 put the underlying data in the `pricing_tiers` table and exposed it
via `app.services.billing.pricing.quota_for_plan()`. This module is a thin
adapter that:

  1. Promotes `UNLIMITED` (a giant int sentinel) to a plain Python `None`
     at the boundary — most callers prefer `if limit is None: ...` over
     `if limit >= UNLIMITED: ...`.
  2. Centralises the "what does free actually allow" decision. The
     pricing_tiers seed treats free as zero-quota (an org with no active
     subscription should not be able to do work). Some callers want to
     keep free orgs functional at a trial-level allowance — they read the
     `*_trial_*` helpers instead.

Why a wrapper rather than calling quota_for_plan() directly: enforcement
sites should not import the pricing internals (UNLIMITED, PlanQuota); they
get a small, named function whose intent reads at the call site.
"""
from __future__ import annotations

from app.services.billing.pricing import (
    UNLIMITED,
    is_unlimited,
    quota_for_plan,
)

# Trial allowance for orgs that haven't yet completed Checkout. Keeps brand-new
# signups usable on day one without giving them a perpetual free tier — the
# /settings/billing page is the supposed-to-be-obvious next step.
_FREE_TRIAL_QUERIES_PER_MONTH = 50
_FREE_TRIAL_DOCUMENTS = 10
_FREE_TRIAL_SEATS = 2


def _to_optional(value: int) -> int | None:
    return None if is_unlimited(value) else int(value)


# ── Monthly query budget (chat rate limiter) ─────────────────────────────────


def monthly_query_limit(plan: str | None) -> int | None:
    """Monthly chat-task allowance for a plan. None = unlimited.

    Free orgs get a small trial allowance rather than zero so a brand-new
    signup can use the product before Checkout; without this, the very
    first chat call from a new tenant would hit a 402 instead of
    converting them into a paid plan.
    """
    if not plan or plan == "free":
        return _FREE_TRIAL_QUERIES_PER_MONTH
    return _to_optional(quota_for_plan(plan).queries_monthly)


# ── Document upload cap ──────────────────────────────────────────────────────


def document_limit(plan: str | None) -> int | None:
    """Max documents an org may hold at once. None = unlimited.

    Same free-tier philosophy as queries: a few docs to evaluate the
    product, then they need a plan.
    """
    if not plan or plan == "free":
        return _FREE_TRIAL_DOCUMENTS
    return _to_optional(quota_for_plan(plan).documents)


# ── Seat cap (members + pending invites) ─────────────────────────────────────


def seat_limit(plan: str | None) -> int | None:
    """Max members + outstanding invites. None = unlimited."""
    if not plan or plan == "free":
        return _FREE_TRIAL_SEATS
    return _to_optional(quota_for_plan(plan).seats)


# Re-export for callers that need the sentinel directly (e.g., admin pages
# that want to render "Unlimited" rather than a number).
__all__ = [
    "UNLIMITED",
    "monthly_query_limit",
    "document_limit",
    "seat_limit",
]
