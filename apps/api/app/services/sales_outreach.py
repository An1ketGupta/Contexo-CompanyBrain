"""Shared helpers for the sales outreach agent and its admin router.

Both the agent's autonomous send path and the admin's approve-and-send
endpoint have to resolve the same mailbox, apply the same "does this mention
money" test, and advance the same follow-up clock. They live here so the two
paths can't drift — a divergence between them would mean a human-approved
send and an agent send behaving differently, which is exactly the kind of
inconsistency that erodes trust in the approval gate.
"""
from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from app.observability import get_logger
from app.services.integrations import gmail

log = get_logger(__name__)

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "mode": "assisted",
    "tone": None,
    "follow_up_delay_days": 3,
    "max_follow_ups": 3,
    "daily_send_cap": 20,
    "escalation_channel_id": None,
    "escalation_channel_name": None,
}

# Terms that make a draft commercially binding-ish. Matching any of these
# forces human review no matter what trust mode the org picked: a wrong price
# or a term we didn't agree to is not a mistake a confidence score can catch,
# and unlike a clumsy sentence it can't be walked back after it's sent.
_PRICING_RE = re.compile(
    r"\b(pric(?:e|es|ing)|cost|costs|quote|discount|"
    r"\$|usd|eur|inr|per\s+seat|per\s+user|per\s+month|/mo\b|/month\b|"
    r"contract|invoic(?:e|ing)|billing|refund|"
    r"annual\s+plan|subscription\s+fee|payment\s+terms|sla\b)\b",
    re.IGNORECASE,
)

_SUBJECT_LINE_RE = re.compile(r"^\s*subject\s*:\s*(.+?)\s*$", re.IGNORECASE)
_RE_PREFIX_RE = re.compile(r"^\s*re\s*:\s*", re.IGNORECASE)


def mentions_pricing(*texts: str | None) -> bool:
    """True if any of the given strings touches money or contract terms."""
    return any(_PRICING_RE.search(t) for t in texts if t)


def split_subject_body(raw: str, *, fallback_subject: str) -> tuple[str, str]:
    """Pull `Subject: ...` off the front of a drafted email.

    The LLM is asked for that format but isn't trusted to obey it — a draft
    that skips the header still has a usable body, so we fall back rather
    than fail the run over a formatting miss.
    """
    lines = (raw or "").strip().splitlines()
    for idx, line in enumerate(lines[:3]):
        m = _SUBJECT_LINE_RE.match(line)
        if m:
            subject = m.group(1).strip()
            body = "\n".join(lines[idx + 1:]).strip()
            if subject and body:
                return subject[:300], body
    return fallback_subject[:300], (raw or "").strip()


def reply_subject(subject: str | None) -> str:
    """`Re:`-prefix a subject without stacking prefixes on a long thread."""
    base = (subject or "").strip() or "Following up"
    if _RE_PREFIX_RE.match(base):
        return base[:300]
    return f"Re: {base}"[:300]


async def load_settings(client: Any, org_id: str) -> dict[str, Any]:
    """Read sales_settings, filling defaults for an org that never saved any."""
    row = await asyncio.to_thread(
        lambda: client.table("sales_settings")
        .select("*").eq("org_id", org_id).maybe_single().execute()
    )
    if row and row.data:
        return {**DEFAULT_SETTINGS, **row.data}
    return {"org_id": org_id, **DEFAULT_SETTINGS}




def next_follow_up_at(settings: dict[str, Any], *, now: datetime | None = None) -> str:
    """When the cadence cron should next pick this lead up."""
    days = int(settings.get("follow_up_delay_days") or 3)
    return ((now or datetime.now(UTC)) + timedelta(days=days)).isoformat()


async def mark_contacted(
    client: Any, *, lead_id: str, lead: dict[str, Any], settings: dict[str, Any],
) -> None:
    """Advance a lead after an outbound message actually left the building."""
    now = datetime.now(UTC).isoformat()
    await asyncio.to_thread(
        lambda: client.table("sales_leads").update({
            "status": "awaiting_reply",
            "first_contacted_at": lead.get("first_contacted_at") or now,
            "last_contacted_at": now,
            "next_follow_up_at": next_follow_up_at(settings),
        }).eq("id", lead_id).execute()
    )


async def notify_escalation(
    *, org_id: str, settings: dict[str, Any], text: str,
) -> None:
    """Best-effort Slack ping. Never raises — a missing notification must not
    fail a run whose draft is already safely queued in the review inbox."""
    channel_id = settings.get("escalation_channel_id")
    if not channel_id:
        return
    try:
        from app.services.integrations import slack as slack_service

        await slack_service.post_message(org_id=org_id, channel_id=channel_id, text=text)
    except Exception as exc:
        log.warning("sales_slack_notify_failed org=%s err=%s", org_id, exc)


def score_confidence(sources: list[dict]) -> float:
    """Heuristic, not a model output: zero sources -> 0.0, which forces human
    review upstream. Otherwise a blend of the strongest match and how many
    chunks corroborate it. Same shape as the support agent's scorer so the two
    queues' confidence numbers mean the same thing to a reviewer."""
    if not sources:
        return 0.0
    top = max((s.get("similarity") or s.get("vector_similarity") or 0.0) for s in sources)
    breadth = min(len(sources) / 3, 1.0)
    return round(min(1.0, top * 0.7 + breadth * 0.3), 3)
