"""Stripe products/prices bootstrap — Production Roadmap Day 6.

Run-once-per-environment script that:

  1. Creates (or finds) one Product per plan in Stripe: Starter, Team,
     Business.
  2. Creates (or finds) Monthly + Annual Prices for each product.
  3. Upserts a `pricing_tiers` row for every (plan × interval) pair so the
     webhook handler and /pricing page can look up the mapping.

Why a script and not a migration:
  - Stripe creates IDs server-side; we can't put `price_xxx` literals in
    a SQL migration that runs against any account.
  - Stripe Test mode and Live mode have entirely separate ID namespaces.
    The script reads STRIPE_MODE and writes to the matching pricing_tiers
    rows — same code, different IDs, no manual env-var swap.

Idempotency:
  - Products are matched by metadata.plan tag, not by name.
  - Prices are matched by (product, interval, amount). A second run won't
    create duplicates.

Usage:
  STRIPE_MODE=test STRIPE_SECRET_KEY=sk_test_... uv run python -m scripts.stripe_seed
  STRIPE_MODE=live STRIPE_SECRET_KEY=sk_live_... uv run python -m scripts.stripe_seed --confirm-live

Run from apps/api/ so the relative `scripts/` import path works.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Any, Iterable

import stripe  # type: ignore[import-untyped]

from app.config import get_settings
from app.database import get_service_client

# ── Canonical price list ────────────────────────────────────────────────────
# Single source of truth for the seed. Quotas here become the
# pricing_tiers.quota_* columns; the webhook handler reads them via the
# pricing service module.
#
# Annual prices reflect the ~20% discount from PRICING.md (12× monthly ×0.8).
# Currency is locked to USD pending a tax-collection decision.


@dataclass(frozen=True)
class PlanSpec:
    plan: str
    product_name: str
    product_description: str
    monthly_cents: int
    annual_cents: int
    quota_documents: int | None  # None = unlimited at this tier
    quota_queries_monthly: int | None
    quota_seats: int | None


PLANS: tuple[PlanSpec, ...] = (
    PlanSpec(
        plan="starter",
        product_name="Contexo — Starter",
        product_description="For small teams getting started with company-context AI.",
        monthly_cents=6900,    # $69.00/mo
        annual_cents=66500,    # $665.00/yr (~20% off $828.00)
        quota_documents=100,
        quota_queries_monthly=500,
        quota_seats=10,
    ),
    PlanSpec(
        plan="team",
        product_name="Contexo — Team",
        product_description="For growing teams that need shared knowledge + collaboration.",
        monthly_cents=14900,   # $149.00/mo
        annual_cents=144000,   # $1,440.00/yr (~20% off $1,548)
        quota_documents=1000,
        quota_queries_monthly=3000,
        quota_seats=30,
    ),
    PlanSpec(
        plan="business",
        product_name="Contexo — Business",
        product_description="Unlimited usage, all integrations, priority support.",
        monthly_cents=34900,   # $349.00/mo
        annual_cents=335000,   # $3,350.00/yr (~20% off $4,296)
        quota_documents=None,
        quota_queries_monthly=None,
        quota_seats=None,
    ),
)


# ── Stripe object helpers ───────────────────────────────────────────────────


def _find_product_by_plan(plan: str) -> dict[str, Any] | None:
    # search() requires the Search API to be enabled (it is, by default,
    # on every Stripe account). Falls back to list() iteration in the
    # unlikely event search is unavailable.
    try:
        result = stripe.Product.search(query=f"metadata['plan']:'{plan}'")
        if result and result.data:
            return result.data[0]
    except Exception as exc:
        print(f"  (Product.search failed: {exc}, falling back to list)")

    for product in stripe.Product.list(limit=100).auto_paging_iter():
        meta = product.to_dict().get("metadata") or {}
        if meta.get("plan") == plan:
            return product
    return None


def _ensure_product(spec: PlanSpec) -> dict[str, Any]:
    existing = _find_product_by_plan(spec.plan)
    if existing:
        # Repair drift in name/description if the dashboard was edited.
        needs_update = (
            existing.name != spec.product_name
            or (existing.description or "") != spec.product_description
        )
        if needs_update:
            print(f"  Updating product {existing.id} for plan={spec.plan}")
            return stripe.Product.modify(
                existing.id,
                name=spec.product_name,
                description=spec.product_description,
                metadata={"plan": spec.plan},
            )
        return existing

    print(f"  Creating product for plan={spec.plan}")
    return stripe.Product.create(
        name=spec.product_name,
        description=spec.product_description,
        metadata={"plan": spec.plan},
    )


def _find_price(product_id: str, interval: str, amount_cents: int) -> dict[str, Any] | None:
    """Return a matching active price if one exists.

    Stripe prices are immutable; if the amount or interval needs to change
    we create a new price and deactivate the old one. Matching here is on
    the active set so deactivated prices don't shadow a recreation.
    """
    for price in stripe.Price.list(
        product=product_id, active=True, limit=100
    ).auto_paging_iter():
        if (
            price.unit_amount == amount_cents
            and price.recurring
            and price.recurring.interval == interval
            and price.currency == "usd"
        ):
            return price
    return None


def _ensure_price(
    *, product_id: str, plan: str, interval: str, amount_cents: int
) -> dict[str, Any]:
    existing = _find_price(product_id, interval, amount_cents)
    if existing:
        return existing

    print(f"  Creating {interval}ly price ${amount_cents/100:.2f} for plan={plan}")
    return stripe.Price.create(
        product=product_id,
        unit_amount=amount_cents,
        currency="usd",
        recurring={"interval": interval},
        metadata={"plan": plan, "interval": interval},
    )


# ── pricing_tiers upsert ────────────────────────────────────────────────────


def _upsert_tier(
    *,
    plan: str,
    interval: str,
    stripe_mode: str,
    stripe_product_id: str,
    stripe_price_id: str,
    unit_amount_cents: int,
    quota_documents: int | None,
    quota_queries_monthly: int | None,
    quota_seats: int | None,
) -> None:
    svc = get_service_client()
    row = {
        "plan": plan,
        "interval": interval,
        "stripe_mode": stripe_mode,
        "stripe_product_id": stripe_product_id,
        "stripe_price_id": stripe_price_id,
        "unit_amount_cents": unit_amount_cents,
        "currency": "usd",
        "quota_documents": quota_documents,
        "quota_queries_monthly": quota_queries_monthly,
        "quota_seats": quota_seats,
        "is_active": True,
        "updated_at": "now()",
    }
    # supabase-py supports upsert via on_conflict. Pricing_tiers has a
    # unique index on (stripe_price_id, stripe_mode) — that's the conflict
    # target.
    svc.table("pricing_tiers").upsert(
        row,
        on_conflict="stripe_price_id,stripe_mode",
    ).execute()


# ── Entry point ─────────────────────────────────────────────────────────────


def _confirm_live_mode(args: argparse.Namespace, mode: str) -> None:
    if mode == "live" and not args.confirm_live:
        print(
            "Refusing to run against Stripe LIVE mode without --confirm-live.\n"
            "Re-run with --confirm-live once you've double-checked the env.",
            file=sys.stderr,
        )
        sys.exit(2)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed Stripe products/prices and pricing_tiers."
    )
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Required to run against Stripe LIVE mode.",
    )
    parser.add_argument(
        "--print-env",
        action="store_true",
        help="After seeding, print STRIPE_PRICE_* env entries for legacy consumers.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    settings = get_settings()
    if not settings.stripe_secret_key:
        print("STRIPE_SECRET_KEY is not set; cannot continue.", file=sys.stderr)
        return 2

    stripe.api_key = settings.stripe_secret_key
    stripe.api_version = settings.stripe_api_version

    mode = settings.stripe_mode
    _confirm_live_mode(args, mode)

    if not mode == ("live" if settings.stripe_secret_key.startswith("sk_live_") else "test"):
        print(
            "WARNING: STRIPE_MODE and STRIPE_SECRET_KEY prefix don't match. "
            "Continuing — but double-check this is intentional.",
            file=sys.stderr,
        )

    print(f"Seeding Stripe in mode={mode!r} with API version {stripe.api_version}")

    env_lines: list[str] = []
    for spec in PLANS:
        print(f"\nPlan: {spec.plan}")
        product = _ensure_product(spec)

        for interval, amount in (("month", spec.monthly_cents), ("year", spec.annual_cents)):
            price = _ensure_price(
                product_id=product.id,
                plan=spec.plan,
                interval=interval,
                amount_cents=amount,
            )
            _upsert_tier(
                plan=spec.plan,
                interval=interval,
                stripe_mode=mode,
                stripe_product_id=product.id,
                stripe_price_id=price.id,
                unit_amount_cents=amount,
                quota_documents=spec.quota_documents,
                quota_queries_monthly=spec.quota_queries_monthly,
                quota_seats=spec.quota_seats,
            )
            env_lines.append(
                f"STRIPE_PRICE_{spec.plan.upper()}_{interval.upper()}LY={price.id}"
            )

    # Bust the pricing cache so the next request reads the fresh rows.
    try:
        from app.services.billing.pricing import invalidate_cache

        invalidate_cache()
    except Exception:
        pass

    print("\nDone. pricing_tiers rows upserted.")
    if args.print_env:
        print(
            "\n# Optional — env-var form for legacy consumers (the webhook "
            "handler reads pricing_tiers directly, so this is informational):"
        )
        for line in env_lines:
            print(line)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
