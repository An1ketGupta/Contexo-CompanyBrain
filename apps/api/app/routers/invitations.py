"""Team-member invitation flow.

Admin-only management endpoints are mounted under /organizations/invitations
and protected by JWT + role check. The public accept endpoints live under
/auth/invitations and use the service-role client because the caller is a
freshly-signed-up Supabase user who doesn't yet have an org_id in their JWT
(the org binding happens here).

Caps:
    free     →  2 seats
    starter  → 10 seats
    growth   → 30 seats
    business → unlimited

Plan caps include both confirmed users and any unaccepted, non-expired
invites — so an admin can't queue 50 invites against a 10-seat plan and have
them all redeem.
"""
from __future__ import annotations

import asyncio
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.observability import get_logger

log = get_logger(__name__)

router = APIRouter(tags=["invitations"])

# ── Plan seat caps. None = unlimited. ────────────────────────────────────────
SEAT_CAPS: dict[str, int | None] = {
    "free": 2,
    "starter": 10,
    "growth": 30,
    "business": None,
}

INVITE_TTL = timedelta(days=7)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ── Models ───────────────────────────────────────────────────────────────────

class InviteCreate(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    role: Literal["admin", "member"] = "member"


class InviteAccept(BaseModel):
    # The newly-created Supabase auth user id. The frontend signs up first
    # (creating the auth identity), then calls accept with this id so we can
    # bind the org/role + app_metadata.
    user_id: str = Field(..., min_length=8, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=80)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _require_user(current_user: dict) -> tuple[str, str, str]:
    user_id = current_user.get("user_id")
    org_id = current_user.get("org_id")
    token = current_user.get("token")
    if not user_id or not org_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return user_id, org_id, token


async def _is_admin(client, user_id: str) -> bool:
    row = await asyncio.to_thread(
        lambda: client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    return bool(row and row.data and row.data.get("role") == "admin")


async def _seat_usage(svc, org_id: str) -> int:
    """Confirmed users + outstanding (un-accepted, un-expired) invites."""
    users = await asyncio.to_thread(
        lambda: svc.table("users")
        .select("id", count="exact", head=True)
        .eq("org_id", org_id)
        .execute()
    )
    invites = await asyncio.to_thread(
        lambda: svc.table("invitations")
        .select("id", count="exact", head=True)
        .eq("org_id", org_id)
        .is_("accepted_at", "null")
        .gt("expires_at", datetime.now(timezone.utc).isoformat())
        .execute()
    )
    return (users.count or 0) + (invites.count or 0)


async def _get_org(svc, org_id: str) -> dict[str, Any]:
    row = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select("id, name, slug, plan")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found.")
    return row.data


def _normalize_email(email: str) -> str:
    return email.strip().lower()


# ── Admin-protected: list/create/revoke ──────────────────────────────────────

@router.get("/organizations/members")
async def list_members(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    """All confirmed users in the caller's org. Used by Settings → Team."""
    _, org_id, token = _require_user(current_user)
    client = get_user_client(token)

    rows = await asyncio.to_thread(
        lambda: client.table("users")
        .select("id, role, display_name, created_at")
        .eq("org_id", org_id)
        .order("created_at")
        .execute()
    )
    members = rows.data or []

    # Email lives in auth.users, not the profile table. Fold it in via service-role
    # since auth.users isn't RLS-aware in the same way.
    svc = get_service_client()
    enriched: list[dict[str, Any]] = []
    for row in members:
        try:
            au = await asyncio.to_thread(lambda uid=row["id"]: svc.auth.admin.get_user_by_id(uid))
            email = getattr(getattr(au, "user", None), "email", None)
        except Exception:
            email = None
        enriched.append({**row, "email": email})
    return {"members": enriched}


@router.get("/organizations/invitations")
async def list_invitations(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    """Pending invites visible to the whole org. Members can see them too —
    helpful so a teammate doesn't re-invite an address that's already pending."""
    _, _org_id, token = _require_user(current_user)
    client = get_user_client(token)

    now_iso = datetime.now(timezone.utc).isoformat()
    rows = await asyncio.to_thread(
        lambda: client.table("invitations")
        .select("id, email, role, expires_at, created_at, invited_by")
        .is_("accepted_at", "null")
        .gt("expires_at", now_iso)
        .order("created_at", desc=True)
        .execute()
    )
    return {"invitations": rows.data or []}


@router.post("/organizations/invitations", status_code=status.HTTP_201_CREATED)
async def create_invitation(
    body: InviteCreate,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)

    if not await _is_admin(client, user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can invite teammates.",
        )

    email = _normalize_email(body.email)
    if not EMAIL_RE.match(email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Enter a valid email address.",
        )

    svc = get_service_client()
    org = await _get_org(svc, org_id)
    cap = SEAT_CAPS.get(org["plan"], SEAT_CAPS["free"])

    # Block already-a-member checks via auth.users (a confirmed user has a row
    # in both auth.users and our users table; the email is in auth.users).
    try:
        existing_user = await asyncio.to_thread(
            lambda: svc.auth.admin.list_users()
        )
        for u in getattr(existing_user, "users", []) or existing_user or []:
            u_email = getattr(u, "email", None) or (u.get("email") if isinstance(u, dict) else None)
            u_id = getattr(u, "id", None) or (u.get("id") if isinstance(u, dict) else None)
            if not u_email or not u_id:
                continue
            if _normalize_email(u_email) != email:
                continue
            # Is this auth user already a member of THIS org?
            profile = await asyncio.to_thread(
                lambda: svc.table("users")
                .select("id")
                .eq("id", u_id)
                .eq("org_id", org_id)
                .maybe_single()
                .execute()
            )
            if profile and profile.data:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="That email is already a member of this workspace.",
                )
    except HTTPException:
        raise
    except Exception as exc:
        # Don't block on auth-listing failures — they're rare and the unique
        # index below is the source of truth for active-invite dedup.
        log.warning("auth_list_users_failed", error=str(exc))

    if cap is not None and await _seat_usage(svc, org_id) >= cap:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"Your {org['plan']} plan supports up to {cap} seats. "
                "Upgrade or revoke a pending invite to add more."
            ),
        )

    token_str = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + INVITE_TTL

    try:
        result = await asyncio.to_thread(
            lambda: svc.table("invitations")
            .insert(
                {
                    "org_id": org_id,
                    "email": email,
                    "role": body.role,
                    "token": token_str,
                    "invited_by": user_id,
                    "expires_at": expires_at.isoformat(),
                }
            )
            .execute()
        )
    except Exception as exc:
        msg = str(exc)
        if "idx_invitations_unique_active" in msg or "duplicate key" in msg.lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A pending invite already exists for that email.",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create invite. Try again.",
        ) from exc

    invite_row = (result.data or [None])[0]
    if not invite_row:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Invite created but couldn't be read back.",
        )

    # Fire the email event — actual send is handled by the Inngest worker so a
    # Resend outage doesn't 500 the invite request.
    try:
        from app.config import get_settings
        from app.services.email import send_email_event  # local to avoid circular

        settings = get_settings()
        accept_url = f"{settings.app_url.rstrip('/')}/accept-invite?token={token_str}"

        # Resolve inviter display name (best-effort — fall back to None).
        inviter_name: str | None = None
        try:
            me_row = await asyncio.to_thread(
                lambda: svc.table("users")
                .select("display_name")
                .eq("id", user_id)
                .maybe_single()
                .execute()
            )
            inviter_name = (me_row.data or {}).get("display_name") if me_row else None
        except Exception:
            pass

        await send_email_event(
            event_type="invite",
            to=email,
            user_id=None,
            org_id=org_id,
            dedupe_key=invite_row["id"],
            data={
                "org_name": org["name"],
                "inviter_name": inviter_name,
                "role": body.role,
                "accept_url": accept_url,
                "expires_in_days": 7,
            },
        )
    except Exception as exc:
        # Log + continue. The admin can revoke + re-invite if delivery fails.
        log.warning("invite_email_dispatch_failed", invite_id=invite_row["id"], error=str(exc))

    try:
        from app.services.analytics import track_event

        await track_event(
            org_id=org_id,
            user_id=user_id,
            event_type="invite_sent",
            metadata={"role": body.role},
        )
    except Exception:
        pass

    return {
        "invitation": {
            "id": invite_row["id"],
            "email": invite_row["email"],
            "role": invite_row["role"],
            "expires_at": invite_row["expires_at"],
            "created_at": invite_row["created_at"],
        }
    }


@router.delete(
    "/organizations/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_invitation(
    invitation_id: str,
    current_user: dict = Depends(verify_jwt),
) -> None:
    user_id, _org_id, token = _require_user(current_user)
    client = get_user_client(token)

    if not await _is_admin(client, user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can revoke invites.",
        )

    result = await asyncio.to_thread(
        lambda: client.table("invitations").delete().eq("id", invitation_id).execute()
    )
    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invite not found or already used.",
        )


# ── Remove member ────────────────────────────────────────────────────────────

@router.delete(
    "/organizations/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_member(
    member_id: str,
    current_user: dict = Depends(verify_jwt),
) -> None:
    """Detach a member from this workspace.

    Effects:
        - Delete the `users` row (cascades to their conversations & messages).
        - Clear `org_id` from the auth user's app_metadata so any cached JWT
          starts failing the FastAPI org check on next refresh.
        - Their uploaded documents survive (created_by FK is ON DELETE SET NULL).

    Guards:
        - Caller must be admin.
        - Target must be in the same org.
        - Caller can't remove themselves (admins shoot themselves in the foot
          this way; demote a teammate to admin first or delete the workspace).
        - Can't remove the last admin (workspace would be unmanageable).
    """
    caller_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)

    if not await _is_admin(client, caller_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can remove members.",
        )
    if member_id == caller_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You can't remove yourself. Ask another admin or delete the workspace from the danger zone.",
        )

    target = await asyncio.to_thread(
        lambda: client.table("users")
        .select("id, role, org_id")
        .eq("id", member_id)
        .maybe_single()
        .execute()
    )
    if not target or not target.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found.")
    if target.data.get("org_id") != org_id:
        # RLS should already hide them, but be explicit so we don't accidentally
        # delete cross-org if the RLS policy regresses.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found.")

    if target.data.get("role") == "admin":
        svc = get_service_client()
        admin_count = await asyncio.to_thread(
            lambda: svc.table("users")
            .select("id", count="exact", head=True)
            .eq("org_id", org_id)
            .eq("role", "admin")
            .execute()
        )
        if (admin_count.count or 0) <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Can't remove the last admin. Promote another member first.",
            )

    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("users").delete().eq("id", member_id).eq("org_id", org_id).execute()
    )

    # Clear app_metadata.org_id so a cached JWT can't keep accessing the org.
    # If this fails, the users-row delete already revoked access via RLS — log and move on.
    try:
        await asyncio.to_thread(
            lambda: svc.auth.admin.update_user_by_id(
                member_id,
                {"app_metadata": {"org_id": None}},
            )
        )
    except Exception as exc:
        log.warning("remove_member_metadata_clear_failed", member_id=member_id, error=str(exc))


# ── Public: lookup + accept by token ─────────────────────────────────────────

@router.get("/auth/invitations/{token}")
async def lookup_invitation(token: str) -> dict[str, Any]:
    """Pre-fills the signup form. Returns 404 if invalid or expired."""
    if len(token) < 16 or len(token) > 128:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed invite token.",
        )
    svc = get_service_client()
    now_iso = datetime.now(timezone.utc).isoformat()

    invite = await asyncio.to_thread(
        lambda: svc.table("invitations")
        .select("id, email, role, org_id, expires_at, accepted_at")
        .eq("token", token)
        .maybe_single()
        .execute()
    )
    if not invite or not invite.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid invite link.")
    row = invite.data
    if row.get("accepted_at"):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invite has already been accepted.")
    if row["expires_at"] < now_iso:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invite has expired. Ask the admin for a new one.")

    org = await _get_org(svc, row["org_id"])
    return {
        "email": row["email"],
        "role": row["role"],
        "org": {"id": org["id"], "name": org["name"]},
    }


@router.post("/auth/invitations/{token}/accept")
async def accept_invitation(token: str, body: InviteAccept) -> dict[str, Any]:
    """Atomically bind a freshly-signed-up auth user to the invite's org.

    Caller flow:
        1. Frontend calls supabase.auth.signUp() → gets user_id.
        2. Frontend POSTs here with { user_id, display_name }.
        3. We verify the auth user's email matches the invite (defense against
           a token leak where the attacker creates an account with their own
           email but tries to redeem someone else's invite).
        4. We insert the users row + stamp accepted_at + write org_id into
           app_metadata so the next session refresh includes it in the JWT.
    """
    svc = get_service_client()
    now_iso = datetime.now(timezone.utc).isoformat()

    invite = await asyncio.to_thread(
        lambda: svc.table("invitations")
        .select("id, email, role, org_id, expires_at, accepted_at")
        .eq("token", token)
        .maybe_single()
        .execute()
    )
    if not invite or not invite.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid invite link.")
    row = invite.data
    if row.get("accepted_at"):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invite has already been accepted.")
    if row["expires_at"] < now_iso:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invite has expired.")

    # Verify the new auth user's email matches the invite email — otherwise a
    # leaked token could redirect to a different account.
    try:
        au = await asyncio.to_thread(lambda: svc.auth.admin.get_user_by_id(body.user_id))
        auth_email = getattr(getattr(au, "user", None), "email", None)
    except Exception as exc:
        log.warning("invite_accept_auth_lookup_failed", user_id=body.user_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Couldn't verify your account. Try signing in again.",
        ) from exc

    if not auth_email or _normalize_email(auth_email) != _normalize_email(row["email"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This invite is for a different email address.",
        )

    # Refuse if a profile already exists. The signup flow only mints a profile
    # via /api/auth/complete-signup; an existing row means they're already a member
    # somewhere — joining a new org would orphan their old data.
    existing = await asyncio.to_thread(
        lambda: svc.table("users").select("id").eq("id", body.user_id).maybe_single().execute()
    )
    if existing and existing.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already belong to a workspace.",
        )

    cleaned_name = " ".join(body.display_name.split()).strip()
    if not cleaned_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Display name cannot be empty.",
        )

    await asyncio.to_thread(
        lambda: svc.table("users")
        .insert(
            {
                "id": body.user_id,
                "org_id": row["org_id"],
                "role": row["role"],
                "display_name": cleaned_name,
            }
        )
        .execute()
    )

    await asyncio.to_thread(
        lambda: svc.table("invitations")
        .update({"accepted_at": now_iso})
        .eq("id", row["id"])
        .execute()
    )

    # Welcome email for the new joiner. Once-per-lifetime via dedupe_key=None.
    try:
        from app.config import get_settings
        from app.services.email import send_email_event

        settings = get_settings()
        org_meta = await _get_org(svc, row["org_id"])
        await send_email_event(
            event_type="welcome",
            to=auth_email,
            user_id=body.user_id,
            org_id=row["org_id"],
            data={
                "first_name": cleaned_name.split(" ")[0] if cleaned_name else None,
                "org_name": org_meta["name"],
                "app_url": settings.app_url,
            },
        )
    except Exception as exc:
        log.warning("welcome_email_dispatch_failed", user_id=body.user_id, error=str(exc))

    # JWT app_metadata. The next supabase.auth.refreshSession() will pick this
    # up and FastAPI's verify_jwt will read org_id from it.
    try:
        await asyncio.to_thread(
            lambda: svc.auth.admin.update_user_by_id(
                body.user_id,
                {"app_metadata": {"org_id": row["org_id"]}},
            )
        )
    except Exception as exc:
        # The row inserts succeeded; without app_metadata the user can still
        # log in but FastAPI will 403 until refreshSession() — surface it so
        # the frontend can retry or show a friendly error.
        log.warning("invite_accept_metadata_failed", user_id=body.user_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Joined workspace, but session needs a refresh. Sign out and back in.",
        ) from exc

    try:
        from app.services.analytics import track_event

        await track_event(
            org_id=row["org_id"],
            user_id=body.user_id,
            event_type="invite_accepted",
            metadata={"role": row["role"]},
        )
    except Exception:
        pass

    return {"org_id": row["org_id"], "role": row["role"]}
