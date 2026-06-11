"""Settings endpoints — workspace + user profile + account deletion.

Naming convention: `/organizations/me` and `/users/me` route to the caller's
own resources via JWT. We avoid `/organizations/{id}` so a future bug in path
handling can't lead a user to mutate someone else's org.

Account deletion uses the service-role client because `auth.admin.delete_user`
isn't callable with a user JWT. The org cascade comes from FKs (see migration
001 — documents/conversations/messages all hold an `org_id` that goes away when
the org row is deleted).
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client

log = logging.getLogger(__name__)

router = APIRouter(tags=["settings"])

ORG_NAME_PATTERN = re.compile(r"^[\w\s\-&.,'()]+$", re.UNICODE)


def _require_user(current_user: dict) -> tuple[str, str, str]:
    user_id = current_user.get("user_id")
    org_id = current_user.get("org_id")
    token = current_user.get("token")
    if not user_id or not org_id or not token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No organization found. Please sign out and sign back in.",
        )
    return user_id, org_id, token


# ── Request models ────────────────────────────────────────────────────────────

class UpdateOrganizationRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)


class UpdateOrgSettingsRequest(BaseModel):
    # `None` clears any existing value; empty string is treated the same as None.
    # 500-char cap mirrors org_config.INSTRUCTIONS_MAX_CHARS — keep the two in sync
    # if you change it. The text rides inside the LLM system prompt on every turn,
    # so length directly costs us tokens.
    ai_instructions: str | None = Field(default=None, max_length=500)


class UpdateOrgSharingRequest(BaseModel):
    # V3 Day 4 #62. Admin kill-switch for public output links. New shares are
    # gated by this; existing tokens stop resolving while the flag is off.
    allow_output_sharing: bool


class UpdateProfileRequest(BaseModel):
    # Both optional; PATCH allows changing one without echoing the other back.
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    # V4 #57 — hide my activity from the team feed.
    activity_private: bool | None = None


class DeleteAccountRequest(BaseModel):
    # Defense in depth — the UI also requires the user to type the org name.
    confirm_org_name: str


# ── Organization ──────────────────────────────────────────────────────────────

@router.patch("/organizations/me")
async def update_organization(
    body: UpdateOrganizationRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)

    cleaned = " ".join(body.name.split()).strip()
    if not cleaned or not ORG_NAME_PATTERN.match(cleaned):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Organization name contains unsupported characters.",
        )

    # Role check — only admins can rename the org. Members get a 403.
    me = await asyncio.to_thread(
        lambda: client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not me.data or me.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can update workspace settings.",
        )

    result = await asyncio.to_thread(
        lambda: client.table("organizations")
        .update({"name": cleaned})
        .eq("id", org_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found.",
        )
    return {"organization": result.data[0]}


# ── Org-wide AI settings (Day 9 / #67) ────────────────────────────────────────


@router.get("/organizations/me/ai-settings")
async def get_org_ai_settings(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)
    result = await asyncio.to_thread(
        lambda: client.table("organizations")
        .select("ai_instructions")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return {"ai_instructions": result.data.get("ai_instructions") or ""}


@router.patch("/organizations/me/ai-settings")
async def update_org_ai_settings(
    body: UpdateOrgSettingsRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Admin-only. Set or clear the per-org AI instructions prepended to the
    LLM system prompt on every chat turn.

    Invalidates the in-process org_config cache for this process — other
    worker processes pick up the change within the cache TTL (~60s).
    """
    user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)

    me = await asyncio.to_thread(
        lambda: client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not me.data or me.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can change AI settings.",
        )

    cleaned: str | None
    raw = (body.ai_instructions or "").strip()
    cleaned = raw or None  # treat blank as a clear

    result = await asyncio.to_thread(
        lambda: client.table("organizations")
        .update({"ai_instructions": cleaned})
        .eq("id", org_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Workspace not found.")

    # Invalidate the cache so this worker picks the change up on the very
    # next chat call. Other workers age out via TTL.
    from app.services.org_config import invalidate

    invalidate(org_id)

    return {"ai_instructions": cleaned or ""}


# ── Org-wide sharing toggle (Day 4 / #62) ─────────────────────────────────────


@router.get("/organizations/me/sharing")
async def get_org_sharing(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)
    result = await asyncio.to_thread(
        lambda: client.table("organizations")
        .select("allow_output_sharing")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return {
        "allow_output_sharing": bool(result.data.get("allow_output_sharing", True)),
    }


@router.patch("/organizations/me/sharing")
async def update_org_sharing(
    body: UpdateOrgSharingRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Admin-only. Toggle whether team members can mint public share links."""
    user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)

    me = await asyncio.to_thread(
        lambda: client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not me.data or me.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can change sharing settings.",
        )

    result = await asyncio.to_thread(
        lambda: client.table("organizations")
        .update({"allow_output_sharing": body.allow_output_sharing})
        .eq("id", org_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return {"allow_output_sharing": body.allow_output_sharing}


# ── User profile ──────────────────────────────────────────────────────────────

@router.patch("/users/me")
async def update_profile(
    body: UpdateProfileRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, _org_id, token = _require_user(current_user)
    client = get_user_client(token)

    update: dict[str, Any] = {}
    if body.display_name is not None:
        cleaned = " ".join(body.display_name.split()).strip()
        if not cleaned:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Display name cannot be empty.",
            )
        update["display_name"] = cleaned
    if body.activity_private is not None:
        update["activity_private"] = body.activity_private

    if not update:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes supplied.",
        )

    result = await asyncio.to_thread(
        lambda: client.table("users")
        .update(update)
        .eq("id", user_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found.",
        )
    return {"user": result.data[0]}


# ── Account deletion ──────────────────────────────────────────────────────────

@router.delete("/users/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    body: DeleteAccountRequest,
    current_user: dict = Depends(verify_jwt),
) -> None:
    """Delete the caller's auth identity and all org data they own.

    Semantics:
    - If the caller is the sole admin of the org → delete the org row, which
      cascades to documents, chunks, embeddings, conversations, messages.
    - If other admins exist → only delete the caller's user row + auth identity;
      the org keeps running.
    - Storage objects under `orgs/{org_id}/` are removed best-effort.
    """
    user_id, org_id, _token = _require_user(current_user)
    svc = get_service_client()

    # Confirm org-name typed by user (UI requires it; server enforces it).
    org_row = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select("id, name")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    if not org_row.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found.")

    expected = org_row.data["name"].strip().lower()
    provided = body.confirm_org_name.strip().lower()
    if expected != provided:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace name does not match. Type the exact name to confirm.",
        )

    # Count admins to decide single-admin vs. multi-admin path.
    admins = await asyncio.to_thread(
        lambda: svc.table("users")
        .select("id", count="exact")
        .eq("org_id", org_id)
        .eq("role", "admin")
        .execute()
    )
    admin_count = admins.count or 0
    me_row = next((row for row in (admins.data or []) if row["id"] == user_id), None)
    is_sole_admin = admin_count <= 1 and me_row is not None

    if is_sole_admin:
        # Delete storage objects for the whole org, then drop the org row.
        # Cascade removes documents, conversations, messages, embeddings, users.
        await _wipe_org_storage(svc, org_id)
        await asyncio.to_thread(
            lambda: svc.table("organizations").delete().eq("id", org_id).execute()
        )
    else:
        # Just drop this user's profile row; auth.users delete below cascades to
        # owner-of records only where we use ON DELETE SET NULL.
        await asyncio.to_thread(
            lambda: svc.table("users").delete().eq("id", user_id).execute()
        )

    # Delete the Supabase auth identity. Without this, the email/password
    # remains usable to sign back in — which would be confusing UX.
    try:
        await asyncio.to_thread(lambda: svc.auth.admin.delete_user(user_id))
    except Exception as exc:
        # Surface the failure — leaving an orphan auth identity is the worse
        # outcome (re-sign-in would land in a broken state with no profile row).
        log.error("Failed to delete auth user %s: %s", user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete account. Please contact support.",
        ) from exc


# ── Admin: feedback stats ─────────────────────────────────────────────────────

@router.get("/admin/feedback-stats")
async def admin_feedback_stats(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Aggregated thumbs-up/down counts for the caller's workspace.

    Admin-only. Powers the 'Feedback signal' card on Settings → Workspace.
    Uses the user-scoped client so RLS keeps org isolation honest; the
    `count='exact'` flag asks PostgREST for the row count without dragging
    rows back.
    """
    user_id, _org_id, token = _require_user(current_user)
    client = get_user_client(token)

    me = await asyncio.to_thread(
        lambda: client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can view feedback analytics.",
        )

    async def _count(filter_value: str | None) -> int:
        def _run() -> int:
            q = (
                client.table("messages")
                .select("id", count="exact", head=True)
                .eq("role", "assistant")
            )
            q = q.eq("feedback", filter_value) if filter_value else q.is_("feedback", "null")
            res = q.execute()
            return res.count or 0

        return await asyncio.to_thread(_run)

    positive, negative, unrated = await asyncio.gather(
        _count("positive"),
        _count("negative"),
        _count(None),
    )

    return {
        "positive": positive,
        "negative": negative,
        "unrated": unrated,
        "total": positive + negative + unrated,
    }


# ── API keys (Day 15 / #47) ──────────────────────────────────────────────────

class CreateApiKeyBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    scope: str = Field(default="org", pattern="^(org|user)$")


@router.get("/settings/api-keys")
async def list_api_keys(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, org_id, token = _require_user(current_user)
    # Members can see keys but only admins can create/revoke. Visibility is
    # safe because we never expose key_hash or anything reversible.
    from app.services.api_keys import list_keys
    keys = await list_keys(org_id=org_id)
    return {"keys": keys}


@router.post("/settings/api-keys", status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: CreateApiKeyBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)
    me = await asyncio.to_thread(
        lambda: client.table("users").select("role").eq("id", user_id).maybe_single().execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can create API keys.",
        )

    cleaned = " ".join(body.name.split()).strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Name cannot be empty.")

    from app.services.api_keys import create_key
    issued = await create_key(
        org_id=org_id, user_id=user_id, name=cleaned, scope=body.scope
    )
    # The full key leaves the server here and never again.
    return {
        "id": issued.id,
        "name": issued.name,
        "scope": issued.scope,
        "key": issued.full_key,
        "key_prefix": issued.key_prefix,
        "created_at": issued.created_at,
    }


@router.delete("/settings/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: str,
    current_user: dict = Depends(verify_jwt),
) -> None:
    user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)
    me = await asyncio.to_thread(
        lambda: client.table("users").select("role").eq("id", user_id).maybe_single().execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can revoke API keys.",
        )
    from app.services.api_keys import revoke_key
    ok = await revoke_key(org_id=org_id, key_id=key_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Key not found or already revoked.")


async def _wipe_org_storage(svc, org_id: str) -> None:
    """Best-effort removal of org storage tree. Failures don't block deletion."""
    from app.routers.documents import STORAGE_BUCKET

    try:
        # List recursively under the org prefix.
        prefix = f"orgs/{org_id}"
        listing = await asyncio.to_thread(
            lambda: svc.storage.from_(STORAGE_BUCKET).list(prefix)
        )
        # `list` is shallow; we follow up by removing whole doc folders.
        doc_paths: list[str] = []
        for entry in listing or []:
            doc_id = entry.get("name")
            if not doc_id:
                continue
            sub = await asyncio.to_thread(
                lambda: svc.storage.from_(STORAGE_BUCKET).list(f"{prefix}/{doc_id}")
            )
            for f in sub or []:
                fname = f.get("name")
                if fname:
                    doc_paths.append(f"{prefix}/{doc_id}/{fname}")

        if doc_paths:
            await asyncio.to_thread(
                lambda: svc.storage.from_(STORAGE_BUCKET).remove(doc_paths)
            )
    except Exception as exc:
        log.warning("Storage cleanup for org %s failed: %s", org_id, exc)
