"""Stripe billing service module.

Layered like this so Day 7 (router + webhook) and Day 8 (UI quota wiring)
can drop into a stable surface:

  * client.py   — Stripe SDK initialization. Pinned API version, lazy init.
  * pricing.py  — pricing_tiers table accessor + plan/quota lookups.
  * quotas.py   — plan → quota dict (read through pricing.py).

Nothing here imports the Stripe SDK at module import time. Day 6's scope
is foundational — the SDK is only touched when someone actually calls
`get_stripe_client()`. Importing this package on a deploy that hasn't set
STRIPE_SECRET_KEY yet is therefore safe.
"""
from app.services.billing.client import (
    StripeNotConfigured,
    get_stripe_client,
)
from app.services.billing.pricing import (
    UNLIMITED,
    PlanQuota,
    PricingTier,
    list_active_tiers,
    plan_for_price,
    quota_for_plan,
)

__all__ = [
    "StripeNotConfigured",
    "get_stripe_client",
    "PricingTier",
    "PlanQuota",
    "UNLIMITED",
    "plan_for_price",
    "quota_for_plan",
    "list_active_tiers",
]
