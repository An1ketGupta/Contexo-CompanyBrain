"""Inngest functions for Day-13 outbound webhooks.

    deliver-webhook            (event: webhook/deliver)
        One delivery attempt per invocation. HMAC-signs the body when the
        webhook has a secret, POSTs to the URL, records the attempt in
        webhook_deliveries. Inngest's retries+backoff drive resilience —
        we keep this function dumb so the retry policy is the source of
        truth for "what counts as failure."

Split off `functions.py` so a future change to webhook semantics doesn't
have to read 400 lines of ingestion code first.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
import inngest

from app.inngest.client import get_inngest_client
from app.services.network_security import UnsafeURLError, validate_outbound_url
from app.services.webhooks import fetch_webhook, record_delivery

log = logging.getLogger(__name__)

_inngest_client = get_inngest_client()

# Cap on the URL response we read back. Customers occasionally hand us a
# Heroku-style error page; we want the first line for debugging, not 2 MB
# of HTML in the deliveries table.
_RESPONSE_BODY_LIMIT = 4096

# Bounded total delivery latency. Inngest's per-attempt cap is generous but
# a slow customer endpoint should still time out fast enough that we don't
# leak runtime into the next retry.
_DELIVERY_TIMEOUT_SECONDS = 10.0

# Header set we send out. Kept here as constants so a customer can grep this
# file when debugging "where did this header come from."
_HEADER_EVENT = "X-NirnayaIQ-Event"
_HEADER_DELIVERY = "X-NirnayaIQ-Delivery"
_HEADER_SIGNATURE = "X-NirnayaIQ-Signature"


@_inngest_client.create_function(
    fn_id="deliver-webhook",
    trigger=inngest.TriggerEvent(event="webhook/deliver"),
    # Inngest exponential backoff handles the rest — 4 attempts with the
    # default schedule (~30s, ~2min, ~10min, ~30min) gives a customer's
    # endpoint a fair chance of coming back up.
    retries=3,
    # Per-org concurrency cap so a misbehaving customer with 50 webhooks
    # firing on every chat turn can't starve the worker pool.
    concurrency=[
        inngest.Concurrency(limit=4, key="event.data.org_id", scope="fn"),
    ],
)
async def deliver_webhook(ctx: inngest.Context) -> dict[str, Any]:
    step = ctx.step
    data = ctx.event.data

    webhook_id: str = data["webhook_id"]
    org_id: str = data["org_id"]
    event: str = data["event"]
    payload: dict[str, Any] = data.get("payload") or {}
    # Inngest exposes the attempt counter via `ctx.attempt` (0-indexed) —
    # we surface it as 1-indexed for human consumption in the deliveries log.
    attempt = (getattr(ctx, "attempt", 0) or 0) + 1

    # Refetch the webhook on each attempt so a quick "revoke" or "rotate
    # secret" change takes effect on the next retry rather than the next
    # event. Avoids the surprise of "I disabled the hook but it's still
    # firing for 30 minutes."
    hook = await step.run(
        "fetch-webhook",
        lambda: fetch_webhook(webhook_id),
    )
    if not hook:
        log.info("webhook_skip_missing", webhook_id=webhook_id)
        return {"status": "skipped", "reason": "webhook_not_found"}
    if not hook.get("is_active"):
        return {"status": "skipped", "reason": "webhook_disabled"}

    # Build a stable JSON body. `separators=(',', ':')` so the recipient
    # gets bytes-identical content on retries (matters if they cache by
    # body hash) and so the HMAC we compute matches what they recompute.
    body_json = {
        "event": event,
        "data": payload,
        "delivered_at": datetime.now(UTC).isoformat(),
        "attempt": attempt,
    }
    raw = json.dumps(body_json, separators=(",", ":"), sort_keys=True).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        _HEADER_EVENT: event,
        _HEADER_DELIVERY: ctx.event.id or webhook_id,
        "User-Agent": "NirnayaIQ-Webhooks/1.0",
    }
    secret = hook.get("secret")
    if secret:
        sig = hmac.new(
            secret.encode("utf-8"), raw, hashlib.sha256
        ).hexdigest()
        headers[_HEADER_SIGNATURE] = f"sha256={sig}"

    status_code: int | None = None
    response_body: str | None = None
    error: str | None = None
    transient = False

    # SSRF guard. Validate AS LATE as possible — right before the request —
    # so a hostname that flipped from public to private between webhook
    # creation and delivery still gets caught. Re-validation on every
    # retry is intentional: a customer's DNS can rebind at any time.
    try:
        validate_outbound_url(hook["url"])
    except UnsafeURLError as exc:
        # Treat as a PERMANENT failure (transient=False) so Inngest doesn't
        # burn retries on a URL we'll never dial. The customer-facing error
        # in the deliveries table is a generic "destination not allowed";
        # the specific reason is logged server-side only — confirming
        # which internal IPs are reachable would itself be useful recon.
        log.warning("webhook_ssrf_blocked webhook=%s reason=%s", webhook_id, exc)
        error = "destination_not_allowed"
        transient = False
        await step.run(
            "record-delivery",
            lambda: record_delivery(
                webhook_id=webhook_id,
                org_id=org_id,
                event=event,
                payload=payload,
                attempt=attempt,
                status_code=None,
                response_body=None,
                error=error,
            ),
        )
        return {"status": "blocked", "reason": "destination_not_allowed"}

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_DELIVERY_TIMEOUT_SECONDS, connect=3.0)
        ) as client:
            resp = await client.post(hook["url"], content=raw, headers=headers)
        status_code = resp.status_code
        response_body = (resp.text or "")[:_RESPONSE_BODY_LIMIT]
        # Treat 5xx + 429 as transient (retry), 4xx as permanent (don't).
        # This is the convention webhook receivers expect (GitHub, Stripe).
        if status_code >= 500 or status_code == 429:
            transient = True
            error = f"HTTP {status_code}"
    except httpx.TimeoutException as exc:
        error = f"timeout: {exc}"
        transient = True
    except httpx.RequestError as exc:
        # Network-level failures (DNS, connection refused) — retry.
        error = f"{type(exc).__name__}: {exc}"
        transient = True
    except Exception as exc:
        # Unknown failure mode — log and retry once.
        log.exception("webhook_deliver_unhandled")
        error = f"{type(exc).__name__}: {exc}"
        transient = True

    # Record the attempt regardless of outcome. This is the audit trail the
    # admin sees in the dashboard.
    await step.run(
        "record-delivery",
        lambda: record_delivery(
            webhook_id=webhook_id,
            org_id=org_id,
            event=event,
            payload=payload,
            status_code=status_code,
            response_body=response_body,
            error=error,
            attempt=attempt,
        ),
    )

    if transient and attempt <= 4:
        # Let Inngest retry. Raising surfaces the right error in the dash.
        raise RuntimeError(error or f"HTTP {status_code}")

    return {
        "status": "delivered" if (status_code and 200 <= status_code < 300) else "permanent_failure",
        "status_code": status_code,
        "attempt": attempt,
    }


FUNCTIONS = [deliver_webhook]
