"""Stripe webhook receiver — single point at which Stripe state lands in our DB.

Guardrails:

  1. Signature verification on raw bytes. Re-serialising request.json()
     would change the byte sequence and fail verification.
  2. Write-ahead log + idempotency. Every event lands in `billing_events`
     with status='received' BEFORE the handler runs; the UNIQUE constraint
     on stripe_event_id dedupes Stripe's at-least-once retries. A crash
     mid-event leaves the row so an admin can replay.
  3. Status transitions: received → processed | failed.

We return 200 on duplicates and unknown event types so Stripe stops
retrying no-ops. Processing failures return 500 so Stripe retries on its
exponential schedule.

The route is NOT JWT-protected — Stripe authenticates by HMAC signature,
not by bearer token.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import get_service_client
from app.observability import get_logger
from app.services.billing import (
    StripeNotConfigured,
    get_stripe_client,
    plan_for_price,
)

log = get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks-stripe"])


# Adding a new event type means listing it here AND wiring up the branch
# in `_dispatch_event`. Unlisted events are acked + logged.
_SUBSCRIPTION_EVENTS = frozenset({
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
})
_HANDLED_EVENTS = frozenset({
    "checkout.session.completed",
    "invoice.payment_failed",
    "invoice.payment_succeeded",
}) | _SUBSCRIPTION_EVENTS


def _iso_from_unix(ts: int | None) -> str | None:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=UTC).isoformat()
    except (TypeError, ValueError):
        return None


def _extract_price_id(subscription_obj: dict[str, Any]) -> str | None:
    """Pull the first price id off a Stripe Subscription object.

    Multi-item subscriptions are out of scope for the launch plan set, but
    we still pick `items[0]` defensively rather than crashing.
    """
    items = (subscription_obj.get("items") or {}).get("data") or []
    if not items:
        return None
    price = (items[0] or {}).get("price") or {}
    return price.get("id")


async def _resolve_org_id(
    *,
    customer_id: str | None,
    metadata_org_id: str | None,
    client_reference_id: str | None,
) -> str | None:
    """Find the local org_id for an incoming Stripe event.

    Preference order:
      1. metadata.org_id  — set by us at Checkout, most reliable.
      2. client_reference_id — also set by us, used on session events.
      3. organizations.stripe_customer_id — reverse lookup, used on
         events that lack our metadata (e.g., invoice events).
    """
    if metadata_org_id:
        return metadata_org_id
    if client_reference_id:
        return client_reference_id
    if not customer_id:
        return None

    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select("id")
        .eq("stripe_customer_id", customer_id)
        .maybe_single()
        .execute()
    )
    if res and res.data:
        return res.data["id"]
    return None


async def _update_org(org_id: str, fields: dict[str, Any]) -> None:
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("organizations")
        .update(fields)
        .eq("id", org_id)
        .execute()
    )


# ── Per-event handlers ───────────────────────────────────────────────────────


async def _handle_checkout_completed(data: dict[str, Any]) -> str | None:
    """Land the first paid state for a freshly-subscribed org.

    The subscription itself arrives in a separate
    `customer.subscription.created` event a beat later, so we *don't* try
    to derive plan/period here from the session — we just bind the
    customer and subscription ids and let the subscription handler do the
    authoritative plan write.
    """
    org_id = await _resolve_org_id(
        customer_id=data.get("customer"),
        metadata_org_id=(data.get("metadata") or {}).get("org_id"),
        client_reference_id=data.get("client_reference_id"),
    )
    if not org_id:
        log.warning("stripe_checkout_no_org", session_id=data.get("id"))
        return None

    fields: dict[str, Any] = {}
    if data.get("customer"):
        fields["stripe_customer_id"] = data["customer"]
    if data.get("subscription"):
        fields["stripe_subscription_id"] = data["subscription"]

    if fields:
        await _update_org(org_id, fields)
    return org_id


async def _handle_subscription_change(
    data: dict[str, Any], *, event_type: str
) -> str | None:
    """Authoritative plan + status write.

    Triggered by:
      - customer.subscription.created
      - customer.subscription.updated
      - customer.subscription.deleted   (status comes in as 'canceled')
    """
    customer_id = data.get("customer")
    metadata_org_id = (data.get("metadata") or {}).get("org_id")
    org_id = await _resolve_org_id(
        customer_id=customer_id,
        metadata_org_id=metadata_org_id,
        client_reference_id=None,
    )
    if not org_id:
        log.warning(
            "stripe_subscription_no_org",
            customer=customer_id,
            event_type=event_type,
        )
        return None

    status_value = data.get("status") or "incomplete"
    cancel_at_period_end = bool(data.get("cancel_at_period_end"))
    current_period_end_iso = _iso_from_unix(data.get("current_period_end"))

    fields: dict[str, Any] = {
        "plan_status": status_value,
        "cancel_at_period_end": cancel_at_period_end,
        "current_period_end": current_period_end_iso,
    }

    if event_type == "customer.subscription.deleted":
        # The subscription is gone — drop to free until they resubscribe.
        # Keep stripe_customer_id (so a future Checkout reuses it) but
        # clear stripe_subscription_id (we no longer own that sub).
        fields["plan"] = "free"
        fields["plan_status"] = "canceled"
        fields["stripe_subscription_id"] = None
    else:
        # Resolve the plan from the active price. plan_for_price reads
        # pricing_tiers, which is the source of truth set up in Day 6.
        price_id = _extract_price_id(data)
        resolved_plan = plan_for_price(price_id) if price_id else None
        if resolved_plan:
            fields["plan"] = resolved_plan
        else:
            # Unknown price — log loudly. Don't overwrite the plan column
            # with garbage; an alert + investigation is more useful than a
            # silent fallback that hides a misconfigured price.
            log.error(
                "stripe_unknown_price_id",
                price_id=price_id,
                customer=customer_id,
                org_id=org_id,
                event_type=event_type,
            )
        if data.get("id"):
            fields["stripe_subscription_id"] = data["id"]

    await _update_org(org_id, fields)
    return org_id


async def _handle_invoice_payment_failed(data: dict[str, Any]) -> str | None:
    """Mark the org as past_due so the UI can warn admins.

    We don't downgrade or revoke access here — Stripe's smart-retry
    schedule handles the recovery. Downgrades happen via
    `customer.subscription.deleted` once Stripe gives up.
    """
    customer_id = data.get("customer")
    org_id = await _resolve_org_id(
        customer_id=customer_id,
        metadata_org_id=None,
        client_reference_id=None,
    )
    if not org_id:
        log.warning("stripe_invoice_failed_no_org", customer=customer_id)
        return None

    await _update_org(org_id, {"plan_status": "past_due"})
    # TODO Day 13: enqueue a notifications row + an email to org admins.
    return org_id


async def _handle_invoice_payment_succeeded(data: dict[str, Any]) -> str | None:
    """Clear a previously-past_due flag when a retry succeeds.

    Stripe sends this on every renewal, not just recoveries. The WHERE
    filter avoids churning the plan_updated_at trigger on routine
    monthly renewals — only past_due/unpaid rows actually update.
    """
    customer_id = data.get("customer")
    org_id = await _resolve_org_id(
        customer_id=customer_id,
        metadata_org_id=None,
        client_reference_id=None,
    )
    if not org_id:
        return None

    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("organizations")
        .update({"plan_status": "active"})
        .eq("id", org_id)
        .in_("plan_status", ["past_due", "unpaid"])
        .execute()
    )
    return org_id


async def _dispatch_event(event_type: str, data: dict[str, Any]) -> str | None:
    """Route to the right per-event handler. Caller guarantees `event_type`
    is in `_HANDLED_EVENTS`; an unknown branch raises rather than silently
    returning so a new event added to the set without a handler fails loud.
    """
    if event_type == "checkout.session.completed":
        return await _handle_checkout_completed(data)
    if event_type in _SUBSCRIPTION_EVENTS:
        return await _handle_subscription_change(data, event_type=event_type)
    if event_type == "invoice.payment_failed":
        return await _handle_invoice_payment_failed(data)
    if event_type == "invoice.payment_succeeded":
        return await _handle_invoice_payment_succeeded(data)
    raise RuntimeError(f"Handler missing for {event_type!r}")


def _is_duplicate_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "duplicate key" in msg or "billing_events_stripe_event_id_key" in msg


async def _record_received(
    *,
    stripe_event_id: str,
    event_type: str,
    payload: dict[str, Any],
    customer_id: str | None,
) -> bool:
    """Insert the WAL row. Returns True if newly received, False if duplicate."""
    svc = get_service_client()
    try:
        await asyncio.to_thread(
            lambda: svc.table("billing_events")
            .insert(
                {
                    "stripe_event_id": stripe_event_id,
                    "event_type": event_type,
                    "stripe_customer_id": customer_id,
                    "payload": payload,
                    "status": "received",
                }
            )
            .execute()
        )
        return True
    except Exception as exc:
        if _is_duplicate_error(exc):
            return False
        raise


async def _mark_processed(
    stripe_event_id: str, org_id: str | None
) -> None:
    svc = get_service_client()
    updates: dict[str, Any] = {
        "status": "processed",
        "processed_at": datetime.now(UTC).isoformat(),
    }
    if org_id:
        updates["org_id"] = org_id
    await asyncio.to_thread(
        lambda: svc.table("billing_events")
        .update(updates)
        .eq("stripe_event_id", stripe_event_id)
        .execute()
    )


async def _mark_failed(stripe_event_id: str, error_message: str) -> None:
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("billing_events")
        .update(
            {
                "status": "failed",
                "error_message": error_message[:2000],
                "processed_at": datetime.now(UTC).isoformat(),
            }
        )
        .eq("stripe_event_id", stripe_event_id)
        .execute()
    )


@router.post("/stripe", include_in_schema=False)
async def stripe_webhook(request: Request) -> JSONResponse:
    """Stripe -> us. Verified by signature, idempotent by stripe_event_id."""
    settings = get_settings()
    if not settings.stripe_webhook_secret:
        # Avoid a confusing signature error when the secret simply isn't set
        # (e.g., local dev without `stripe listen`). 503 surfaces the gap.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe webhooks are not configured on this deployment.",
        )

    raw_body = await request.body()
    sig_header = request.headers.get("stripe-signature")
    if not sig_header:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Stripe signature.",
        )

    try:
        stripe = get_stripe_client()
    except StripeNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing is not configured.",
        ) from exc

    try:
        event = stripe.Webhook.construct_event(
            raw_body, sig_header, settings.stripe_webhook_secret
        )
    except stripe.error.SignatureVerificationError as exc:
        log.warning("stripe_webhook_bad_signature", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid signature.",
        ) from exc
    except ValueError as exc:
        log.warning("stripe_webhook_bad_payload", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid payload.",
        ) from exc

    event_id: str = event["id"]
    event_type: str = event["type"]
    data: dict[str, Any] = event["data"]["object"]
    payload: dict[str, Any] = event.to_dict_recursive()

    customer_id = (
        data.get("customer") if isinstance(data.get("customer"), str) else None
    )

    # Step 1 — write-ahead log. Duplicates short-circuit here.
    try:
        is_new = await _record_received(
            stripe_event_id=event_id,
            event_type=event_type,
            payload=payload,
            customer_id=customer_id,
        )
    except Exception as exc:
        # Logging the WAL row failed — surface 500 so Stripe retries. We
        # would rather get the same event twice than silently lose it.
        log.exception("stripe_webhook_wal_insert_failed", event_id=event_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook persistence failed.",
        ) from exc

    if not is_new:
        log.info("stripe_webhook_duplicate", stripe_event_id=event_id)
        return JSONResponse({"status": "duplicate"})

    # Step 2 — process. Anything we don't explicitly handle is acked so
    # Stripe stops retrying it; we still WAL'd the row for audit.
    if event_type not in _HANDLED_EVENTS:
        await _mark_processed(event_id, org_id=None)
        return JSONResponse({"status": "ignored", "event_type": event_type})

    try:
        org_id = await _dispatch_event(event_type, data)
    except Exception as exc:
        log.exception(
            "stripe_webhook_handler_failed",
            event_id=event_id,
            event_type=event_type,
        )
        await _mark_failed(event_id, error_message=str(exc))
        # 500 → Stripe retries on its exponential schedule. Don't lose
        # state because of a transient downstream issue.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook processing failed.",
        ) from exc

    await _mark_processed(event_id, org_id=org_id)
    return JSONResponse({"status": "ok"})
