"""Sales follow-up sequence endpoints (Agent2 Day 4 #8).

CRUD over `sequences` + `sequence_steps` for the creator, plus admin
visibility into org-wide sequence health. Send-time scheduling rides on
Inngest's step.sleep_until pattern (see app/inngest/sequence_functions.py).

Auth model:
    * Creators see and modify their own sequences. Admins see all and may
      cancel any. The DB RLS policy mirrors this so a forgotten in-router
      check still fails closed.
    * Schedule + send happen from the creator's Gmail mailbox. Admins
      cannot schedule on someone else's behalf — the from-address
      ambiguity isn't worth solving in v1.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.services import sequences as sequence_service

log = logging.getLogger(__name__)

router = APIRouter(tags=["sequences"])


def _require_org(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return org_id, user_id, token


async def _user_role(token: str, user_id: str) -> str | None:
    user_client = get_user_client(token)
    me = await asyncio.to_thread(
        lambda: user_client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    return (me.data if me else {}).get("role") if me else None


# ── Models ───────────────────────────────────────────────────────────────


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SequenceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    prospect_email: str = Field(min_length=3, max_length=320)
    prospect_name: str | None = Field(default=None, max_length=200)
    prospect_context: str = Field(min_length=1, max_length=4000)
    step_offsets_days: list[int] = Field(default=[0, 3, 7])

    @field_validator("prospect_email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        v = (v or "").strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("prospect_email is not a valid email address")
        return v

    def offsets(self) -> tuple[int, ...]:
        return tuple(int(d) for d in self.step_offsets_days)


class StepUpdate(BaseModel):
    subject: str | None = Field(default=None, max_length=998)
    body: str | None = Field(default=None, max_length=50000)
    send_offset_days: int | None = Field(default=None, ge=0, le=365)


class SequenceStep(BaseModel):
    id: str
    step_order: int
    send_offset_days: int
    subject: str
    body: str
    status: str
    scheduled_for: str | None = None
    sent_at: str | None = None
    gmail_message_id: str | None = None
    error_message: str | None = None


class Sequence(BaseModel):
    id: str
    org_id: str
    created_by: str
    name: str
    prospect_email: str
    prospect_name: str | None
    prospect_context: str
    status: str
    sender_email: str | None = None
    created_at: str
    updated_at: str


class SequenceWithSteps(BaseModel):
    sequence: Sequence
    steps: list[SequenceStep]


# ── Endpoints ────────────────────────────────────────────────────────────


@router.post(
    "/sequences/generate",
    response_model=SequenceWithSteps,
    status_code=status.HTTP_201_CREATED,
)
async def generate_sequence_endpoint(
    body: SequenceCreate,
    current_user: dict = Depends(verify_jwt),
) -> SequenceWithSteps:
    org_id, user_id, _token = _require_org(current_user)

    try:
        result = await sequence_service.generate_sequence(
            org_id=org_id,
            user_id=user_id,
            name=body.name,
            prospect_email=str(body.prospect_email),
            prospect_context=body.prospect_context,
            prospect_name=body.prospect_name,
            step_offsets_days=body.offsets(),
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        log.exception("sequences.generate_failed user=%s err=%s", user_id, exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail="Couldn't draft the sequence. Try again in a moment.",
        )

    return SequenceWithSteps(
        sequence=Sequence(**result["sequence"]),
        steps=[SequenceStep(**s) for s in result["steps"]],
    )


@router.get("/sequences")
async def list_sequences(
    current_user: dict = Depends(verify_jwt),
    limit: int = 50,
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    role = await _user_role(token, user_id)
    svc = get_service_client()

    def _fetch() -> list[dict[str, Any]]:
        q = (
            svc.table("sequences")
            .select("*")
            .eq("org_id", org_id)
            .order("created_at", desc=True)
            .limit(min(max(limit, 1), 200))
        )
        if role != "admin":
            q = q.eq("created_by", user_id)
        res = q.execute()
        return res.data or []

    rows = await asyncio.to_thread(_fetch)
    return {"sequences": rows}


@router.get("/sequences/{sequence_id}", response_model=SequenceWithSteps)
async def get_sequence_endpoint(
    sequence_id: str,
    current_user: dict = Depends(verify_jwt),
) -> SequenceWithSteps:
    org_id, user_id, token = _require_org(current_user)
    role = await _user_role(token, user_id)
    svc = get_service_client()

    def _fetch() -> dict[str, Any] | None:
        res = (
            svc.table("sequences")
            .select("*")
            .eq("id", sequence_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return (res.data if res else None) or None

    seq = await asyncio.to_thread(_fetch)
    if not seq:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Sequence not found.")
    if role != "admin" and seq["created_by"] != user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not your sequence.")

    def _steps() -> list[dict[str, Any]]:
        res = (
            svc.table("sequence_steps")
            .select("*")
            .eq("sequence_id", sequence_id)
            .order("step_order", desc=False)
            .execute()
        )
        return res.data or []

    steps = await asyncio.to_thread(_steps)
    return SequenceWithSteps(
        sequence=Sequence(**seq),
        steps=[SequenceStep(**s) for s in steps],
    )


@router.patch("/sequences/{sequence_id}/steps/{step_id}", response_model=SequenceStep)
async def update_step_endpoint(
    sequence_id: str,
    step_id: str,
    body: StepUpdate,
    current_user: dict = Depends(verify_jwt),
) -> SequenceStep:
    org_id, user_id, _token = _require_org(current_user)

    # Verify the caller owns the sequence (router-side, on top of RLS).
    svc = get_service_client()

    def _owner() -> str | None:
        res = (
            svc.table("sequences")
            .select("created_by")
            .eq("id", sequence_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return (res.data or {}).get("created_by") if res and res.data else None

    owner = await asyncio.to_thread(_owner)
    if owner != user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not your sequence.")

    try:
        row = await sequence_service.update_step(
            org_id=org_id,
            sequence_id=sequence_id,
            step_id=step_id,
            subject=body.subject,
            body=body.body,
            send_offset_days=body.send_offset_days,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
    return SequenceStep(**row)


@router.post("/sequences/{sequence_id}/schedule")
async def schedule_sequence_endpoint(
    sequence_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, _token = _require_org(current_user)
    try:
        return await sequence_service.schedule_sequence(
            org_id=org_id, user_id=user_id, sequence_id=sequence_id
        )
    except PermissionError as exc:
        msg = str(exc)
        if msg == "gmail_not_connected":
            raise HTTPException(
                status.HTTP_412_PRECONDITION_FAILED,
                detail="Connect Gmail before scheduling a sequence.",
            )
        if msg == "gmail_send_scope_missing":
            raise HTTPException(
                status.HTTP_412_PRECONDITION_FAILED,
                detail="Your Gmail connection is missing the send scope. Reconnect in Settings → Integrations.",
            )
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=msg)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))


@router.post("/sequences/{sequence_id}/cancel")
async def cancel_sequence_endpoint(
    sequence_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    role = await _user_role(token, user_id)
    svc = get_service_client()

    def _owner() -> str | None:
        res = (
            svc.table("sequences")
            .select("created_by")
            .eq("id", sequence_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return (res.data or {}).get("created_by") if res and res.data else None

    owner = await asyncio.to_thread(_owner)
    if owner is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Sequence not found.")
    if owner != user_id and role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not your sequence.")
    return await sequence_service.cancel_sequence(
        org_id=org_id, user_id=user_id, sequence_id=sequence_id
    )
