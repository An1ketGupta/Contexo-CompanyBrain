"""Inngest functions for Day-13 outbound webhooks + weekly review check.

Two functions live here:

    deliver-webhook            (event: webhook/deliver)
        One delivery attempt per invocation. HMAC-signs the body when the
        webhook has a secret, POSTs to the URL, records the attempt in
        webhook_deliveries. Inngest's retries+backoff drive resilience —
        we keep this function dumb so the retry policy is the source of
        truth for "what counts as failure."

    check-document-reviews     (cron: Mon 08:00 UTC)
        Scans documents whose `review_due_at` has elapsed, groups them by
        org, queues one knowledge_refresh email per admin per org. Dedupe
        keying ensures a re-fired cron in the same week is a no-op.

Split off `functions.py` so a future change to webhook semantics doesn't
have to read 400 lines of ingestion code first.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import httpx
import inngest

from app.database import get_service_client
from app.inngest.client import get_inngest_client
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
        "delivered_at": datetime.now(timezone.utc).isoformat(),
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


# ── #38 Knowledge refresh: weekly review check ──────────────────────────────

@_inngest_client.create_function(
    fn_id="check-document-reviews",
    trigger=inngest.TriggerCron(cron="0 8 * * MON"),  # Mondays 08:00 UTC
    concurrency=[inngest.Concurrency(limit=1)],
)
async def check_document_reviews(ctx: inngest.Context) -> dict[str, Any]:
    """Find documents past their review_due_at and email org admins.

    Idempotency: each email send gets a YYYY-WW dedupe key so a re-fired cron
    in the same week is a no-op even if we changed the trigger time.
    """
    step = ctx.step

    due = await step.run("fetch-due", _fetch_due_documents)
    if not due:
        return {"status": "no-due"}

    # Group by org so admins get one email listing all overdue docs.
    by_org: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in due:
        by_org[d["org_id"]].append(d)

    iso_week = datetime.now(timezone.utc).strftime("%Y-W%V")
    sent = 0

    for org_id, docs in by_org.items():
        sent += await step.run(
            f"notify-org-{org_id}",
            lambda org_id=org_id, docs=docs: _notify_org_admins(
                org_id=org_id, docs=docs, iso_week=iso_week
            ),
        )

    return {"status": "ok", "docs_due": len(due), "emails_sent": sent}


async def _fetch_due_documents() -> list[dict[str, Any]]:
    """Documents with review_due_at in the past. Cheap thanks to the partial
    index on (org_id, review_due_at) WHERE review_due_at IS NOT NULL."""
    svc = get_service_client()
    now = datetime.now(timezone.utc).isoformat()

    def _run() -> list[dict[str, Any]]:
        result = (
            svc.table("documents")
            .select("id, name, org_id, review_due_at, review_frequency_days")
            .lt("review_due_at", now)
            .eq("status", "ready")
            # Cap so a one-time backfill doesn't email out a thousand-row digest.
            .limit(500)
            .execute()
        )
        return result.data or []

    return await asyncio.to_thread(_run)


async def _notify_org_admins(
    *,
    org_id: str,
    docs: list[dict[str, Any]],
    iso_week: str,
) -> int:
    """Email each admin in the org with the list of overdue docs.

    Returns count of emails enqueued (not delivered — that's the email
    worker's problem). Failures here are logged and dropped.
    """
    from app.config import get_settings
    from app.services.email import send_email_event
    from app.services.notifications import create_notification

    settings = get_settings()
    svc = get_service_client()

    def _admin_rows() -> list[tuple[str, str]]:
        users = (
            svc.table("users")
            .select("id, display_name")
            .eq("org_id", org_id)
            .eq("role", "admin")
            .execute()
        )
        out: list[tuple[str, str]] = []
        for row in users.data or []:
            try:
                au = svc.auth.admin.get_user_by_id(row["id"])
                email = getattr(getattr(au, "user", None), "email", None)
                if email:
                    out.append((row["id"], email))
            except Exception:
                continue
        return out

    admins = await asyncio.to_thread(_admin_rows)
    if not admins:
        return 0

    # Hand the email template a slim payload — name + URL is all the
    # template needs; we don't want raw timestamps formatted by a Python
    # f-string slipping into the rendered HTML.
    docs_payload = [
        {
            "id": d["id"],
            "name": d["name"],
            "review_url": (
                f"{settings.app_url.rstrip('/')}/documents?review={d['id']}"
            ),
        }
        for d in docs[:20]  # cap to keep the email tight
    ]
    total_due = len(docs)

    # Two-channel delivery: in-app bell + email. Both share the same iso_week
    # dedupe_key so re-firing the Monday cron doesn't double up either channel.
    enqueued = 0
    notif_title = (
        f"{total_due} document needs review"
        if total_due == 1
        else f"{total_due} documents need review"
    )
    # Body lists the first 5 doc names; the popover trims further if needed.
    doc_names_preview = ", ".join(d["name"] for d in docs[:5])
    if total_due > 5:
        doc_names_preview += f", and {total_due - 5} more"
    notif_link = f"{settings.app_url.rstrip('/')}/documents?filter=review-due"

    for admin_id, admin_email in admins:
        # In-app notification (idempotent via dedupe_key).
        try:
            await create_notification(
                org_id=org_id,
                user_id=admin_id,
                type="review_reminder",
                title=notif_title,
                body=doc_names_preview,
                link_url=notif_link,
                dedupe_key=iso_week,
                metadata={
                    "total_due": total_due,
                    "doc_ids": [d["id"] for d in docs[:20]],
                },
            )
        except Exception as exc:
            log.warning(
                "review_reminder_notification_failed admin=%s org=%s err=%s",
                admin_id, org_id, exc,
            )

        # Email (idempotent via dedupe_key in the email worker).
        try:
            await send_email_event(
                event_type="knowledge_refresh",  # type: ignore[arg-type]
                to=admin_email,
                user_id=admin_id,
                org_id=org_id,
                dedupe_key=iso_week,
                data={
                    "docs": docs_payload,
                    "total_due": total_due,
                    "app_url": settings.app_url,
                },
            )
            enqueued += 1
        except Exception as exc:
            log.warning(
                "review_reminder_email_failed admin=%s org=%s err=%s",
                admin_id, org_id, exc,
            )

    return enqueued


FUNCTIONS = [deliver_webhook, check_document_reviews]
