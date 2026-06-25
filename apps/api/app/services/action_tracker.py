"""Action Tracker service (#44, Agent2 Day 6).

Three flows:

  extract_action_items(notes, meeting_id?)
    Gemini structures pasted notes into [{action_text, owner, due_date}].
    Owner names get fuzzy-matched against the org's users via display_name +
    email local-part. Persists to action_items with status='pending'.

  create_tracked_tasks(action_item_ids, target)
    For each action, call the matching adapter (Notion / Asana / Linear).
    Records external_provider + external_id + external_url back on the row,
    flips status to 'tracked'.

  check_incomplete_actions()
    Daily cron. For tracked items past due_date and not completed, poll the
    external system for completion status. Mark 'overdue' or 'completed'.
    Sends a Slack/in-app reminder for newly-overdue items.
"""
from __future__ import annotations

import asyncio
import difflib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from app.database import get_service_client
from app.services.agents.kb_synthesis import synthesize_json
from app.services.integrations import asana as asana_svc
from app.services.integrations import jira as jira_svc
from app.services.integrations import linear as linear_svc
from app.services.integrations import notion as notion_svc
from app.services.notifications import create_notification

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
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("users")
        .select("id, display_name, email")
        .eq("org_id", org_id)
        .execute()
    )
    return res.data or []


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


# ── Create tracked tasks ────────────────────────────────────────────────────


TaskProvider = Literal["notion", "asana", "linear", "jira"]


async def create_tracked_tasks(
    *,
    org_id: str,
    user_id: str,
    action_item_ids: list[str],
    target: TaskProvider,
    notion_parent_page_id: str | None = None,
) -> list[dict[str, Any]]:
    """For each action item, create an external task. Returns the updated rows."""
    svc = get_service_client()

    def _fetch() -> list[dict[str, Any]]:
        res = (
            svc.table("action_items")
            .select("*")
            .in_("id", action_item_ids)
            .eq("org_id", org_id)
            .execute()
        )
        return res.data or []

    items = await asyncio.to_thread(_fetch)
    if not items:
        return []

    # Pull owner emails for assignee mapping (Asana/Linear).
    member_lookup: dict[str, dict[str, Any]] = {}
    if any(i.get("owner_user_id") for i in items):
        members = await _fetch_org_members(org_id)
        member_lookup = {m["id"]: m for m in members}

    updated: list[dict[str, Any]] = []
    for item in items:
        if item.get("status") == "tracked" and item.get("external_id"):
            updated.append(item)
            continue  # idempotent — skip already-tracked
        owner_email = None
        if item.get("owner_user_id"):
            owner_email = (member_lookup.get(item["owner_user_id"]) or {}).get("email")
        try:
            ext = await _create_one(
                target=target,
                org_id=org_id,
                user_id=user_id,
                action_text=item["action_text"],
                notes=item.get("source_notes") or "",
                owner_email=owner_email,
                due_date=item.get("due_date"),
                notion_parent_page_id=notion_parent_page_id,
            )
        except Exception as exc:
            log.warning("action.create_task.failed id=%s err=%s", item["id"], exc)
            continue

        def _update(item_id=item["id"], ext=ext) -> dict[str, Any]:
            res = (
                svc.table("action_items")
                .update(
                    {
                        "external_provider": target,
                        "external_id": ext.get("task_id"),
                        "external_url": ext.get("url"),
                        "status": "tracked",
                    }
                )
                .eq("id", item_id)
                .execute()
            )
            return (res.data or [{}])[0]

        row = await asyncio.to_thread(_update)
        if row:
            updated.append(row)
    return updated


async def _create_one(
    *,
    target: TaskProvider,
    org_id: str,
    user_id: str,
    action_text: str,
    notes: str,
    owner_email: str | None,
    due_date: str | None,
    notion_parent_page_id: str | None,
) -> dict[str, Any]:
    if target == "notion":
        if not notion_parent_page_id:
            raise PermissionError("notion_parent_required")
        content_lines = [f"# {action_text}", ""]
        if owner_email:
            content_lines += [f"**Owner:** {owner_email}"]
        if due_date:
            content_lines += [f"**Due:** {due_date}"]
        if notes:
            content_lines += ["", "## Source notes", notes[:1500]]
        page = await notion_svc.create_page(
            org_id=org_id,
            parent_page_id=notion_parent_page_id,
            title=action_text[:200],
            content="\n".join(content_lines),
        )
        return {"task_id": page.get("page_id"), "url": page.get("url")}
    if target == "asana":
        return await asana_svc.create_task(
            org_id=org_id,
            user_id=user_id,
            name=action_text[:200],
            notes=notes[:1500] or None,
            assignee_email=owner_email,
            due_date=due_date,
        )
    if target == "linear":
        return await linear_svc.create_issue(
            org_id=org_id,
            user_id=user_id,
            title=action_text[:200],
            description=notes[:1500] or None,
            assignee_email=owner_email,
            due_date=due_date,
        )
    if target == "jira":
        return await jira_svc.create_issue(
            org_id=org_id,
            user_id=user_id,
            title=action_text[:200],
            description=notes[:1500] or None,
            assignee_email=owner_email,
            due_date=due_date,
        )
    raise ValueError(f"unknown_target: {target}")


# ── Reminder cron ───────────────────────────────────────────────────────────


async def check_incomplete_actions() -> dict[str, Any]:
    """Daily-cron entrypoint. Walks tracked items whose due_date has passed
    and re-checks external status. Bumps status + sends one reminder
    notification per newly-overdue item (rate-limited via last_reminded_at)."""
    svc = get_service_client()
    today_iso = datetime.now(UTC).date().isoformat()
    reminder_cutoff = (datetime.now(UTC) - timedelta(days=2)).isoformat()

    def _fetch() -> list[dict[str, Any]]:
        # Items that are tracked (have external_id) + due date past + we
        # haven't reminded in the last 2 days. Cap to 500 to keep one cron
        # tick bounded.
        res = (
            svc.table("action_items")
            .select("*")
            .in_("status", ["pending", "tracked", "overdue"])
            .not_.is_("external_id", None)
            .lt("due_date", today_iso)
            .or_(f"last_reminded_at.is.null,last_reminded_at.lt.{reminder_cutoff}")
            .limit(500)
            .execute()
        )
        return res.data or []

    items = await asyncio.to_thread(_fetch)
    completed = 0
    reminded = 0
    for item in items:
        try:
            completed_now = await _poll_completion(item)
        except Exception as exc:
            log.warning("action.poll.failed id=%s err=%s", item["id"], exc)
            continue

        if completed_now:
            await asyncio.to_thread(
                lambda i=item: svc.table("action_items")
                .update(
                    {"status": "completed", "completed_at": datetime.now(UTC).isoformat()}
                )
                .eq("id", i["id"])
                .execute()
            )
            completed += 1
            continue

        # Still incomplete past due → overdue + remind.
        await asyncio.to_thread(
            lambda i=item: svc.table("action_items")
            .update(
                {
                    "status": "overdue",
                    "last_reminded_at": datetime.now(UTC).isoformat(),
                    "reminder_count": (i.get("reminder_count") or 0) + 1,
                }
            )
            .eq("id", i["id"])
            .execute()
        )
        if item.get("owner_user_id"):
            try:
                await create_notification(
                    org_id=item["org_id"],
                    user_id=item["owner_user_id"],
                    type="action_item_overdue",
                    title="Action item overdue",
                    body=item["action_text"][:200],
                    link_url=item.get("external_url"),
                    dedupe_key=f"action-overdue-{item['id']}",
                )
            except Exception as exc:
                log.warning(
                    "action.notify.failed id=%s err=%s", item["id"], exc
                )
        reminded += 1

    return {"checked": len(items), "completed": completed, "reminded": reminded}


async def _poll_completion(item: dict[str, Any]) -> bool:
    """Returns True if the external task is now completed."""
    provider = item.get("external_provider")
    ext_id = item.get("external_id")
    org_id = item["org_id"]
    user_id = item["created_by"]
    if not (provider and ext_id):
        return False
    if provider == "asana":
        data = await asana_svc.get_task_status(
            org_id=org_id, user_id=user_id, task_gid=ext_id
        )
        return bool(data.get("completed"))
    if provider == "linear":
        data = await linear_svc.get_issue_status(
            org_id=org_id, user_id=user_id, issue_id=ext_id
        )
        return data.get("state_type") == "completed"
    if provider == "jira":
        data = await jira_svc.get_issue_status(
            org_id=org_id, user_id=user_id, issue_id=ext_id
        )
        # Jira's category 'done' is the canonical completion signal across
        # workflow customisations (whether the state is called "Done", "Closed",
        # "Resolved" etc., the category settles to 'done' when truly complete).
        return data.get("state_category") == "done"
    if provider == "notion":
        # Notion doesn't have a native "done" semantic on a page. We don't
        # auto-mark Notion items completed — the user marks them in-app and
        # the row stays in 'tracked' / 'overdue'.
        return False
    return False
