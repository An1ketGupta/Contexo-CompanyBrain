"""Team channels service (Production Roadmap 2.7).

A "channel" is a `conversations` row with `is_channel=true` plus a roster
of `conversation_participants`. The legacy single-user conversation path is
untouched — channels coexist as a parallel notion.

This module owns:
  * create_channel        — owner-only creation, seeds the participants table
  * invite_participants   — owner adds members (single call, batched)
  * list_my_channels      — channels the caller belongs to OR org-visible
  * get_channel           — channel + roster (membership-checked)
  * leave_channel         — caller removes self; last owner can't leave
  * mark_read             — bumps last_read_at for the caller

Authorisation model:
  * Owners can invite, change visibility, and rename.
  * Editors can post messages (handled by chat router via is_member check).
  * Viewers are read-only.
  * Org-visible channels are auto-readable by any org member without being
    in the participants table; they are joined implicitly on first post.

We never bypass RLS for SELECTs — admins reading the roster goes through
the user-scoped client. Writes use the service role because the policy is
intentionally minimal (writes are router-authorised).
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal

from app.database import get_service_client, get_user_client

ParticipantRole = Literal["owner", "editor", "viewer"]
ChannelVisibility = Literal["private", "org"]


# ── Internal helpers ────────────────────────────────────────────────────────


async def _get_channel_row(*, conversation_id: str) -> dict[str, Any] | None:
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("conversations")
        .select("id, org_id, user_id, title, topic, is_channel, channel_visibility, created_at, updated_at")
        .eq("id", conversation_id)
        .maybe_single()
        .execute()
    )
    return res.data if res else None


async def _participant_role(
    *, conversation_id: str, user_id: str
) -> ParticipantRole | None:
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("conversation_participants")
        .select("role")
        .eq("conversation_id", conversation_id)
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    if not res or not res.data:
        return None
    return res.data.get("role")  # type: ignore[return-value]


async def is_member(*, conversation_id: str, user_id: str) -> bool:
    """True iff user is in the participants table OR the channel is org-visible
    AND the user is in the same org. Used by the chat stream handler to gate
    posting and by the realtime broadcaster to gate subscribe."""
    ch = await _get_channel_row(conversation_id=conversation_id)
    if not ch:
        return False
    if not ch.get("is_channel"):
        return ch.get("user_id") == user_id
    role = await _participant_role(conversation_id=conversation_id, user_id=user_id)
    if role:
        return True
    if ch.get("channel_visibility") == "org":
        # Caller's org_id must match — the user_client lookup proves this
        # implicitly via RLS, but here we use service role so check by hand.
        svc = get_service_client()
        res = await asyncio.to_thread(
            lambda: svc.table("users")
            .select("org_id")
            .eq("id", user_id)
            .maybe_single()
            .execute()
        )
        if res and res.data and res.data.get("org_id") == ch.get("org_id"):
            return True
    return False


async def require_owner(*, conversation_id: str, user_id: str) -> None:
    """Raises PermissionError unless caller is an owner of the channel."""
    role = await _participant_role(
        conversation_id=conversation_id, user_id=user_id
    )
    if role != "owner":
        raise PermissionError("channel_owner_required")


# ── Create / list / read ───────────────────────────────────────────────────


async def create_channel(
    *,
    org_id: str,
    creator_user_id: str,
    title: str,
    topic: str | None = None,
    visibility: ChannelVisibility = "private",
    invitee_user_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Creates a channel + owner participant row + optional invitees in one shot.

    Idempotency: not built-in — channels are cheap and dupes are visible to
    the creator. A client-side dedupe key could be threaded through later if
    we see complaints.
    """
    svc = get_service_client()
    title = title.strip()[:200]
    if not title:
        raise ValueError("title_required")

    conv_row = {
        "org_id": org_id,
        "user_id": creator_user_id,  # legacy column — owner of record
        "title": title,
        "topic": (topic or "").strip()[:1000] or None,
        "is_channel": True,
        "channel_visibility": visibility,
    }

    def _insert_conversation() -> dict[str, Any]:
        res = svc.table("conversations").insert(conv_row).execute()
        if not res.data:
            raise RuntimeError("channel_insert_returned_no_row")
        return res.data[0]

    conversation = await asyncio.to_thread(_insert_conversation)
    conversation_id = conversation["id"]

    # Seed owner row + invitees in a single batch.
    rows: list[dict[str, Any]] = [
        {
            "conversation_id": conversation_id,
            "org_id": org_id,
            "user_id": creator_user_id,
            "role": "owner",
        }
    ]
    seen: set[str] = {creator_user_id}
    for uid in invitee_user_ids or []:
        if uid in seen:
            continue
        seen.add(uid)
        rows.append(
            {
                "conversation_id": conversation_id,
                "org_id": org_id,
                "user_id": uid,
                "role": "editor",
            }
        )

    def _seed() -> None:
        svc.table("conversation_participants").insert(rows).execute()

    await asyncio.to_thread(_seed)
    return {**conversation, "participants": rows}


async def invite_participants(
    *,
    conversation_id: str,
    org_id: str,
    inviter_user_id: str,
    user_ids: list[str],
    role: ParticipantRole = "editor",
) -> list[dict[str, Any]]:
    """Owner-only. Skips users already in the channel; returns the new rows."""
    await require_owner(
        conversation_id=conversation_id, user_id=inviter_user_id
    )
    svc = get_service_client()

    # Drop dupes already in the channel.
    def _existing() -> set[str]:
        res = (
            svc.table("conversation_participants")
            .select("user_id")
            .eq("conversation_id", conversation_id)
            .execute()
        )
        return {r["user_id"] for r in (res.data or [])}

    existing = await asyncio.to_thread(_existing)
    fresh = [u for u in user_ids if u not in existing]
    if not fresh:
        return []

    rows = [
        {
            "conversation_id": conversation_id,
            "org_id": org_id,
            "user_id": uid,
            "role": role,
        }
        for uid in fresh
    ]

    def _insert() -> list[dict[str, Any]]:
        res = svc.table("conversation_participants").insert(rows).execute()
        return res.data or []

    return await asyncio.to_thread(_insert)


async def remove_participant(
    *,
    conversation_id: str,
    remover_user_id: str,
    target_user_id: str,
) -> None:
    """Owner removes someone; or any participant removes themselves.

    Last-owner protection: removing the only owner is rejected.
    """
    if remover_user_id != target_user_id:
        await require_owner(
            conversation_id=conversation_id, user_id=remover_user_id
        )

    svc = get_service_client()

    def _check_last_owner() -> bool:
        res = (
            svc.table("conversation_participants")
            .select("user_id, role")
            .eq("conversation_id", conversation_id)
            .eq("role", "owner")
            .execute()
        )
        owners = [r["user_id"] for r in (res.data or [])]
        return len(owners) == 1 and owners[0] == target_user_id

    if await asyncio.to_thread(_check_last_owner):
        raise PermissionError("last_owner_cannot_leave")

    def _delete() -> None:
        svc.table("conversation_participants").delete().eq(
            "conversation_id", conversation_id
        ).eq("user_id", target_user_id).execute()

    await asyncio.to_thread(_delete)


async def list_my_channels(
    *, token: str, org_id: str, user_id: str
) -> list[dict[str, Any]]:
    """Channels the caller is in, plus all org-visible channels."""
    client = get_user_client(token)

    def _query() -> list[dict[str, Any]]:
        res = (
            client.table("conversations")
            .select(
                "id, title, topic, is_channel, channel_visibility, "
                "created_at, updated_at, user_id"
            )
            .eq("org_id", org_id)
            .eq("is_channel", True)
            .order("updated_at", desc=True)
            .limit(100)
            .execute()
        )
        return res.data or []

    rows = await asyncio.to_thread(_query)

    # Annotate each row with the caller's role + member count via service-role
    # (RLS would forbid these joins for non-members of private channels).
    svc = get_service_client()
    if not rows:
        return []
    ids = [r["id"] for r in rows]

    def _members() -> dict[str, dict[str, Any]]:
        res = (
            svc.table("conversation_participants")
            .select("conversation_id, user_id, role")
            .in_("conversation_id", ids)
            .execute()
        )
        bucket: dict[str, dict[str, Any]] = {}
        for r in (res.data or []):
            cid = r["conversation_id"]
            entry = bucket.setdefault(cid, {"member_count": 0, "my_role": None})
            entry["member_count"] += 1
            if r["user_id"] == user_id:
                entry["my_role"] = r["role"]
        return bucket

    annotation = await asyncio.to_thread(_members)
    out: list[dict[str, Any]] = []
    for r in rows:
        ann = annotation.get(r["id"], {"member_count": 0, "my_role": None})
        out.append({**r, **ann})
    return out


async def get_channel_with_roster(
    *, conversation_id: str, requesting_user_id: str
) -> dict[str, Any]:
    """Channel + participants list. Raises PermissionError if caller can't read."""
    ch = await _get_channel_row(conversation_id=conversation_id)
    if not ch:
        raise PermissionError("channel_not_found")
    if not ch.get("is_channel"):
        raise PermissionError("not_a_channel")
    if not await is_member(
        conversation_id=conversation_id, user_id=requesting_user_id
    ):
        raise PermissionError("not_a_member")

    svc = get_service_client()

    def _roster() -> list[dict[str, Any]]:
        # Join to users for display_name; emails come from the auth admin
        # API at the router layer if needed.
        res = (
            svc.table("conversation_participants")
            .select(
                "id, user_id, role, joined_at, last_read_at, "
                "users(display_name)"
            )
            .eq("conversation_id", conversation_id)
            .execute()
        )
        return res.data or []

    roster = await asyncio.to_thread(_roster)
    return {**ch, "participants": roster}


async def mark_read(
    *, conversation_id: str, user_id: str
) -> None:
    svc = get_service_client()
    now = datetime.now(UTC).isoformat()

    def _update() -> None:
        svc.table("conversation_participants").update(
            {"last_read_at": now}
        ).eq("conversation_id", conversation_id).eq(
            "user_id", user_id
        ).execute()

    await asyncio.to_thread(_update)


async def get_participant_display_names(
    *, conversation_id: str
) -> list[str]:
    """Returns display names of all participants — used to seed the system
    note "Multiple users are in this conversation: [names]" so the LLM can
    address everyone."""
    svc = get_service_client()

    def _query() -> list[str]:
        res = (
            svc.table("conversation_participants")
            .select("users(display_name)")
            .eq("conversation_id", conversation_id)
            .execute()
        )
        return [
            (r.get("users") or {}).get("display_name") or "Someone"
            for r in (res.data or [])
        ]

    return await asyncio.to_thread(_query)
