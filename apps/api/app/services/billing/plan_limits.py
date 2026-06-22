"""Single source of truth for plan-derived enforcement caps.

Every quota check in the app should route through here so quotas stay
consistent across the four enforcement points:

  * /chat              — monthly query budget (services/rate_limit.py)
  * /documents/upload  — per-org document count (routers/documents.py)
  * /invitations       — seat cap including pending invites (routers/invitations.py)
  * /settings/billing  — UI display of usage vs. limit (Next.js)

Routers should not import the pricing internals (UNLIMITED, PlanQuota);
they get the named helpers below whose intent reads at the call site.
The `free` plan returns a trial-level allowance rather than zero so a
brand-new tenant can use the product before completing Checkout.
"""
from __future__ import annotations

from app.services.billing.pricing import (
    UNLIMITED,
    is_unlimited,
    quota_for_plan,
)

_FREE_TRIAL_QUERIES_PER_MONTH = 50
_FREE_TRIAL_DOCUMENTS = 10
_FREE_TRIAL_SEATS = 2


def to_optional(value: int | None) -> int | None:
    """Normalize a pricing-tier quota to `None` when it represents unlimited.

    Routers calling pricing_tiers directly (e.g., to render plan cards)
    use this so they don't have to import the `UNLIMITED` sentinel.
    """
    if value is None:
        return None
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
    return to_optional(quota_for_plan(plan).queries_monthly)


# ── Document upload cap ──────────────────────────────────────────────────────


def document_limit(plan: str | None) -> int | None:
    """Max documents an org may hold at once. None = unlimited.

    Same free-tier philosophy as queries: a few docs to evaluate the
    product, then they need a plan.
    """
    if not plan or plan == "free":
        return _FREE_TRIAL_DOCUMENTS
    return to_optional(quota_for_plan(plan).documents)


# ── Seat cap (members + pending invites) ─────────────────────────────────────


def seat_limit(plan: str | None) -> int | None:
    """Max members + outstanding invites. None = unlimited."""
    if not plan or plan == "free":
        return _FREE_TRIAL_SEATS
    return to_optional(quota_for_plan(plan).seats)


# Re-export for callers that need the sentinel directly (e.g., admin pages
# that want to render "Unlimited" rather than a number).
__all__ = [
    "UNLIMITED",
    "to_optional",
    "monthly_query_limit",
    "document_limit",
    "seat_limit",
]
