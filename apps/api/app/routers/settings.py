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


class UpdateProfileRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=80)


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


# ── User profile ──────────────────────────────────────────────────────────────

@router.patch("/users/me")
async def update_profile(
    body: UpdateProfileRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, _org_id, token = _require_user(current_user)
    client = get_user_client(token)

    cleaned = " ".join(body.display_name.split()).strip()
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Display name cannot be empty.",
        )

    result = await asyncio.to_thread(
        lambda: client.table("users")
        .update({"display_name": cleaned})
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
