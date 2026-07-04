"""Org-curated personas (Feature 1.11).

Admin-managed list of shareable AI personas that any user in the org can
adopt by setting `users.persona = 'org:<id>'`. Built as a separate router
to keep settings.py focused on user/org profile fields.

RLS does the heavy lifting:
  * SELECT: every org member.
  * INSERT/UPDATE/DELETE: admin only (enforced again here so 403s come back
    with a clear message instead of an opaque RLS denial).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.database import get_user_client
from app.services.llm.client import get_llm_client
from app.services.llm.types import Message

log = logging.getLogger(__name__)

router = APIRouter(prefix="/org-personas", tags=["personas"])


def _require_org(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No organization found.",
        )
    return org_id, user_id, token


async def _require_admin(client, user_id: str) -> None:
    """Match the RLS predicate so we can fail with a friendly 403."""
    res = await asyncio.to_thread(
        lambda: client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    role = (getattr(res, "data", None) or {}).get("role") if res else None
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only org admins can manage shared personas.",
        )


class PersonaCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=240)
    instructions: str = Field(..., min_length=10, max_length=2000)


class PersonaEnhanceRequest(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    draft: str = Field(..., min_length=3, max_length=4000)


class PersonaEnhanceResponse(BaseModel):
    description: str
    instructions: str


class PersonaUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=240)
    instructions: str | None = Field(default=None, min_length=10, max_length=2000)
    is_archived: bool | None = None


@router.get("")
async def list_personas(
    include_archived: bool = False,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, list[dict[str, Any]]]:
    """List personas visible in the user's org. Archived ones come back only
    when the caller is admin AND explicitly opts in."""
    _, user_id, token = _require_org(current_user)
    client = get_user_client(token)

    query = client.table("org_personas").select(
        "id, name, description, instructions, is_archived, created_at, updated_at"
    )
    if not include_archived:
        query = query.eq("is_archived", False)
    query = query.order("name")

    res = await asyncio.to_thread(lambda: query.execute())
    rows = list(getattr(res, "data", None) or [])
    return {"personas": rows}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_persona(
    body: PersonaCreate,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    client = get_user_client(token)
    await _require_admin(client, user_id)

    payload = {
        "org_id": org_id,
        "created_by": user_id,
        "name": body.name.strip(),
        "description": (body.description or "").strip() or None,
        "instructions": body.instructions.strip(),
    }
    try:
        res = await asyncio.to_thread(
            lambda: client.table("org_personas").insert(payload).execute()
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "duplicate" in msg or "unique" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A persona with that name already exists.",
            ) from exc
        log.warning("org_persona_insert_failed", extra={"err": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not save persona.",
        ) from exc
    return {"persona": (res.data or [None])[0]}


@router.post("/enhance")
async def enhance_persona(
    body: PersonaEnhanceRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Feature 1.11 follow-up — turn a rough one-liner into a structured
    persona overlay (bias / tone / format / guardrail) before it's saved.

    Admin-gated for the same reason create/update are: this is a write-path
    preview step, not a general chat call, so we don't want it exercised by
    every org member.
    """
    _, user_id, token = _require_org(current_user)
    client = get_user_client(token)
    await _require_admin(client, user_id)

    enhanced = await _enhance_persona_with_llm(name=body.name, draft=body.draft)
    return {"persona": enhanced.model_dump()}


def _parse_persona_json(text: str) -> dict[str, str]:
    """Extract the JSON object from the LLM's response — mirrors the fence /
    brace stripping used for sequence drafting so a stray ```json fence or
    surrounding prose doesn't break the parse."""
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
        raise RuntimeError(f"persona_llm_invalid_json: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("persona_llm_invalid_shape: expected a JSON object")
    return data


async def _enhance_persona_with_llm(
    *, name: str | None, draft: str
) -> PersonaEnhanceResponse:
    label = (name or "").strip() or "this persona"
    system = (
        "You turn a rough one-line idea for a company AI persona into a precise "
        "system-prompt overlay. The overlay is inserted verbatim into the AI's "
        "system prompt as `USER ROLE: <name>. <your instructions>`, so write "
        "instructions that stand alone without that prefix."
    )
    user_prompt = (
        f"Persona name: {label}\n"
        f"Rough draft from the admin: {draft.strip()}\n\n"
        "Expand this into:\n"
        "1. A one-line description (<=240 chars) summarizing who this persona is for.\n"
        "2. Instructions (<=2000 chars, 3-5 sentences) that state: what to bias "
        "retrieval toward, the answer format, the tone, and one guardrail "
        "(something this persona should hand off rather than answer definitively).\n\n"
        "Return STRICT JSON in this shape:\n"
        '{"description": "...", "instructions": "..."}\n'
        "Do not invent company-specific facts, document names, or tools you "
        "weren't told about — keep it generic to the role described."
    )

    llm_client = get_llm_client()
    response = await llm_client.complete(
        messages=[Message(role="user", content=user_prompt)],
        system_extra=system,
        temperature=0.4,
        replace_system_prompt=True,
    )
    text = (response.text or "").strip()
    if not text:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI didn't return a persona draft. Try again.",
        )

    try:
        data = _parse_persona_json(text)
    except RuntimeError as exc:
        log.warning("persona_enhance_parse_failed", extra={"err": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Couldn't parse the AI's persona draft. Try again.",
        ) from exc

    description = str(data.get("description") or "").strip()[:240]
    instructions = str(data.get("instructions") or "").strip()[:2000]
    if not instructions or len(instructions) < 10:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI's persona draft was too short. Try rephrasing your input.",
        )
    return PersonaEnhanceResponse(description=description, instructions=instructions)


@router.patch("/{persona_id}")
async def update_persona(
    persona_id: str,
    body: PersonaUpdate,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    client = get_user_client(token)
    await _require_admin(client, user_id)

    update: dict[str, Any] = {}
    if body.name is not None:
        update["name"] = body.name.strip()
    if body.description is not None:
        update["description"] = body.description.strip() or None
    if body.instructions is not None:
        update["instructions"] = body.instructions.strip()
    if body.is_archived is not None:
        update["is_archived"] = body.is_archived

    if not update:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes supplied.",
        )

    res = await asyncio.to_thread(
        lambda: client.table("org_personas")
        .update(update)
        .eq("id", persona_id)
        .eq("org_id", org_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Persona not found."
        )
    return {"persona": res.data[0]}


@router.delete("/{persona_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_persona(
    persona_id: str,
    current_user: dict = Depends(verify_jwt),
) -> None:
    org_id, user_id, token = _require_org(current_user)
    client = get_user_client(token)
    await _require_admin(client, user_id)

    # Hard delete is fine — RLS owns the boundary. Users who had this
    # persona selected fall back to NULL via _resolve_persona_overlay.
    await asyncio.to_thread(
        lambda: client.table("org_personas")
        .delete()
        .eq("id", persona_id)
        .eq("org_id", org_id)
        .execute()
    )
    return None
