"""Internal Communications Agent — multichannel announcement drafting +
scheduled dispatch (Agent2 Day 4 #23).

Flow:

    1. draft_announcement(request_text)
         Single LLM call returning structured JSON with email, Slack, and
         Notion renderings of the same content. Stored as a draft on
         `internal_announcements` with status='draft'.

    2. update_announcement(...)
         Admin tweaks the drafts in place before scheduling.

    3. schedule_announcement(id, scheduled_for, recipients, channels)
         Snapshot the chosen destinations on the row; flip status to
         'scheduled'; fire ``internal_announcement/scheduled`` event.
         Inngest worker sleep_until's then sends all three channels
         concurrently.

    4. cancel_announcement(id)
         Status -> 'cancelled'. Worker checks status before each send.

Single-user approval (per the Day-4 sign-off): the creator IS the
approver. We don't route through the `approvals` table — the
announcement's status flow is the audit trail.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any, Iterable

from app.database import get_service_client
from app.services.langfuse import observe
from app.services.llm.client import get_llm_client
from app.services.llm.types import Message

log = logging.getLogger(__name__)


# ── Drafting ─────────────────────────────────────────────────────────────


@observe(name="internal_comms.draft")
async def draft_announcement(
    *,
    org_id: str,
    user_id: str,
    request_text: str,
) -> dict[str, Any]:
    """One LLM call → three channel-appropriate renderings.

    Returns the inserted announcement row so the caller can immediately
    render the three drafts for review.
    """
    request = (request_text or "").strip()
    if not request:
        raise ValueError("request_text is required")

    kb_context = await _gather_kb_context(org_id=org_id, request_text=request)
    drafts = await _draft_multichannel_with_llm(
        request=request, kb_context=kb_context
    )

    svc = get_service_client()

    def _insert() -> dict[str, Any]:
        res = (
            svc.table("internal_announcements")
            .insert(
                {
                    "org_id": org_id,
                    "created_by": user_id,
                    "request_text": request[:4000],
                    "email_subject": drafts["email_subject"][:998],
                    "email_body": drafts["email_body"][:50000],
                    "slack_body": drafts["slack_body"][:40000],
                    "notion_title": drafts["notion_title"][:300],
                    "notion_body": drafts["notion_body"][:80000],
                    "status": "draft",
                }
            )
            .execute()
        )
        rows = res.data or []
        if not rows:
            raise RuntimeError("internal_announcements insert returned no rows")
        return rows[0]

    return await asyncio.to_thread(_insert)


async def _gather_kb_context(*, org_id: str, request_text: str) -> str:
    """Pull relevant org docs to ground the announcement (e.g. Q3 OKRs)."""
    try:
        from app.services.retrieval.hybrid_search import hybrid_search

        results = await hybrid_search(
            query=request_text,
            org_id=org_id,
            client=get_service_client(),
            k=5,
        )
    except Exception as exc:
        log.warning("internal_comms.kb_context_failed err=%s", exc)
        return ""
    if not results:
        return ""
    lines: list[str] = []
    for r in results[:5]:
        name = getattr(r, "document_name", None) or "(doc)"
        content = (getattr(r, "content", "") or "").strip()
        if not content:
            continue
        excerpt = content[:600] + ("…" if len(content) > 600 else "")
        lines.append(f"- ({name}) {excerpt}")
    return "\n".join(lines)


async def _draft_multichannel_with_llm(
    *,
    request: str,
    kb_context: str,
) -> dict[str, str]:
    """Single structured-output call producing email/slack/notion drafts."""
    system = (
        "You are drafting an internal company announcement. The team has asked "
        "for one message that will go out across three channels. Each rendering "
        "should be channel-appropriate: a polished email body, a concise Slack "
        "post, and a structured Notion page. Use any KB excerpts to ground "
        "specific facts; do not invent dates, numbers, or names."
    )
    user_prompt = (
        f"Admin's request: {request}\n\n"
        f"KB excerpts:\n{kb_context or '(none — write generally)'}\n\n"
        "Return STRICT JSON in this shape:\n"
        "{\n"
        '  "email_subject": "...",\n'
        '  "email_body": "Markdown body for email. Conversational but professional.",\n'
        '  "slack_body": "Slack message (Slack mrkdwn). Tight; <300 words. '
        'Bullets ok, headings via *bold* not #.",\n'
        '  "notion_title": "Notion page title",\n'
        '  "notion_body": "Markdown body for Notion. Use headings, bullets, '
        'and a short table of contents if useful."\n'
        "}\n"
        "Do not include any text outside the JSON object."
    )

    client = get_llm_client()
    response = await client.complete(
        messages=[Message(role="user", content=user_prompt)],
        system_extra=system,
        temperature=0.4,
    )
    text = (response.text or "").strip()
    return _parse_drafts_json(text)


def _parse_drafts_json(text: str) -> dict[str, str]:
    """Extract a JSON object from the LLM's response. Raises on shape errors."""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL | re.IGNORECASE)
    if fence:
        cleaned = fence.group(1).strip()
    else:
        brace = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if brace:
            cleaned = brace.group(0)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"announcement_llm_invalid_json: {exc}") from exc

    required = (
        "email_subject",
        "email_body",
        "slack_body",
        "notion_title",
        "notion_body",
    )
    if not isinstance(data, dict) or not all(isinstance(data.get(k), str) for k in required):
        raise RuntimeError("announcement_llm_invalid_shape")
    return {k: str(data[k]).strip() for k in required}


# ── Editing ──────────────────────────────────────────────────────────────


async def update_announcement(
    *,
    org_id: str,
    user_id: str,
    announcement_id: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """Mutate one announcement's drafts. Only allowed in 'draft' status."""
    allowed = {
        "email_subject",
        "email_body",
        "slack_body",
        "notion_title",
        "notion_body",
        "request_text",
    }
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return await _fetch_announcement(org_id=org_id, announcement_id=announcement_id) or {}

    svc = get_service_client()

    def _run() -> dict[str, Any]:
        cur = (
            svc.table("internal_announcements")
            .select("status, created_by")
            .eq("id", announcement_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        if not cur or not cur.data:
            raise ValueError("announcement_not_found")
        if cur.data["status"] != "draft":
            raise ValueError(f"announcement_not_editable:{cur.data['status']}")
        if cur.data["created_by"] != user_id:
            raise PermissionError("not_your_announcement")

        res = (
            svc.table("internal_announcements")
            .update(updates)
            .eq("id", announcement_id)
            .execute()
        )
        return (res.data or [{}])[0]

    return await asyncio.to_thread(_run)


# ── Scheduling ───────────────────────────────────────────────────────────


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalise_recipients(emails: Iterable[str] | None) -> list[str]:
    if not emails:
        return []
    out: list[str] = []
    for e in emails:
        e = (e or "").strip()
        if not e or len(e) > 320 or not _EMAIL_RE.match(e):
            continue
        out.append(e)
    # Dedupe preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for e in out:
        if e in seen:
            continue
        seen.add(e)
        deduped.append(e)
    return deduped


async def schedule_announcement(
    *,
    org_id: str,
    user_id: str,
    announcement_id: str,
    scheduled_for: datetime,
    recipients: list[str] | None,
    slack_channel_id: str | None,
    notion_parent_page_id: str | None,
) -> dict[str, Any]:
    """Snapshot destinations, flip status, fire dispatcher.

    At least one channel must be enabled (recipients non-empty, or
    slack_channel_id set, or notion_parent_page_id set) — otherwise the
    announcement has no effect and we reject the schedule.
    """
    cleaned_recipients = _normalise_recipients(recipients)
    if not cleaned_recipients and not slack_channel_id and not notion_parent_page_id:
        raise ValueError("no_channels_enabled")

    svc = get_service_client()

    def _run() -> dict[str, Any]:
        cur = (
            svc.table("internal_announcements")
            .select("status, created_by, email_body, slack_body, notion_body")
            .eq("id", announcement_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        if not cur or not cur.data:
            raise ValueError("announcement_not_found")
        if cur.data["status"] != "draft":
            raise ValueError(f"announcement_not_schedulable:{cur.data['status']}")
        if cur.data["created_by"] != user_id:
            raise PermissionError("not_your_announcement")

        # Sanity: a channel was selected but the corresponding draft is empty?
        # That's almost certainly a UX bug — surface as an explicit error.
        if cleaned_recipients and not (cur.data.get("email_body") or "").strip():
            raise ValueError("email_body_empty")
        if slack_channel_id and not (cur.data.get("slack_body") or "").strip():
            raise ValueError("slack_body_empty")
        if notion_parent_page_id and not (cur.data.get("notion_body") or "").strip():
            raise ValueError("notion_body_empty")

        res = (
            svc.table("internal_announcements")
            .update(
                {
                    "status": "scheduled",
                    "scheduled_for": scheduled_for.isoformat(),
                    "recipients": cleaned_recipients,
                    "slack_channel_id": slack_channel_id,
                    "notion_parent_page_id": notion_parent_page_id,
                }
            )
            .eq("id", announcement_id)
            .execute()
        )
        return (res.data or [{}])[0]

    row = await asyncio.to_thread(_run)

    from app.inngest.client import get_inngest_client
    import inngest as _inn

    client = get_inngest_client()
    await client.send(
        _inn.Event(
            name="internal_announcement/scheduled",
            data={
                "org_id": org_id,
                "announcement_id": announcement_id,
                "user_id": user_id,
            },
        )
    )
    return row


async def cancel_announcement(
    *, org_id: str, user_id: str, announcement_id: str
) -> dict[str, Any]:
    svc = get_service_client()

    def _run() -> dict[str, Any]:
        cur = (
            svc.table("internal_announcements")
            .select("status, created_by")
            .eq("id", announcement_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        if not cur or not cur.data:
            raise ValueError("announcement_not_found")
        # Only the creator can cancel a draft / scheduled announcement.
        if cur.data["created_by"] != user_id:
            raise PermissionError("not_your_announcement")
        if cur.data["status"] in ("sent", "failed", "cancelled"):
            return {"announcement_id": announcement_id, "status": cur.data["status"]}
        svc.table("internal_announcements").update({"status": "cancelled"}).eq(
            "id", announcement_id
        ).execute()
        return {"announcement_id": announcement_id, "status": "cancelled"}

    return await asyncio.to_thread(_run)


# ── Dispatch (called from Inngest worker) ────────────────────────────────


async def dispatch_announcement(*, announcement_id: str) -> dict[str, Any]:
    """Fire all enabled channels concurrently and write the results back.

    Each channel runs in a separate task so a slow Slack post doesn't
    block the email send. Per-channel failures are captured into a
    structured `channel_results` dict; the announcement's terminal
    status is 'sent' if any channel succeeded, 'failed' if none did.
    """
    row = await _fetch_announcement(announcement_id=announcement_id)
    if not row:
        return {"status": "not_found"}
    if row["status"] in ("cancelled", "sent", "failed"):
        return {"status": row["status"], "reason": "non_terminal_required"}
    if row["status"] != "scheduled":
        return {"status": row["status"], "reason": "unexpected_state"}

    # Move to 'sending' first so a retry that races itself doesn't double-send.
    await _set_status(announcement_id=announcement_id, status="sending")

    org_id = row["org_id"]
    recipients: list[str] = row.get("recipients") or []
    slack_channel_id = row.get("slack_channel_id")
    notion_parent_page_id = row.get("notion_parent_page_id")

    email_task = (
        _send_email_channel(
            org_id=org_id,
            announcement_id=announcement_id,
            sender_id=row["created_by"],
            recipients=recipients,
            subject=row.get("email_subject") or "Team announcement",
            body=row.get("email_body") or "",
        )
        if recipients
        else None
    )
    slack_task = (
        _send_slack_channel(
            org_id=org_id,
            channel_id=slack_channel_id,
            text=row.get("slack_body") or "",
        )
        if slack_channel_id
        else None
    )
    notion_task = (
        _send_notion_channel(
            org_id=org_id,
            parent_page_id=notion_parent_page_id,
            title=row.get("notion_title") or "Team announcement",
            content=row.get("notion_body") or "",
        )
        if notion_parent_page_id
        else None
    )

    tasks = [t for t in (email_task, slack_task, notion_task) if t]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    channel_results: dict[str, Any] = {}
    email_sent_count = 0
    slack_ts: str | None = None
    notion_page_url: str | None = None
    any_ok = False
    any_err = False

    idx = 0
    if email_task:
        r = results[idx]
        idx += 1
        if isinstance(r, Exception):
            channel_results["email"] = {"ok": False, "error": str(r)[:500]}
            any_err = True
        else:
            channel_results["email"] = {"ok": True, "sent": r["sent"]}
            email_sent_count = int(r["sent"])
            any_ok = True
    if slack_task:
        r = results[idx]
        idx += 1
        if isinstance(r, Exception):
            channel_results["slack"] = {"ok": False, "error": str(r)[:500]}
            any_err = True
        else:
            slack_ts = r.get("ts")
            channel_results["slack"] = {"ok": True, "ts": slack_ts}
            any_ok = True
    if notion_task:
        r = results[idx]
        idx += 1
        if isinstance(r, Exception):
            channel_results["notion"] = {"ok": False, "error": str(r)[:500]}
            any_err = True
        else:
            notion_page_url = r.get("url")
            channel_results["notion"] = {"ok": True, "url": notion_page_url}
            any_ok = True

    terminal = "sent" if any_ok else "failed"
    error_message = None if any_ok else "All enabled channels failed."

    svc = get_service_client()

    def _finalise() -> None:
        svc.table("internal_announcements").update(
            {
                "status": terminal,
                "sent_at": datetime.utcnow().isoformat() + "Z",
                "email_sent_count": email_sent_count,
                "slack_ts": slack_ts,
                "notion_page_url": notion_page_url,
                "error_message": error_message,
            }
        ).eq("id", announcement_id).execute()

    await asyncio.to_thread(_finalise)

    return {
        "status": terminal,
        "channels": channel_results,
        "any_ok": any_ok,
        "any_err": any_err,
    }


async def _send_email_channel(
    *,
    org_id: str,
    announcement_id: str,
    sender_id: str,
    recipients: list[str],
    subject: str,
    body: str,
) -> dict[str, Any]:
    """Queue one Resend email per recipient via the existing email pipeline.

    We use a per-(recipient, announcement) dedupe key so an Inngest retry
    of dispatch_announcement re-queues each address once, not N times.
    """
    from app.config import get_settings
    from app.services.email import send_email_event
    import markdown as md

    body_html = md.markdown(
        body, extensions=["nl2br", "sane_lists", "fenced_code", "tables"]
    )
    org_name = await _fetch_org_name(org_id)
    sender_name = await _fetch_user_display_name(sender_id)
    settings = get_settings()

    sent = 0
    for addr in recipients:
        try:
            await send_email_event(
                event_type="internal_announcement",
                to=addr,
                user_id=None,
                org_id=org_id,
                dedupe_key=f"announcement:{announcement_id}:{addr}",
                data={
                    "subject": subject,
                    "body_html": body_html,
                    "org_name": org_name or "your team",
                    "sender_name": sender_name,
                    "app_url": settings.app_url,
                },
            )
            sent += 1
        except Exception as exc:
            log.warning(
                "internal_comms.email_queue_failed announcement=%s to=%s err=%s",
                announcement_id,
                addr,
                exc,
            )
            # Continue — partial failure shouldn't tank the whole send.
    return {"sent": sent}


async def _send_slack_channel(
    *, org_id: str, channel_id: str, text: str
) -> dict[str, Any]:
    from app.services.integrations.slack import post_message

    try:
        return await post_message(org_id=org_id, channel_id=channel_id, text=text)
    except PermissionError as exc:
        raise RuntimeError(f"slack_unavailable:{exc}") from exc


async def _send_notion_channel(
    *, org_id: str, parent_page_id: str, title: str, content: str
) -> dict[str, Any]:
    from app.services.integrations.notion import create_page

    try:
        return await create_page(
            org_id=org_id,
            parent_page_id=parent_page_id,
            title=title,
            content=content,
        )
    except PermissionError as exc:
        raise RuntimeError(f"notion_unavailable:{exc}") from exc


# ── Helpers ──────────────────────────────────────────────────────────────


async def _fetch_announcement(*, announcement_id: str, org_id: str | None = None) -> dict[str, Any] | None:
    svc = get_service_client()

    def _run() -> dict[str, Any] | None:
        q = svc.table("internal_announcements").select("*").eq("id", announcement_id)
        if org_id:
            q = q.eq("org_id", org_id)
        res = q.maybe_single().execute()
        return (res.data if res else None) or None

    return await asyncio.to_thread(_run)


async def _set_status(*, announcement_id: str, status: str) -> None:
    svc = get_service_client()

    def _run() -> None:
        svc.table("internal_announcements").update({"status": status}).eq(
            "id", announcement_id
        ).execute()

    await asyncio.to_thread(_run)


async def _fetch_org_name(org_id: str) -> str | None:
    svc = get_service_client()

    def _run() -> str | None:
        res = (
            svc.table("organizations")
            .select("name")
            .eq("id", org_id)
            .maybe_single()
            .execute()
        )
        return (res.data or {}).get("name") if res and res.data else None

    return await asyncio.to_thread(_run)


async def _fetch_user_display_name(user_id: str) -> str | None:
    svc = get_service_client()

    def _run() -> str | None:
        res = (
            svc.table("users")
            .select("display_name")
            .eq("id", user_id)
            .maybe_single()
            .execute()
        )
        return (res.data or {}).get("display_name") if res and res.data else None

    return await asyncio.to_thread(_run)
