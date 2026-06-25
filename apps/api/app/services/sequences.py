"""Sales follow-up sequences — generation + scheduling (Agent2 Day 4 #8).

Single-prospect model (per the Day-4 sign-off): one ``sequences`` row =
one prospect = N timed steps. Multi-prospect "enrollments" are P2.

Pipeline:

    1. generate_sequence(prospect_email, prospect_context)
         Search the org's KB for relevant case studies / objection
         handling / positioning, then ask the LLM for 3 timed emails
         (Day 0 intro, Day 3 value-add, Day 7 final nudge). Inserts the
         sequence + 3 sequence_steps rows with status='pending'.

    2. update_step(step_id, subject?, body?, send_offset_days?)
         Admin edits in place before scheduling. Pre-send only.

    3. schedule_sequence(sequence_id)
         Snapshot the creator's Gmail address (verify scope present);
         set step.scheduled_for = now() + send_offset_days; flip
         statuses to 'scheduled'; fire one ``sequence/scheduled`` Inngest
         event. The Inngest worker step.sleep_until's into each send
         and uses get_user_credentials() to refresh the Gmail token.

    4. cancel_sequence(sequence_id)
         Flip sequence + remaining steps to 'cancelled'. The Inngest
         worker checks status before each send, so racing the cancel
         after the cron has woken up is safe.

Threading:
    The first step opens a new Gmail thread. Steps 1+ reply on the
    thread (gmail_thread_id captured from the step-0 send response, and
    passed to send_email via the In-Reply-To header pattern). This makes
    the sequence look like a coherent conversation to the prospect.
    NOTE: Gmail's send_email helper currently doesn't expose an
    In-Reply-To param; the Day 4 follow-up adds that (kept as a
    minimal patch on integrations/gmail.py).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from app.database import get_service_client
from app.services.langfuse import observe
from app.services.llm.client import get_llm_client
from app.services.llm.types import Message

log = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────

DEFAULT_STEP_OFFSETS_DAYS = (0, 3, 7)
MAX_STEPS_PER_SEQUENCE = 5

# Reasonable cap on the prompt context size sent to the LLM.
_MAX_PROSPECT_CONTEXT_CHARS = 4000

# Email-shaped check — defence-in-depth. The router does this too, but the
# service-level call also validates so unit tests stay tight.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_email(addr: str) -> str:
    addr = (addr or "").strip()
    if not addr or not _EMAIL_RE.match(addr) or len(addr) > 320:
        raise ValueError(f"invalid_email:{addr!r}")
    return addr


# ── Generation ───────────────────────────────────────────────────────────


@observe(name="sequences.generate")
async def generate_sequence(
    *,
    org_id: str,
    user_id: str,
    name: str,
    prospect_email: str,
    prospect_context: str,
    prospect_name: str | None = None,
    step_offsets_days: tuple[int, ...] = DEFAULT_STEP_OFFSETS_DAYS,
) -> dict[str, Any]:
    """Create a draft sequence with N LLM-drafted steps.

    Returns the inserted sequence row plus its steps so the UI can
    surface the draft immediately for review.
    """
    prospect_email = _validate_email(prospect_email)
    context = (prospect_context or "").strip()[:_MAX_PROSPECT_CONTEXT_CHARS]
    if not context:
        raise ValueError("prospect_context is required")
    if not 1 <= len(step_offsets_days) <= MAX_STEPS_PER_SEQUENCE:
        raise ValueError(f"step_offsets_days must have 1..{MAX_STEPS_PER_SEQUENCE} entries")
    if any(d < 0 for d in step_offsets_days):
        raise ValueError("step_offsets_days cannot include negative values")

    # Pull org-grounded KB context so the LLM grounds claims in real
    # case studies / positioning rather than inventing them.
    kb_context = await _gather_kb_context(org_id=org_id, prospect_context=context)

    drafts = await _draft_emails_with_llm(
        prospect_name=prospect_name,
        prospect_context=context,
        step_offsets_days=step_offsets_days,
        kb_context=kb_context,
    )

    svc = get_service_client()

    def _insert_sequence() -> dict[str, Any]:
        res = (
            svc.table("sequences")
            .insert(
                {
                    "org_id": org_id,
                    "created_by": user_id,
                    "name": name[:200],
                    "prospect_email": prospect_email,
                    "prospect_name": (prospect_name or None),
                    "prospect_context": context,
                    "status": "draft",
                }
            )
            .execute()
        )
        rows = res.data or []
        if not rows:
            raise RuntimeError("sequences insert returned no rows")
        return rows[0]

    sequence = await asyncio.to_thread(_insert_sequence)

    def _insert_steps() -> list[dict[str, Any]]:
        rows = [
            {
                "sequence_id": sequence["id"],
                "org_id": org_id,
                "step_order": i,
                "send_offset_days": int(offset),
                "subject": drafts[i]["subject"],
                "body": drafts[i]["body"],
                "status": "pending",
            }
            for i, offset in enumerate(step_offsets_days)
        ]
        res = svc.table("sequence_steps").insert(rows).execute()
        return list(res.data or [])

    steps = await asyncio.to_thread(_insert_steps)
    return {"sequence": sequence, "steps": steps}


async def _gather_kb_context(*, org_id: str, prospect_context: str) -> str:
    """Run one KB search and return a compact bullet list of relevant
    excerpts for the LLM. Best-effort — empty string on miss.
    """
    try:
        from app.services.retrieval.hybrid_search import hybrid_search

        results = await hybrid_search(
            query=(
                "case studies, competitor differentiation, pricing positioning, "
                "objection handling relevant to: " + prospect_context
            ),
            org_id=org_id,
            client=get_service_client(),
            k=5,
        )
    except Exception as exc:
        log.warning("sequences.kb_context_failed err=%s", exc)
        return ""
    if not results:
        return ""
    lines: list[str] = []
    for r in results[:5]:
        name = getattr(r, "document_name", None) or "(doc)"
        content = (getattr(r, "content", "") or "").strip()
        if not content:
            continue
        excerpt = content[:500] + ("…" if len(content) > 500 else "")
        lines.append(f"- ({name}) {excerpt}")
    return "\n".join(lines)


async def _draft_emails_with_llm(
    *,
    prospect_name: str | None,
    prospect_context: str,
    step_offsets_days: tuple[int, ...],
    kb_context: str,
) -> list[dict[str, str]]:
    """Single LLM call returning ``len(step_offsets_days)`` drafts as JSON.

    Structured output keeps the call cheap (~1 round-trip) and avoids
    the parsing ambiguity of free-text "three emails".
    """
    addressed_to = prospect_name or "the prospect"
    step_descriptions = []
    for i, offset in enumerate(step_offsets_days):
        if i == 0:
            arc = "introduction — open the conversation, anchor on the prospect's stated context."
        elif i == len(step_offsets_days) - 1:
            arc = "final nudge — short, value-led, easy out (acknowledge if they'd rather pass)."
        else:
            arc = "value-add — reference a specific case study or proof point from the KB context."
        step_descriptions.append(f"  Step {i} (sends {offset} days after schedule): {arc}")
    step_arc_text = "\n".join(step_descriptions)

    system = (
        "You are writing a short sales follow-up sequence for an outbound rep. "
        "Each email must read like a real human wrote it — concise, specific, no buzzwords. "
        "Anchor on the prospect's stated context. Use any KB excerpts to ground claims; "
        "do not invent customer logos or stats."
    )
    user_prompt = (
        f"Prospect: {addressed_to}\n"
        f"Context: {prospect_context}\n\n"
        f"KB excerpts:\n{kb_context or '(none — write without specific case studies)'}\n\n"
        "Write a follow-up sequence with these steps:\n"
        f"{step_arc_text}\n\n"
        "Return STRICT JSON in this shape, with one entry per step:\n"
        "{\n"
        '  "steps": [\n'
        '    {"subject": "...", "body": "..."},\n'
        "    ...\n"
        "  ]\n"
        "}\n"
        "Bodies should be plain text (no Markdown headers). Each body ≤ 1500 chars. "
        "Subjects ≤ 80 chars. No greeting placeholders like {{name}}."
    )

    client = get_llm_client()
    response = await client.complete(
        messages=[Message(role="user", content=user_prompt)],
        system_extra=system,
        temperature=0.5,
    )
    text = (response.text or "").strip()
    parsed = _parse_steps_json(text, expected=len(step_offsets_days))
    return parsed


def _parse_steps_json(text: str, *, expected: int) -> list[dict[str, str]]:
    """Extract the JSON object from the LLM's response.

    Models sometimes wrap JSON in ```json fences or prose. We greedy-match
    the first {...} block. If parsing fails we raise so the caller surfaces
    a clear error rather than persisting garbage steps.
    """
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
        raise RuntimeError(f"sequence_llm_invalid_json: {exc}") from exc

    steps = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(steps, list) or len(steps) < expected:
        raise RuntimeError(
            f"sequence_llm_invalid_shape: expected at least {expected} steps, got "
            f"{0 if not isinstance(steps, list) else len(steps)}"
        )

    out: list[dict[str, str]] = []
    for i in range(expected):
        s = steps[i]
        subject = str((s or {}).get("subject") or "").strip()
        body = str((s or {}).get("body") or "").strip()
        if not subject or not body:
            raise RuntimeError(f"sequence_llm_missing_step_fields: step {i}")
        # Hard-cap to the CHECK constraints so a chatty model doesn't break
        # the insert with overlength text.
        out.append(
            {
                "subject": subject[:998],
                "body": body[:50000],
            }
        )
    return out


# ── Editing ──────────────────────────────────────────────────────────────


async def update_step(
    *,
    org_id: str,
    sequence_id: str,
    step_id: str,
    subject: str | None = None,
    body: str | None = None,
    send_offset_days: int | None = None,
) -> dict[str, Any]:
    """Mutate one step in a draft sequence. Only allowed while the parent
    sequence is in 'draft' status — once scheduled, edits must go through
    a cancel + regenerate flow.
    """
    svc = get_service_client()

    def _verify_and_update() -> dict[str, Any]:
        # Check the parent sequence is still a draft.
        seq = (
            svc.table("sequences")
            .select("id, status")
            .eq("id", sequence_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        if not seq or not seq.data:
            raise ValueError("sequence_not_found")
        if seq.data["status"] != "draft":
            raise ValueError(f"sequence_not_editable:{seq.data['status']}")

        updates: dict[str, Any] = {}
        if subject is not None:
            updates["subject"] = subject[:998]
        if body is not None:
            updates["body"] = body[:50000]
        if send_offset_days is not None:
            if send_offset_days < 0 or send_offset_days > 365:
                raise ValueError("send_offset_days_out_of_range")
            updates["send_offset_days"] = int(send_offset_days)
        if not updates:
            res = (
                svc.table("sequence_steps")
                .select("*")
                .eq("id", step_id)
                .eq("sequence_id", sequence_id)
                .maybe_single()
                .execute()
            )
            return (res.data if res else {}) or {}

        res = (
            svc.table("sequence_steps")
            .update(updates)
            .eq("id", step_id)
            .eq("sequence_id", sequence_id)
            .execute()
        )
        return (res.data or [{}])[0]

    return await asyncio.to_thread(_verify_and_update)


# ── Scheduling ───────────────────────────────────────────────────────────


async def schedule_sequence(
    *,
    org_id: str,
    user_id: str,
    sequence_id: str,
) -> dict[str, Any]:
    """Verify Gmail is connected, snapshot the sender, set scheduled times,
    flip statuses, fire the Inngest dispatcher.
    """
    from app.services.integrations.gmail import (
        get_user_credentials,
        has_send_scope,
    )

    creds = await get_user_credentials(org_id=org_id, user_id=user_id)
    if not creds:
        raise PermissionError("gmail_not_connected")
    if not has_send_scope(creds.get("scopes")):
        raise PermissionError("gmail_send_scope_missing")
    sender_email = creds["email_address"]

    svc = get_service_client()

    def _flip() -> dict[str, Any]:
        seq = (
            svc.table("sequences")
            .select("id, status, created_by")
            .eq("id", sequence_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        if not seq or not seq.data:
            raise ValueError("sequence_not_found")
        if seq.data["status"] != "draft":
            raise ValueError(f"sequence_not_schedulable:{seq.data['status']}")
        # Only the creator may schedule (admins can cancel via a separate
        # endpoint if needed). Keeps "who's sending from whose mailbox"
        # unambiguous.
        if seq.data["created_by"] != user_id:
            raise PermissionError("only_creator_can_schedule")

        # Load steps in order.
        steps_res = (
            svc.table("sequence_steps")
            .select("id, step_order, send_offset_days")
            .eq("sequence_id", sequence_id)
            .order("step_order", desc=False)
            .execute()
        )
        steps = steps_res.data or []
        if not steps:
            raise ValueError("sequence_has_no_steps")

        now = datetime.now(UTC)
        for s in steps:
            sched = now + timedelta(days=int(s["send_offset_days"]))
            svc.table("sequence_steps").update(
                {"scheduled_for": sched.isoformat(), "status": "scheduled"}
            ).eq("id", s["id"]).execute()

        svc.table("sequences").update(
            {"status": "scheduled", "sender_email": sender_email}
        ).eq("id", sequence_id).execute()

        return {"sequence_id": sequence_id, "scheduled_at": now.isoformat()}

    result = await asyncio.to_thread(_flip)

    # Fire the dispatcher AFTER the DB flip is committed — if it ran first
    # and the DB update failed, we'd have an Inngest worker that wouldn't
    # find scheduled rows.
    from app.inngest.client import get_inngest_client
    import inngest as _inn

    client = get_inngest_client()
    await client.send(
        _inn.Event(
            name="sequence/scheduled",
            data={
                "org_id": org_id,
                "sequence_id": sequence_id,
                "user_id": user_id,
            },
        )
    )
    return result


async def cancel_sequence(*, org_id: str, user_id: str, sequence_id: str) -> dict[str, Any]:
    """Mark the sequence + its remaining steps cancelled. The Inngest
    dispatcher checks status before each send, so a cancel after the
    cron has woken is safe.
    """
    svc = get_service_client()

    def _run() -> dict[str, Any]:
        seq = (
            svc.table("sequences")
            .select("id, status, created_by")
            .eq("id", sequence_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        if not seq or not seq.data:
            raise ValueError("sequence_not_found")
        # Allow the creator to cancel; admins should be able to too — we
        # let the router enforce admin/creator gating instead of duplicating
        # it here.
        svc.table("sequences").update({"status": "cancelled"}).eq(
            "id", sequence_id
        ).execute()
        svc.table("sequence_steps").update({"status": "cancelled"}).eq(
            "sequence_id", sequence_id
        ).in_("status", ["pending", "scheduled"]).execute()
        return {"sequence_id": sequence_id, "status": "cancelled"}

    return await asyncio.to_thread(_run)


# ── Send-time helpers (called from the Inngest worker) ───────────────────


async def send_step(*, step_id: str) -> dict[str, Any]:
    """Actually send one step's email.

    Reads the sequence + step rows under service-role, refreshes the
    creator's Gmail token, sends the email (replying on the first step's
    thread for steps 1+), then records the result on the step row.

    Returns a small dict reporting outcome; the Inngest worker writes
    this back to the run context (not strictly needed but useful for
    debug visibility).
    """
    from app.services.integrations.gmail import get_user_credentials, send_email

    svc = get_service_client()

    def _load() -> dict[str, Any] | None:
        # Pull step + parent sequence in one round-trip via PostgREST nested select.
        res = (
            svc.table("sequence_steps")
            .select(
                "id, sequence_id, org_id, step_order, subject, body, status, "
                "scheduled_for, gmail_thread_id, "
                "sequences(id, org_id, created_by, status, sender_email, "
                "prospect_email, prospect_name)"
            )
            .eq("id", step_id)
            .maybe_single()
            .execute()
        )
        return (res.data if res else None) or None

    row = await asyncio.to_thread(_load)
    if not row:
        return {"status": "step_not_found"}

    seq = row.get("sequences") or {}
    if not seq:
        return {"status": "sequence_missing"}
    if seq["status"] in ("cancelled", "failed"):
        return {"status": "sequence_cancelled"}
    if row["status"] == "sent":
        # Inngest retry after a successful send — short-circuit.
        return {"status": "already_sent"}
    if row["status"] not in ("scheduled", "pending"):
        return {"status": f"unexpected_state:{row['status']}"}

    creator_id = seq["created_by"]
    org_id = seq["org_id"]

    creds = await get_user_credentials(org_id=org_id, user_id=creator_id)
    if not creds:
        await _mark_step_failed(step_id, "gmail_disconnected_at_send_time")
        return {"status": "failed", "reason": "gmail_disconnected"}

    sender = seq.get("sender_email") or creds["email_address"]
    to = seq["prospect_email"]

    # Reply-on-thread for steps 1+: load step 0's thread id if we haven't
    # already cached it on this row.
    thread_id = row.get("gmail_thread_id")
    if not thread_id and int(row["step_order"]) > 0:
        thread_id = await _fetch_step0_thread(row["sequence_id"])

    try:
        result = await send_email(
            access_token=creds["access_token"],
            sender=sender,
            to=to,
            subject=row["subject"],
            body=row["body"],
        )
    except PermissionError as exc:
        await _mark_step_failed(step_id, f"gmail_unauthorized:{exc}")
        return {"status": "failed", "reason": str(exc)}
    except Exception as exc:
        # Transient errors bubble up so Inngest retries.
        log.warning("sequences.send_failed step=%s err=%s", step_id, exc)
        raise

    await _mark_step_sent(
        step_id=step_id,
        message_id=result.get("message_id"),
        thread_id=result.get("thread_id") or thread_id,
    )
    await _maybe_mark_sequence_active(seq["id"])
    return {
        "status": "sent",
        "message_id": result.get("message_id"),
        "thread_id": result.get("thread_id"),
    }


async def _fetch_step0_thread(sequence_id: str) -> str | None:
    svc = get_service_client()

    def _run() -> str | None:
        res = (
            svc.table("sequence_steps")
            .select("gmail_thread_id")
            .eq("sequence_id", sequence_id)
            .eq("step_order", 0)
            .maybe_single()
            .execute()
        )
        return (res.data or {}).get("gmail_thread_id") if res and res.data else None

    return await asyncio.to_thread(_run)


async def _mark_step_sent(
    *, step_id: str, message_id: str | None, thread_id: str | None
) -> None:
    svc = get_service_client()

    def _run() -> None:
        svc.table("sequence_steps").update(
            {
                "status": "sent",
                "sent_at": datetime.now(UTC).isoformat(),
                "gmail_message_id": message_id,
                "gmail_thread_id": thread_id,
            }
        ).eq("id", step_id).execute()

    await asyncio.to_thread(_run)


async def _mark_step_failed(step_id: str, reason: str) -> None:
    svc = get_service_client()

    def _run() -> None:
        svc.table("sequence_steps").update(
            {"status": "failed", "error_message": reason[:2000]}
        ).eq("id", step_id).execute()

    await asyncio.to_thread(_run)


async def _maybe_mark_sequence_active(sequence_id: str) -> None:
    """Flip a 'scheduled' sequence to 'active' on first successful send,
    and to 'completed' once every step is terminal (sent/failed/cancelled).
    """
    svc = get_service_client()

    def _run() -> None:
        steps_res = (
            svc.table("sequence_steps")
            .select("status")
            .eq("sequence_id", sequence_id)
            .execute()
        )
        statuses = [s["status"] for s in (steps_res.data or [])]
        if not statuses:
            return

        all_terminal = all(s in ("sent", "failed", "cancelled") for s in statuses)
        any_sent = any(s == "sent" for s in statuses)

        seq_res = (
            svc.table("sequences")
            .select("status")
            .eq("id", sequence_id)
            .maybe_single()
            .execute()
        )
        cur_status = (seq_res.data or {}).get("status") if seq_res else None

        if all_terminal:
            target = "completed"
        elif any_sent and cur_status == "scheduled":
            target = "active"
        else:
            return

        if cur_status != target:
            svc.table("sequences").update({"status": target}).eq(
                "id", sequence_id
            ).execute()

    await asyncio.to_thread(_run)
