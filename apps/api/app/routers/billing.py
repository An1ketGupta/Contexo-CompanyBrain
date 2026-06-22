"""Stripe billing surface — Day 7 of the Production Roadmap.

Exposes the four endpoints the frontend needs:

    GET  /billing/plans            — public read of pricing_tiers for /pricing & /settings/billing
    GET  /billing/status           — current org plan/status/period_end (authed)
    POST /billing/checkout-session — start a Stripe Checkout for an upgrade (admin only)
    POST /billing/portal-session   — open the Stripe customer portal (admin only)

Design notes worth carrying forward:

  * **Price IDs come from `pricing_tiers`, not env vars.** Day 6 invested in
    that table specifically so the Test→Live cutover is a single row swap.
    Reading env vars here would re-introduce the drift this design avoided.

  * **Stripe customer creation uses an idempotency key.** A flaky network
    or a user double-clicking "Upgrade" would otherwise mint two Stripe
    customers for the same org. The customer-creation path is the only
    Stripe call here that's not naturally idempotent, so we belt-and-brace
    it. We also persist the customer id BEFORE creating the Checkout
    session — a crash between the two would otherwise orphan the customer.

  * **No free plan in Checkout.** Free is the no-subscription state; the
    only way to land there is `customer.subscription.deleted`. Rejecting
    `plan='free'` at the boundary keeps the webhook handler's invariants
    simple.

  * **Admin-only gate.** Only `users.role = 'admin'` can change billing.
    Members hitting these endpoints get a clear 403 instead of a confusing
    Stripe error after a Checkout redirect.
"""
from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.config import get_settings
from app.database import get_service_client
from app.errors import NoOrganization
from app.observability import get_logger
from app.services.billing import (
    StripeNotConfigured,
    get_stripe_client,
    list_active_tiers,
)
from app.services.billing.pricing import is_unlimited

log = get_logger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])

# Plans that may be selected at Checkout. 'free' is intentionally absent — see
# module docstring.
_CHECKOUT_PLANS = frozenset({"starter", "team", "business"})
_CHECKOUT_INTERVALS = frozenset({"month", "year"})


# ── Models ───────────────────────────────────────────────────────────────────


class CheckoutRequest(BaseModel):
    plan: Literal["starter", "team", "business"]
    interval: Literal["month", "year"] = "month"
    # success/cancel URLs are server-derived from settings.app_url so a
    # browser can't redirect the post-checkout user to an attacker-controlled
    # page by manipulating the request body.


class CheckoutResponse(BaseModel):
    checkout_url: str


class PortalResponse(BaseModel):
    portal_url: str


class BillingStatus(BaseModel):
    plan: str
    plan_status: str
    current_period_end: str | None
    cancel_at_period_end: bool
    has_billing_account: bool
    is_admin: bool


class TierView(BaseModel):
    plan: str
    interval: str
    stripe_price_id: str
    unit_amount_cents: int
    currency: str
    quota_documents: int | None
    quota_queries_monthly: int | None
    quota_seats: int | None


class PlansResponse(BaseModel):
    plans: list[TierView]
    mode: str  # 'test' | 'live'


# ── Helpers ──────────────────────────────────────────────────────────────────


def _require_org(current_user: dict) -> tuple[str, str, str]:
    user_id = current_user.get("user_id")
    org_id = current_user.get("org_id")
    token = current_user.get("token")
    if not user_id or not org_id or not token:
        raise NoOrganization(
            "No organization found. Please sign out and sign back in."
        )
    return user_id, org_id, token


async def _load_org(org_id: str) -> dict[str, Any]:
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select(
            "id, name, plan, plan_status, stripe_customer_id, "
            "stripe_subscription_id, current_period_end, cancel_at_period_end"
        )
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    if not res or not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found.",
        )
    return res.data


async def _is_admin(user_id: str, org_id: str) -> bool:
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("users")
        .select("role")
        .eq("id", user_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    return bool(res and res.data and res.data.get("role") == "admin")


async def _require_admin(user_id: str, org_id: str) -> None:
    if not await _is_admin(user_id, org_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can change billing.",
        )


def _quota_to_optional(value: int | None) -> int | None:
    if value is None:
        return None
    return None if is_unlimited(value) else int(value)


# ── Public: pricing for the marketing /pricing + /settings/billing cards ────


@router.get("/plans", response_model=PlansResponse)
async def get_plans() -> PlansResponse:
    """List active pricing tiers for the configured Stripe mode.

    Public — no auth header required. The pricing_tiers table is RLS-public
    (SELECT) for is_active=TRUE rows; we still surface through FastAPI so
    the response shape is stable even if we move the data later.
    """
    settings = get_settings()
    tiers = list_active_tiers()
    return PlansResponse(
        mode=settings.stripe_mode,
        plans=[
            TierView(
                plan=t.plan,
                interval=t.interval,
                stripe_price_id=t.stripe_price_id,
                unit_amount_cents=t.unit_amount_cents,
                currency=t.currency,
                quota_documents=_quota_to_optional(t.quota_documents),
                quota_queries_monthly=_quota_to_optional(t.quota_queries_monthly),
                quota_seats=_quota_to_optional(t.quota_seats),
            )
            for t in tiers
        ],
    )


# ── Authed: status snapshot for the billing settings page ────────────────────


@router.get("/status", response_model=BillingStatus)
async def get_status(
    current_user: dict = Depends(verify_jwt),
) -> BillingStatus:
    user_id, org_id, _ = _require_org(current_user)
    org = await _load_org(org_id)
    is_admin = await _is_admin(user_id, org_id)
    return BillingStatus(
        plan=org.get("plan") or "free",
        plan_status=org.get("plan_status") or "inactive",
        current_period_end=org.get("current_period_end"),
        cancel_at_period_end=bool(org.get("cancel_at_period_end")),
        has_billing_account=bool(org.get("stripe_customer_id")),
        is_admin=is_admin,
    )


# ── Authed: start a Checkout session ─────────────────────────────────────────


def _find_price_for(plan: str, interval: str) -> tuple[str, int]:
    """Look up the Stripe Price ID + amount for (plan, interval) from
    pricing_tiers. Raises 400 if the combination isn't seeded.
    """
    tiers = list_active_tiers()
    for t in tiers:
        if t.plan == plan and t.interval == interval:
            return t.stripe_price_id, t.unit_amount_cents
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            f"No active price for {plan}/{interval}. "
            "Run scripts/stripe_seed.py to populate pricing_tiers."
        ),
    )


@router.post("/checkout-session", response_model=CheckoutResponse)
async def create_checkout_session(
    body: CheckoutRequest,
    current_user: dict = Depends(verify_jwt),
) -> CheckoutResponse:
    user_id, org_id, _ = _require_org(current_user)
    await _require_admin(user_id, org_id)

    if body.plan not in _CHECKOUT_PLANS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pick a paid plan to subscribe to.",
        )
    if body.interval not in _CHECKOUT_INTERVALS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Billing interval must be 'month' or 'year'.",
        )

    price_id, _ = _find_price_for(body.plan, body.interval)

    settings = get_settings()
    try:
        stripe = get_stripe_client()
    except StripeNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Billing is not configured on this deployment. "
                "Contact support@nirnayaiq.com."
            ),
        ) from exc

    org = await _load_org(org_id)

    # Resolve the caller's email — Stripe wants it on the Customer object
    # so receipts and invoices go to the right place.
    svc = get_service_client()
    user_email: str | None = None
    try:
        au = await asyncio.to_thread(
            lambda: svc.auth.admin.get_user_by_id(user_id)
        )
        user_email = getattr(getattr(au, "user", None), "email", None)
    except Exception as exc:
        log.warning("billing_caller_email_lookup_failed", error=str(exc))

    # Mint or reuse the Stripe Customer. We persist `stripe_customer_id`
    # immediately so a retry on a transient failure between Customer.create
    # and Checkout.create doesn't orphan a second customer.
    customer_id: str | None = org.get("stripe_customer_id")
    if not customer_id:
        try:
            customer = await asyncio.to_thread(
                lambda: stripe.Customer.create(
                    email=user_email,
                    name=org.get("name"),
                    metadata={"org_id": str(org_id)},
                    # Idempotency: if the user double-clicks Upgrade we get
                    # the same customer back, not a duplicate.
                    idempotency_key=f"customer-create:org:{org_id}",
                )
            )
        except Exception as exc:
            log.exception("stripe_customer_create_failed", org_id=org_id)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Couldn't reach the billing provider. Try again in a moment.",
            ) from exc

        customer_id = customer.id
        await asyncio.to_thread(
            lambda: svc.table("organizations")
            .update({"stripe_customer_id": customer_id})
            .eq("id", org_id)
            .execute()
        )

    success_url = (
        f"{settings.app_url.rstrip('/')}/settings/billing?checkout=success"
        "&session_id={CHECKOUT_SESSION_ID}"
    )
    cancel_url = (
        f"{settings.app_url.rstrip('/')}/settings/billing?checkout=canceled"
    )

    checkout_kwargs: dict[str, Any] = {
        "customer": customer_id,
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        # Both for redundancy — different Stripe event types surface one or
        # the other. The webhook handler reads whichever it can find.
        "client_reference_id": str(org_id),
        "metadata": {"org_id": str(org_id), "plan": body.plan, "interval": body.interval},
        "subscription_data": {
            "metadata": {"org_id": str(org_id), "plan": body.plan},
        },
        "allow_promotion_codes": True,
    }

    if settings.stripe_collect_billing_address:
        checkout_kwargs["billing_address_collection"] = "required"
        checkout_kwargs["tax_id_collection"] = {"enabled": True}

    try:
        session = await asyncio.to_thread(
            lambda: stripe.checkout.Session.create(**checkout_kwargs)
        )
    except Exception as exc:
        log.exception(
            "stripe_checkout_create_failed",
            org_id=org_id,
            plan=body.plan,
            interval=body.interval,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Couldn't start checkout. Try again in a moment.",
        ) from exc

    return CheckoutResponse(checkout_url=session.url)


# ── Authed: open the Stripe Customer Portal ──────────────────────────────────


@router.post("/portal-session", response_model=PortalResponse)
async def create_portal_session(
    current_user: dict = Depends(verify_jwt),
) -> PortalResponse:
    user_id, org_id, _ = _require_org(current_user)
    await _require_admin(user_id, org_id)

    org = await _load_org(org_id)
    customer_id = org.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No billing account yet. Subscribe to a plan first.",
        )

    try:
        stripe = get_stripe_client()
    except StripeNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing is not configured on this deployment.",
        ) from exc

    settings = get_settings()
    return_url = f"{settings.app_url.rstrip('/')}/settings/billing"

    try:
        session = await asyncio.to_thread(
            lambda: stripe.billing_portal.Session.create(
                customer=customer_id,
                return_url=return_url,
            )
        )
    except Exception as exc:
        log.exception("stripe_portal_create_failed", org_id=org_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Couldn't open the billing portal. Try again in a moment.",
        ) from exc

    return PortalResponse(portal_url=session.url)
