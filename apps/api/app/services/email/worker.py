"""Inngest worker that renders + sends transactional emails.

Flow per `email/send` event:
    1. Check email_events for a duplicate (idempotency) — short-circuit on hit.
    2. POST { template, data } to the Next.js render route (HMAC-signed) to
       get { html, subject }.
    3. POST to Resend's REST API directly (the official Python SDK is sync;
       httpx is already in our stack).
    4. Insert email_events row. The unique partial indexes in migration 009
       guarantee a second concurrent worker writing the same row will lose
       and the loser's Resend send becomes a double-send — acceptable since
       Resend dedupes on its own headers if we set them, but we keep the
       race window tiny by doing the existence check immediately before send.

Failures bubble up so Inngest retries (3 attempts, exponential backoff). The
render route is treated as transient; Resend 4xx (bad domain, invalid email)
is treated as permanent and logged but not retried.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import inngest

from app.config import get_settings
from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.observability import get_logger

log = get_logger(__name__)

_inngest_client = get_inngest_client()
_ONE_WEEK = timedelta(days=7)


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


async def _render(event_type: str, data: dict[str, Any]) -> dict[str, str]:
    settings = get_settings()
    if not settings.internal_email_secret:
        raise RuntimeError("INTERNAL_EMAIL_SECRET not set — refusing to call render route.")

    payload = json.dumps({"template": event_type, "data": data}).encode("utf-8")
    signature = _sign(settings.internal_email_secret, payload)

    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
        response = await client.post(
            settings.email_render_url,
            content=payload,
            headers={
                "Content-Type": "application/json",
                "X-Internal-Signature": signature,
            },
        )
    if response.status_code >= 400:
        raise RuntimeError(
            f"render route {response.status_code}: {response.text[:300]}"
        )
    rendered = response.json()
    if not rendered.get("html") or not rendered.get("subject"):
        raise RuntimeError(f"render route missing fields: {rendered}")
    return {"html": rendered["html"], "subject": rendered["subject"]}


async def _resend_send(*, to: str, subject: str, html: str) -> str | None:
    """POST to Resend. Returns the Resend message id on success, None on
    permanent failure (4xx). Raises on transient failure so Inngest retries."""
    settings = get_settings()
    if not settings.email_enabled or not settings.resend_api_key:
        log.info(
            "email_send_skipped_dev",
            recipient=to,
            subject=subject,
            reason="email_enabled=false or no key",
        )
        return "dev-skipped"

    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
        response = await client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": settings.email_from,
                "to": [to],
                "subject": subject,
                "html": html,
            },
        )

    if response.status_code == 200 or response.status_code == 201:
        try:
            return response.json().get("id")
        except Exception:
            return None

    body = response.text[:300]
    if 400 <= response.status_code < 500:
        # Bad domain / blocked recipient / malformed payload — don't retry.
        log.error(
            "resend_permanent_failure",
            status=response.status_code,
            body=body,
            recipient=to,
        )
        return None

    # 5xx — let Inngest retry.
    raise RuntimeError(f"resend {response.status_code}: {body}")


async def _already_sent(
    *,
    user_id: str | None,
    recipient: str,
    event_type: str,
    dedupe_key: str | None,
) -> bool:
    """Truthy if a matching row already exists in email_events.

    The unique partial indexes in migration 009 also enforce this at the DB
    layer, but pre-checking lets us skip the render + Resend call entirely
    on duplicates — saves money and rate-limit budget.
    """
    svc = get_service_client()

    def _check() -> bool:
        q = (
            svc.table("email_events")
            .select("id", count="exact", head=True)
            .eq("event_type", event_type)
        )
        if user_id is not None:
            q = q.eq("user_id", user_id)
            if dedupe_key is None:
                q = q.is_("dedupe_key", "null")
            else:
                q = q.eq("dedupe_key", dedupe_key)
        else:
            q = q.eq("recipient", recipient)
            if dedupe_key is not None:
                q = q.eq("dedupe_key", dedupe_key)
            else:
                # No user, no dedupe_key — nothing to match; treat as fresh send.
                return False
        result = q.execute()
        return (result.count or 0) > 0

    return await asyncio.to_thread(_check)


async def _record(
    *,
    user_id: str | None,
    org_id: str | None,
    recipient: str,
    event_type: str,
    dedupe_key: str | None,
    resend_id: str | None,
    metadata: dict[str, Any],
) -> None:
    svc = get_service_client()
    row = {
        "user_id": user_id,
        "org_id": org_id,
        "recipient": recipient,
        "event_type": event_type,
        "dedupe_key": dedupe_key,
        "resend_id": resend_id,
        "metadata": metadata,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await asyncio.to_thread(lambda: svc.table("email_events").insert(row).execute())
    except Exception as exc:
        # Most likely the unique index fired because of a tight race; that's
        # exactly the protection we want. Log and move on — the email has
        # already been sent to the recipient.
        if "duplicate key" in str(exc).lower():
            log.info(
                "email_event_dedup_race",
                recipient=recipient,
                event_type=event_type,
                dedupe_key=dedupe_key,
            )
            return
        raise


@_inngest_client.create_function(
    fn_id="email-send",
    trigger=inngest.TriggerEvent(event="email/send"),
    retries=3,
    concurrency=[inngest.Concurrency(limit=5)],
)
async def email_send(ctx: inngest.Context) -> dict[str, Any]:
    """Render via Next.js, send via Resend, record in email_events."""
    step = ctx.step
    data = ctx.event.data

    event_type: str = data["event_type"]
    recipient: str = data["to"]
    user_id: str | None = data.get("user_id")
    org_id: str | None = data.get("org_id")
    dedupe_key: str | None = data.get("dedupe_key")
    template_data: dict[str, Any] = data.get("template_data") or {}

    # Step 1: bail out early if we've already sent this.
    already = await step.run(
        "check-idempotency",
        lambda: _already_sent(
            user_id=user_id,
            recipient=recipient,
            event_type=event_type,
            dedupe_key=dedupe_key,
        ),
    )
    if already:
        log.info(
            "email_dedup_short_circuit",
            event_type=event_type,
            recipient=recipient,
            dedupe_key=dedupe_key,
        )
        return {"status": "skipped", "reason": "duplicate"}

    # Step 2: render via Next.js.
    rendered = await step.run(
        "render",
        lambda: _render(event_type, template_data),
    )

    # Step 3: send via Resend.
    resend_id = await step.run(
        "send",
        lambda: _resend_send(
            to=recipient,
            subject=rendered["subject"],
            html=rendered["html"],
        ),
    )

    # Step 4: record. Even on permanent Resend failure (resend_id=None) we
    # write the row so a retry doesn't loop forever against a bad address.
    await step.run(
        "record",
        lambda: _record(
            user_id=user_id,
            org_id=org_id,
            recipient=recipient,
            event_type=event_type,
            dedupe_key=dedupe_key,
            resend_id=resend_id,
            metadata={"subject": rendered["subject"]},
        ),
    )

    return {"status": "sent", "resend_id": resend_id}


@_inngest_client.create_function(
    fn_id="email-weekly-digest",
    trigger=inngest.TriggerCron(cron="0 9 * * MON"),
    concurrency=[inngest.Concurrency(limit=1)],
)
async def weekly_digest(ctx: inngest.Context) -> dict[str, Any]:
    """Mondays at 09:00 UTC. Fans out one digest per active org.

    Each fan-out is its own `email/send` event so retries are scoped to one
    org rather than re-driving the whole batch. The dedupe_key (YYYY-WW)
    guarantees a re-fire of the cron in the same week is a no-op.
    """
    step = ctx.step

    orgs = await step.run("collect-orgs", _collect_active_orgs)
    iso_week = datetime.now(timezone.utc).strftime("%Y-W%V")

    for org in orgs:
        await step.run(
            f"enqueue-{org['id']}",
            lambda org=org, week=iso_week: _enqueue_org_digest(org, week),
        )

    return {"orgs_processed": len(orgs)}


async def _collect_active_orgs() -> list[dict[str, Any]]:
    svc = get_service_client()
    one_week_ago = (datetime.now(timezone.utc) - _ONE_WEEK).isoformat()

    def _query() -> list[dict[str, Any]]:
        # Orgs that had at least one message in the last week. Cheap join via
        # IN clause — for a small product like ours (<1k orgs) this is fine.
        msgs = (
            svc.table("messages")
            .select("org_id")
            .gte("created_at", one_week_ago)
            .execute()
        )
        org_ids = list({row["org_id"] for row in (msgs.data or []) if row.get("org_id")})
        if not org_ids:
            return []
        orgs = (
            svc.table("organizations")
            .select("id, name")
            .in_("id", org_ids)
            .execute()
        )
        return orgs.data or []

    return await asyncio.to_thread(_query)


async def _enqueue_org_digest(org: dict[str, Any], iso_week: str) -> None:
    """Compute weekly stats + fan one digest email per admin in the org."""
    svc = get_service_client()
    settings = get_settings()
    one_week_ago = (datetime.now(timezone.utc) - _ONE_WEEK).isoformat()

    def _stats() -> dict[str, Any]:
        msgs = (
            svc.table("messages")
            .select("id", count="exact", head=True)
            .eq("org_id", org["id"])
            .eq("role", "assistant")
            .gte("created_at", one_week_ago)
            .execute()
        )
        active = (
            svc.table("conversations")
            .select("user_id")
            .eq("org_id", org["id"])
            .gte("updated_at", one_week_ago)
            .execute()
        )
        docs = (
            svc.table("documents")
            .select("id", count="exact", head=True)
            .eq("org_id", org["id"])
            .eq("status", "ready")
            .execute()
        )
        unique_users = len({r["user_id"] for r in (active.data or []) if r.get("user_id")})
        return {
            "query_count": msgs.count or 0,
            "doc_count": docs.count or 0,
            "active_users": unique_users,
        }

    stats = await asyncio.to_thread(_stats)
    if stats["query_count"] == 0:
        return  # nothing happened; skip the email rather than mailing a "0 queries" report

    # Send to all admins. Each gets their own dedupe key so we don't email the
    # same admin twice if they're somehow in multiple orgs (future-proofing).
    def _admins() -> list[str]:
        users = (
            svc.table("users")
            .select("id")
            .eq("org_id", org["id"])
            .eq("role", "admin")
            .execute()
        )
        out: list[str] = []
        for row in users.data or []:
            try:
                au = svc.auth.admin.get_user_by_id(row["id"])
                email = getattr(getattr(au, "user", None), "email", None)
                if email:
                    out.append((row["id"], email))  # type: ignore[arg-type]
            except Exception:
                continue
        return out  # type: ignore[return-value]

    admins = await asyncio.to_thread(_admins)
    from app.services.email import send_email_event

    for admin_id, admin_email in admins:  # type: ignore[misc]
        await send_email_event(
            event_type="weekly_digest",
            to=admin_email,
            user_id=admin_id,
            org_id=org["id"],
            dedupe_key=iso_week,
            data={
                "org_name": org["name"],
                "query_count": stats["query_count"],
                "doc_count": stats["doc_count"],
                "active_users": stats["active_users"],
                "top_doc": None,  # TODO: compute from sources jsonb when we instrument it
                "app_url": settings.app_url,
            },
        )


FUNCTIONS = [email_send, weekly_digest]
