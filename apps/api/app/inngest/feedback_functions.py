"""Output-improvement loop (Agent Roadmap Day 15).

Single Inngest function — `analyze-negative-feedback` — fires on
`message/feedback-negative` (emitted from PATCH /messages/{id}/feedback when
a user thumbs-down an assistant response).

Pipeline:
  1. Load the assistant message + its parent user query.
  2. Ask the LLM to classify the failure mode into one of:
       wrong_tone | missing_context | outdated_policy | hallucination
       | wrong_format | unknown
     and suggest what document, if it existed, would have helped.
  3. Persist the verdict to messages.feedback_analysis.
  4. Roll up the org's negative feedback for the trailing 7 days, grouped
     by failure_reason. If any reason has >= ALERT_THRESHOLD occurrences
     AND we haven't already paged an admin this week for that reason,
     send a weekly-threshold alert email to every admin.

Idempotency:
  * The thumbs-down PATCH fires with event id `feedback-negative-{message_id}`
    so a double-tap collapses to one analysis pass.
  * The admin-alert dedupe uses `feedback-alert-{org_id}-{reason}-{YYYY-WW}`
    so a re-fired analysis in the same week is a no-op.

Why no separate aggregations table:
  The threshold query is one indexed SELECT on messages (see migration
  035). A materialised agg table would add a second source of truth that
  has to be kept in sync. We can introduce it later if the admin dashboard
  needs sub-second queries over 6+ months of feedback.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import inngest

from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.services.llm.task_chain import execute_task_blocking

log = logging.getLogger(__name__)

_inngest_client = get_inngest_client()

# Threshold tuning: 5 same-reason failures in 7 days is the smallest signal
# that warrants paging an admin — anything lower is noise from one annoyed
# user, anything higher fails to catch real quality regressions in time.
_ALERT_THRESHOLD = 5
_ROLLUP_DAYS = 7
_FAILURE_REASONS = {
    "wrong_tone",
    "missing_context",
    "outdated_policy",
    "hallucination",
    "wrong_format",
}


@_inngest_client.create_function(
    fn_id="analyze-negative-feedback",
    trigger=inngest.TriggerEvent(event="message/feedback-negative"),
    retries=2,
    # Tightly capped — feedback classification is best-effort, never on the
    # hot path. One per org at a time keeps token costs predictable.
    concurrency=[inngest.Concurrency(limit=1, key="event.data.org_id", scope="fn")],
)
async def analyze_negative_feedback(ctx: inngest.Context) -> dict[str, Any]:
    step = ctx.step
    data = ctx.event.data
    message_id: str = data["message_id"]
    org_id: str = data["org_id"]

    # ── Step 1: load the message + its parent user query ─────────────
    loaded = await step.run(
        "load-message",
        lambda: _load_message_and_query(message_id=message_id, org_id=org_id),
    )
    if not loaded:
        return {"status": "skipped", "reason": "message_not_found"}

    # If a previous run already classified this message, exit early.
    if loaded.get("feedback_analysis"):
        return {"status": "skipped", "reason": "already_analyzed"}

    # ── Step 2: classify via LLM ─────────────────────────────────────
    analysis = await step.run(
        "classify-failure",
        lambda: _classify(
            org_id=org_id,
            query=loaded["query"],
            response=loaded["content"],
            sources=loaded.get("sources") or [],
        ),
    )

    # ── Step 3: persist analysis ─────────────────────────────────────
    await step.run(
        "persist-analysis",
        lambda: _save_analysis(message_id=message_id, analysis=analysis),
    )

    failure_reason = analysis.get("failure_reason") or "unknown"
    if failure_reason not in _FAILURE_REASONS:
        # LLM hallucinated a category — log and treat as unknown.
        log.info(
            "feedback_analysis_unknown_category msg=%s val=%r",
            message_id, failure_reason,
        )
        failure_reason = "unknown"

    # ── Step 4: weekly threshold check + admin alert ────────────────
    if failure_reason != "unknown":
        count = await step.run(
            "rollup-week",
            lambda: _count_same_reason_this_week(
                org_id=org_id, failure_reason=failure_reason,
            ),
        )
        if count >= _ALERT_THRESHOLD:
            sent = await step.run(
                "alert-admins",
                lambda: _alert_admins(
                    org_id=org_id,
                    failure_reason=failure_reason,
                    count=count,
                    suggested_doc=analysis.get("suggested_doc"),
                ),
            )
            return {
                "status": "analyzed",
                "failure_reason": failure_reason,
                "count": count,
                "alerted_admins": sent,
            }

    return {
        "status": "analyzed",
        "failure_reason": failure_reason,
    }


# ── Helpers (synchronous helpers run inside step.run via lambdas) ──


async def _load_message_and_query(
    *, message_id: str, org_id: str
) -> dict[str, Any] | None:
    """Pull the assistant message + the preceding user message in one trip."""
    svc = get_service_client()
    msg_res = await asyncio.to_thread(
        lambda: svc.table("messages")
        .select(
            "id, conversation_id, content, sources, feedback_analysis, "
            "role, created_at, org_id"
        )
        .eq("id", message_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not msg_res or not msg_res.data:
        return None
    msg = msg_res.data
    if msg.get("role") != "assistant":
        return None

    # User query immediately before this assistant message in the same
    # conversation. We order by created_at descending and take the first.
    prior_res = await asyncio.to_thread(
        lambda: svc.table("messages")
        .select("content, created_at")
        .eq("conversation_id", msg["conversation_id"])
        .eq("role", "user")
        .lt("created_at", msg["created_at"])
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    prior = (prior_res.data or [None])[0]
    return {
        "query": (prior or {}).get("content") or "",
        "content": msg.get("content") or "",
        "sources": msg.get("sources") or [],
        "feedback_analysis": msg.get("feedback_analysis"),
    }


# Strict-ish JSON parsing — gemini occasionally wraps in ```json fences.
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


async def _classify(
    *,
    org_id: str,
    query: str,
    response: str,
    sources: list[Any],
) -> dict[str, Any]:
    src_names: list[str] = []
    for s in sources[:6]:
        if isinstance(s, dict):
            name = s.get("document_name") or s.get("name") or s.get("title")
            if name:
                src_names.append(str(name))
    prompt = (
        "A user gave NEGATIVE feedback on this AI-generated response. "
        "Classify the most likely failure reason.\n\n"
        f"USER QUERY:\n{query[:1000]}\n\n"
        f"AI RESPONSE:\n{response[:1500]}\n\n"
        f"SOURCES USED: {', '.join(src_names) if src_names else 'none'}\n\n"
        "Return ONLY a JSON object (no prose, no markdown fence) with two keys:\n"
        "  failure_reason: exactly one of "
        "    wrong_tone | missing_context | outdated_policy | hallucination | wrong_format\n"
        "  suggested_doc: one short sentence describing a document that, if it "
        "    existed in the knowledge base, would most likely have improved the answer. "
        "    Use empty string if none.\n\n"
        "JSON:"
    )
    result = await execute_task_blocking(
        user_message=prompt,
        org_id=org_id,
        db_client=get_service_client(),
    )
    text = (result.text or "").strip()
    text = _FENCE_RE.sub("", text).strip()
    parsed: dict[str, Any] = {}
    try:
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            parsed = {}
    except Exception:
        log.info("feedback_classify_unparseable raw=%r", text[:200])

    return {
        "failure_reason": (parsed.get("failure_reason") or "unknown")[:40],
        "suggested_doc": (parsed.get("suggested_doc") or "")[:400] or None,
        "analysis_at": datetime.now(UTC).isoformat(),
        "model": "execute_task_blocking",
    }


def _save_analysis(*, message_id: str, analysis: dict[str, Any]) -> dict[str, Any]:
    svc = get_service_client()
    res = svc.table("messages").update(
        {"feedback_analysis": analysis}
    ).eq("id", message_id).execute()
    return {"updated": bool(res.data)}


def _count_same_reason_this_week(
    *, org_id: str, failure_reason: str,
) -> int:
    svc = get_service_client()
    cutoff = (datetime.now(UTC) - timedelta(days=_ROLLUP_DAYS)).isoformat()
    res = svc.table("messages").select(
        "id", count="exact"
    ).eq("org_id", org_id).eq(
        "feedback", "negative"
    ).gte("created_at", cutoff).filter(
        "feedback_analysis->>failure_reason", "eq", failure_reason,
    ).execute()
    return int(res.count or 0)


async def _alert_admins(
    *,
    org_id: str,
    failure_reason: str,
    count: int,
    suggested_doc: str | None,
) -> int:
    """Send the threshold-alert email to every admin in the org.

    Dedupe key includes ISO week, so two analyses tripping the same threshold
    in the same week don't both send. We also stamp `alerted_at` on the
    triggering messages so the admin UI can render an indicator.
    """
    from app.config import get_settings
    from app.services.email import send_email_event

    svc = get_service_client()
    settings = get_settings()

    org_row = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select("id, name")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    if not org_row or not org_row.data:
        return 0

    admins = await asyncio.to_thread(
        lambda: svc.table("users")
        .select("id")
        .eq("org_id", org_id)
        .eq("role", "admin")
        .execute()
    )
    admin_ids = [u["id"] for u in (admins.data or [])]
    if not admin_ids:
        return 0

    # Build the example list — most recent 3 negative-feedback messages of
    # this reason, with a short snippet so admins can immediately see what
    # broke.
    cutoff = (datetime.now(UTC) - timedelta(days=_ROLLUP_DAYS)).isoformat()
    examples_res = await asyncio.to_thread(
        lambda: svc.table("messages")
        .select("id, conversation_id, content, created_at, feedback_analysis")
        .eq("org_id", org_id)
        .eq("feedback", "negative")
        .filter("feedback_analysis->>failure_reason", "eq", failure_reason)
        .gte("created_at", cutoff)
        .order("created_at", desc=True)
        .limit(3)
        .execute()
    )
    examples = [
        {
            "id": e["id"],
            "conversation_id": e.get("conversation_id"),
            "snippet": (e.get("content") or "").strip().replace("\n", " ")[:160],
            "created_at": e["created_at"],
        }
        for e in (examples_res.data or [])
    ]

    iso = datetime.now(UTC).isocalendar()
    week_tag = f"{iso[0]}-W{iso[1]:02d}"
    dedupe_key = f"feedback-alert-{org_id}-{failure_reason}-{week_tag}"

    sent = 0
    for admin_id in admin_ids:
        try:
            au = await asyncio.to_thread(
                lambda i=admin_id: svc.auth.admin.get_user_by_id(i)
            )
            email = getattr(getattr(au, "user", None), "email", None)
        except Exception:
            email = None
        if not email:
            continue
        try:
            await send_email_event(
                event_type="feedback_threshold_alert",
                to=email,
                user_id=admin_id,
                org_id=org_id,
                dedupe_key=dedupe_key,
                data={
                    "org_name": org_row.data.get("name") or "your workspace",
                    "failure_reason": failure_reason,
                    "failure_reason_label": _humanise_reason(failure_reason),
                    "count": count,
                    "rollup_days": _ROLLUP_DAYS,
                    "suggested_doc": suggested_doc,
                    "examples": examples,
                    "app_url": settings.app_url,
                },
            )
            sent += 1
        except Exception as exc:
            log.warning(
                "feedback_alert_send_failed admin=%s err=%s", admin_id, exc,
            )

    return sent


def _humanise_reason(reason: str) -> str:
    return {
        "wrong_tone": "Wrong tone",
        "missing_context": "Missing context",
        "outdated_policy": "Outdated policy",
        "hallucination": "Hallucination",
        "wrong_format": "Wrong format",
    }.get(reason, reason.replace("_", " ").title())


FUNCTIONS = [analyze_negative_feedback]
