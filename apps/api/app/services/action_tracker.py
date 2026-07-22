"""Action Tracker service (#44, Agent2 Day 6).

Two flows:

  extract_action_items(notes, meeting_id?)
    Gemini structures pasted notes into [{action_text, owner, due_date}].
    Owner names get fuzzy-matched against the org's users via display_name +
    email local-part. Persists to action_items with status='pending'.

  post_action_items_to_slack(action_item_ids, channel_id)
    Formats the extracted items into a single mrkdwn message and posts it to
    the chosen Slack channel via the Slack adapter.
"""
from __future__ import annotations

import asyncio
import difflib
import logging
from datetime import UTC, datetime
from typing import Any

from app.database import get_service_client
from app.services.agents.kb_synthesis import synthesize_json
from app.services.integrations import slack as slack_service

log = logging.getLogger(__name__)


# ── Extract ─────────────────────────────────────────────────────────────────


_EXTRACT_SYSTEM = """You are an executive note-taker. Extract every actionable item from the meeting notes below.

An "action" is something a specific person committed to do by a specific time. Skip discussion points, retro items, and FYIs.

Rules:
- Keep the action wording close to the speaker's. Don't paraphrase ("Owner will follow up with Acme" → "follow up with Acme").
- If the owner is unclear, leave it null. Don't guess.
- Due date format: YYYY-MM-DD. If a relative ("end of week", "next Tuesday"), resolve against today's date — provided in the prompt.
- If no due date is mentioned, leave it null.

Output JSON only:
{
  "action_items": [
    {"action_text": "string", "owner_raw": "string|null", "due_date": "YYYY-MM-DD|null"}
  ]
}
""".strip()


async def extract_action_items(
    *,
    org_id: str,
    user_id: str,
    notes: str,
    source_meeting_id: str | None = None,
) -> list[dict[str, Any]]:
    """Returns the inserted action_items rows."""
    today = datetime.now(UTC).date().isoformat()
    prompt = (
        f"## Today's date\n{today}\n\n"
        f"## Notes\n{notes[:20000]}\n\n"
        "## Output\nJSON only per the schema in your system prompt."
    )
    result = await synthesize_json(
        system_prompt=_EXTRACT_SYSTEM,
        user_prompt=prompt,
        temperature=0.1,
        timeout=60.0,
    )
    if not isinstance(result, dict):
        raise RuntimeError("action_extract_returned_non_object")

    raw_items = result.get("action_items") or []
    if not isinstance(raw_items, list) or not raw_items:
        return []

    # Fuzzy match owners against org members.
    members = await _fetch_org_members(org_id)

    svc = get_service_client()
    inserts: list[dict[str, Any]] = []
    for item in raw_items:
        action_text = (item.get("action_text") or "").strip()
        if not action_text:
            continue
        owner_raw = (item.get("owner_raw") or "").strip() or None
        due_date = item.get("due_date") or None
        owner_user_id = _match_owner(owner_raw, members) if owner_raw else None
        inserts.append(
            {
                "org_id": org_id,
                "created_by": user_id,
                "source_meeting_id": source_meeting_id,
                "source_notes": notes[:5000],
                "action_text": action_text[:2000],
                "owner_user_id": owner_user_id,
                "owner_name_raw": owner_raw,
                "due_date": due_date,
                "status": "pending",
            }
        )

    if not inserts:
        return []

    def _insert() -> list[dict[str, Any]]:
        res = svc.table("action_items").insert(inserts).execute()
        return res.data or []

    return await asyncio.to_thread(_insert)


async def _fetch_org_members(org_id: str) -> list[dict[str, Any]]:
    """Org members decorated with their auth email.

    `users` only carries the profile (id, display_name); the email is owned by
    Supabase Auth (`auth.users`). We fetch profiles from the table and merge in
    emails from `auth.admin.list_users()` so `_match_owner` can fall back to the
    email local-part. If the auth lookup fails we still return profiles so
    display-name matching keeps working.
    """
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("users")
        .select("id, display_name")
        .eq("org_id", org_id)
        .execute()
    )
    members = res.data or []
    if not members:
        return []

    emails_by_id = await _fetch_auth_emails()
    for m in members:
        m["email"] = emails_by_id.get(m["id"], "")
    return members


async def _fetch_auth_emails() -> dict[str, str]:
    """Map user_id → email from Supabase Auth. Best-effort; {} on failure."""
    svc = get_service_client()
    try:
        result = await asyncio.to_thread(lambda: svc.auth.admin.list_users())
    except Exception as exc:
        log.warning("action_tracker_auth_list_users_failed: %s", exc)
        return {}
    iterable: Any = getattr(result, "users", None) or result or []
    out: dict[str, str] = {}
    for u in iterable:
        u_id = getattr(u, "id", None) or (u.get("id") if isinstance(u, dict) else None)
        u_email = getattr(u, "email", None) or (
            u.get("email") if isinstance(u, dict) else None
        )
        if u_id and u_email:
            out[u_id] = u_email
    return out


def _match_owner(
    owner_raw: str,
    members: list[dict[str, Any]],
) -> str | None:
    """Best-match the owner string against org members.

    Try in order:
      1. Exact display_name (case-insensitive).
      2. Exact email local-part match.
      3. difflib best-match against display_name above 0.7 ratio.

    Returns the user_id or None.
    """
    raw = (owner_raw or "").strip().lower()
    if not raw:
        return None
    by_name: dict[str, str] = {}
    by_local: dict[str, str] = {}
    for m in members:
        name = (m.get("display_name") or "").strip().lower()
        if name:
            by_name[name] = m["id"]
        email = (m.get("email") or "").strip().lower()
        if "@" in email:
            local = email.split("@", 1)[0]
            by_local[local] = m["id"]

    if raw in by_name:
        return by_name[raw]
    if raw in by_local:
        return by_local[raw]
    candidates = list(by_name.keys())
    if candidates:
        best = difflib.get_close_matches(raw, candidates, n=1, cutoff=0.75)
        if best:
            return by_name[best[0]]
    return None


# ── Post to Slack ────────────────────────────────────────────────────────────


async def post_action_items_to_slack(
    *,
    org_id: str,
    action_item_ids: list[str],
    channel_id: str,
) -> dict[str, Any]:
    """Post the given action items to a Slack channel as one mrkdwn message.

    Returns {"posted": <count>, "ts": <slack_ts>}. Raises PermissionError if
    Slack isn't connected or the bot can't post to the channel (the router
    surfaces that as a 400 so the UI can prompt a re-invite/reconnect).
    """
    svc = get_service_client()

    def _fetch() -> list[dict[str, Any]]:
        res = (
            svc.table("action_items")
            .select("id, action_text, owner_name_raw, due_date")
            .in_("id", action_item_ids)
            .eq("org_id", org_id)
            .order("created_at", desc=False)
            .execute()
        )
        return res.data or []

    items = await asyncio.to_thread(_fetch)
    if not items:
        return {"posted": 0, "ts": None}

    lines = ["*Action items*"]
    for item in items:
        text = (item.get("action_text") or "").strip()
        if not text:
            continue
        owner = (item.get("owner_name_raw") or "").strip()
        due = (item.get("due_date") or "").strip() if item.get("due_date") else ""
        meta = " · ".join(p for p in (owner, f"due {due}" if due else "") if p)
        lines.append(f"• {text}" + (f"  _{meta}_" if meta else ""))

    result = await slack_service.post_message(
        org_id=org_id,
        channel_id=channel_id,
        text="\n".join(lines),
    )
    return {"posted": len(items), "ts": result.get("ts")}
