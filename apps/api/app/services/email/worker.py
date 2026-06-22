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
    trigger=inngest.TriggerCron(cron="0 9 * * FRI"),
    concurrency=[inngest.Concurrency(limit=1)],
)
async def weekly_digest(ctx: inngest.Context) -> dict[str, Any]:
    """Fridays at 09:00 UTC. Fans out one digest per active org.

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
        # Orgs with ANY signal in the last week: a message OR a new document.
        # Day 11 expanded this from messages-only because the digest now
        # surfaces new-doc activity even when chat has been quiet (e.g. the
        # admin uploaded a policy on Friday, no one queried yet).
        msgs = (
            svc.table("messages")
            .select("org_id")
            .gte("created_at", one_week_ago)
            .execute()
        )
        docs = (
            svc.table("documents")
            .select("org_id")
            .gte("created_at", one_week_ago)
            .execute()
        )
        org_ids = {row["org_id"] for row in (msgs.data or []) if row.get("org_id")}
        org_ids |= {row["org_id"] for row in (docs.data or []) if row.get("org_id")}
        if not org_ids:
            return []
        orgs = (
            svc.table("organizations")
            .select("id, name")
            .in_("id", list(org_ids))
            .execute()
        )
        return orgs.data or []

    return await asyncio.to_thread(_query)


async def _enqueue_org_digest(org: dict[str, Any], iso_week: str) -> None:
    """Compute weekly stats + fan one digest email per admin in the org.

    Day 11 expanded the digest to cover the full value-prop surface area:
    queries, time saved, knowledge gaps, low-confidence answers, new docs,
    and ack compliance. All sourced from existing analytics tables — no
    new instrumentation. We skip the email if NOTHING happened (zero
    queries AND zero new docs) so an idle org doesn't get spam.
    """
    settings = get_settings()
    stats = await asyncio.to_thread(lambda: _gather_weekly_stats(org["id"]))

    if stats["query_count"] == 0 and stats["new_document_count"] == 0:
        return

    admins = await asyncio.to_thread(lambda: _digest_recipients(org["id"]))
    if not admins:
        return

    from app.services.email import send_email_event

    for admin_id, admin_email in admins:
        await send_email_event(
            event_type="weekly_digest",
            to=admin_email,
            user_id=admin_id,
            org_id=org["id"],
            dedupe_key=iso_week,
            data={
                "org_name": org["name"],
                "app_url": settings.app_url,
                **stats,
            },
        )


def _gather_weekly_stats(org_id: str) -> dict[str, Any]:
    """Single source of truth for the digest's contents.

    Why centralised: the admin "preview / send now" route on Day 11 reuses
    this same function so what they see in-app matches the email exactly.
    """
    svc = get_service_client()
    now = datetime.now(timezone.utc)
    one_week_ago = (now - _ONE_WEEK).isoformat()

    # ── Messages: count, time saved, feedback, low-confidence ──
    msgs_res = (
        svc.table("messages")
        .select("id, role, time_saved_minutes, feedback, confidence_score, intent")
        .eq("org_id", org_id)
        .eq("role", "assistant")
        .gte("created_at", one_week_ago)
        .limit(50000)
        .execute()
    )
    assistant_messages = msgs_res.data or []
    query_count = len(assistant_messages)
    time_saved = sum((m.get("time_saved_minutes") or 0) for m in assistant_messages)
    negative_count = sum(
        1 for m in assistant_messages if (m.get("feedback") or "").lower() in {"negative", "down"}
    )
    positive_count = sum(
        1 for m in assistant_messages if (m.get("feedback") or "").lower() in {"positive", "up"}
    )
    low_conf_count = sum(
        1
        for m in assistant_messages
        if (m.get("confidence_score") is not None and float(m["confidence_score"]) < 5.0)
    )

    # Active users from conversations updated in the window.
    active = (
        svc.table("conversations")
        .select("user_id")
        .eq("org_id", org_id)
        .gte("updated_at", one_week_ago)
        .execute()
    )
    active_users = len({r["user_id"] for r in (active.data or []) if r.get("user_id")})

    # Total doc count (corpus health) + NEW docs this week.
    total_docs = (
        svc.table("documents")
        .select("id", count="exact", head=True)
        .eq("org_id", org_id)
        .eq("status", "ready")
        .execute()
    )
    new_docs_res = (
        svc.table("documents")
        .select("id, name, file_type, requires_acknowledgement")
        .eq("org_id", org_id)
        .gte("created_at", one_week_ago)
        .order("created_at", desc=True)
        .limit(20)
        .execute()
    )
    new_docs = new_docs_res.data or []
    new_doc_titles = [d.get("name") for d in new_docs if d.get("name")][:5]

    # Knowledge gaps surfaced this week — Counter the topics.
    gaps_count = 0
    top_gap_topics: list[dict[str, Any]] = []
    try:
        gaps_res = (
            svc.table("knowledge_gaps")
            .select("topic")
            .eq("org_id", org_id)
            .gte("created_at", one_week_ago)
            .limit(2000)
            .execute()
        )
        gap_rows = gaps_res.data or []
        gaps_count = len(gap_rows)
        from collections import Counter

        counter = Counter(
            (g.get("topic") or "").strip()
            for g in gap_rows
            if (g.get("topic") or "").strip()
        )
        top_gap_topics = [
            {"topic": topic, "count": count}
            for topic, count in counter.most_common(3)
        ]
    except Exception as exc:
        log.warning("digest_gaps_query_failed", org_id=org_id, error=str(exc))

    # Acknowledgement compliance — count outstanding pending older than 7d.
    ack_pending = 0
    ack_completion_pct: float | None = None
    try:
        ack_res = (
            svc.table("acknowledgements")
            .select("status")
            .eq("org_id", org_id)
            .limit(20000)
            .execute()
        )
        ack_rows = ack_res.data or []
        if ack_rows:
            total = len(ack_rows)
            acknowledged = sum(1 for r in ack_rows if r.get("status") == "acknowledged")
            ack_pending = sum(1 for r in ack_rows if r.get("status") == "pending")
            ack_completion_pct = round((acknowledged / total) * 100, 1) if total else 0.0
    except Exception as exc:
        log.warning("digest_ack_query_failed", org_id=org_id, error=str(exc))

    # Top intents — simple Counter for the "what was the team using this for"
    # bar on the digest. Skip blank/None intents.
    try:
        from collections import Counter

        intent_counter = Counter(
            (m.get("intent") or "").strip()
            for m in assistant_messages
            if (m.get("intent") or "").strip()
        )
        top_intents = [
            {"intent": intent, "count": count}
            for intent, count in intent_counter.most_common(3)
        ]
    except Exception:
        top_intents = []

    return {
        "query_count": query_count,
        "doc_count": (total_docs.count or 0),
        "active_users": active_users,
        "time_saved_minutes": round(time_saved, 1),
        "time_saved_hours": round(time_saved / 60, 1) if time_saved else 0,
        "negative_feedback_count": negative_count,
        "positive_feedback_count": positive_count,
        "low_confidence_count": low_conf_count,
        "knowledge_gaps_count": gaps_count,
        "top_gap_topics": top_gap_topics,
        "new_document_count": len(new_docs),
        "new_document_titles": new_doc_titles,
        "ack_pending_count": ack_pending,
        "ack_completion_pct": ack_completion_pct,
        "top_intents": top_intents,
        # Kept for backwards compatibility with the legacy email template;
        # we no longer compute top_doc here.
        "top_doc": None,
    }


def _digest_recipients(org_id: str) -> list[tuple[str, str]]:
    svc = get_service_client()
    users = (
        svc.table("users")
        .select("id")
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


# Public helper for the Day-11 manual trigger route.
async def gather_weekly_stats(org_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(lambda: _gather_weekly_stats(org_id))


@_inngest_client.create_function(
    fn_id="weekly-digest-send-now",
    trigger=inngest.TriggerEvent(event="email/weekly-digest-now"),
    retries=1,
)
async def weekly_digest_send_now(ctx: inngest.Context) -> dict[str, Any]:
    """Manual trigger fired by the Settings page (Day 11).

    Bypasses the iso-week dedupe key — admins testing the digest want to
    see fresh output each time. We mint a unique dedupe per send so the
    email_events guard doesn't swallow follow-up tests.
    """
    data = ctx.event.data
    org_id: str = data["org_id"]
    settings = get_settings()

    org_res = await ctx.step.run(
        "load-org",
        lambda: get_service_client()
        .table("organizations")
        .select("id, name")
        .eq("id", org_id)
        .maybe_single()
        .execute()
        .data,
    )
    if not org_res:
        return {"status": "skipped", "reason": "org_not_found"}

    stats = await ctx.step.run("gather-stats", lambda: _gather_weekly_stats(org_id))
    admins = await ctx.step.run("recipients", lambda: _digest_recipients(org_id))

    if not admins:
        return {"status": "skipped", "reason": "no_admins"}

    from app.services.email import send_email_event

    sent = 0
    nonce = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    for admin_id, admin_email in admins:
        await send_email_event(
            event_type="weekly_digest",
            to=admin_email,
            user_id=admin_id,
            org_id=org_id,
            dedupe_key=f"manual-{nonce}",
            data={
                "org_name": org_res["name"],
                "app_url": settings.app_url,
                **stats,
            },
        )
        sent += 1
    return {"status": "sent", "recipients": sent}


FUNCTIONS = [email_send, weekly_digest, weekly_digest_send_now]
