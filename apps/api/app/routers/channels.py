"""Channels router (Production Roadmap 2.7).

Endpoints:
    POST   /channels                       — create a channel
    GET    /channels                       — list my channels
    GET    /channels/{id}                  — channel + roster
    POST   /channels/{id}/invite           — owner adds participants
    DELETE /channels/{id}/participants/{user_id}  — leave / remove
    POST   /channels/{id}/read             — mark caller's last_read_at

The chat router gates POST /chat by `channels.is_member()` when the target
conversation is a channel, so we don't duplicate that here.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.database import get_service_client
from app.errors import NoOrganization
from app.services import channels as channels_svc

router = APIRouter(prefix="/channels", tags=["channels"])


def _require_org(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return org_id, user_id, token


# ── Schemas ────────────────────────────────────────────────────────────────


class CreateChannelBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    topic: str | None = Field(default=None, max_length=1000)
    visibility: str = Field(default="private", pattern="^(private|org)$")
    invitee_user_ids: list[str] = Field(default_factory=list, max_length=50)


class InviteBody(BaseModel):
    user_ids: list[str] = Field(..., min_length=1, max_length=50)
    role: str = Field(default="editor", pattern="^(owner|editor|viewer)$")


class RoleUpdateBody(BaseModel):
    role: str = Field(..., pattern="^(owner|editor|viewer)$")


# ── Endpoints ──────────────────────────────────────────────────────────────


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_channel(
    body: CreateChannelBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, _ = _require_org(current_user)

    # Resolve and validate invitees: all must be org members.
    if body.invitee_user_ids:
        svc = get_service_client()

        def _validate() -> list[str]:
            res = (
                svc.table("users")
                .select("id")
                .eq("org_id", org_id)
                .in_("id", body.invitee_user_ids)
                .execute()
            )
            return [r["id"] for r in (res.data or [])]

        valid = set(await asyncio.to_thread(_validate))
        invalid = [u for u in body.invitee_user_ids if u not in valid]
        if invalid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Users not in your workspace: {invalid[:3]}",
            )

    try:
        return await channels_svc.create_channel(
            org_id=org_id,
            creator_user_id=user_id,
            title=body.title,
            topic=body.topic,
            visibility=body.visibility,  # type: ignore[arg-type]
            invitee_user_ids=body.invitee_user_ids,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.get("")
async def list_channels(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    rows = await channels_svc.list_my_channels(
        token=token, org_id=org_id, user_id=user_id
    )
    return {"channels": rows}


@router.get("/{channel_id}")
async def get_channel(
    channel_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    _, user_id, _ = _require_org(current_user)
    try:
        return await channels_svc.get_channel_with_roster(
            conversation_id=channel_id, requesting_user_id=user_id
        )
    except PermissionError as exc:
        msg = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if msg == "channel_not_found"
            else status.HTTP_403_FORBIDDEN
        )
        raise HTTPException(status_code=code, detail=msg) from exc


@router.post("/{channel_id}/invite")
async def invite(
    channel_id: str,
    body: InviteBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, _ = _require_org(current_user)

    # Validate all invitees belong to the same org (RLS is membership-based,
    # not org-based, on the writes table, so this is the boundary check).
    svc = get_service_client()

    def _validate() -> list[str]:
        res = (
            svc.table("users")
            .select("id")
            .eq("org_id", org_id)
            .in_("id", body.user_ids)
            .execute()
        )
        return [r["id"] for r in (res.data or [])]

    valid = set(await asyncio.to_thread(_validate))
    invalid = [u for u in body.user_ids if u not in valid]
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Users not in your workspace: {invalid[:3]}",
        )

    try:
        added = await channels_svc.invite_participants(
            conversation_id=channel_id,
            org_id=org_id,
            inviter_user_id=user_id,
            user_ids=body.user_ids,
            role=body.role,  # type: ignore[arg-type]
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    return {"added": added, "count": len(added)}


@router.patch("/{channel_id}/participants/{user_id}")
async def update_role(
    channel_id: str,
    user_id: str,
    body: RoleUpdateBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    _, caller_user_id, _ = _require_org(current_user)
    try:
        updated = await channels_svc.update_participant_role(
            conversation_id=channel_id,
            actor_user_id=caller_user_id,
            target_user_id=user_id,
            role=body.role,  # type: ignore[arg-type]
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except PermissionError as exc:
        code = (
            status.HTTP_409_CONFLICT
            if str(exc) == "last_owner_cannot_demote"
            else status.HTTP_403_FORBIDDEN
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return updated


@router.delete("/{channel_id}/participants/{user_id}")
async def remove(
    channel_id: str,
    user_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    _, caller_user_id, _ = _require_org(current_user)
    try:
        await channels_svc.remove_participant(
            conversation_id=channel_id,
            remover_user_id=caller_user_id,
            target_user_id=user_id,
        )
    except PermissionError as exc:
        code = (
            status.HTTP_409_CONFLICT
            if str(exc) == "last_owner_cannot_leave"
            else status.HTTP_403_FORBIDDEN
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return {"removed": True}


@router.post("/{channel_id}/read")
async def mark_read(
    channel_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    _, user_id, _ = _require_org(current_user)
    if not await channels_svc.is_member(
        conversation_id=channel_id, user_id=user_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="not_a_member"
        )
    await channels_svc.mark_read(
        conversation_id=channel_id, user_id=user_id
    )
    return {"ok": True}
