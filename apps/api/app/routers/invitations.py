"""Team-member invitation flow.

Admin-only management endpoints are mounted under /organizations/invitations
and protected by JWT + role check. The public accept endpoints live under
/auth/invitations and use the service-role client because the caller is a
freshly-signed-up Supabase user who doesn't yet have an org_id in their JWT
(the org binding happens here).

Plan caps include both confirmed users and any unaccepted, non-expired
invites — so an admin can't queue 50 invites against a 10-seat plan and have
them all redeem. The actual cap numbers come from
`app.services.billing.plan_limits.seat_limit(plan)`, which reads
`pricing_tiers`. This file used to ship a hardcoded `SEAT_CAPS` dict that
drifted from the seeded tiers (had 'growth' after the rename to 'team') —
routing reads through plan_limits eliminates that class of bug.
"""
from __future__ import annotations

import asyncio
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.observability import get_logger
from app.services.billing.plan_limits import seat_limit

log = get_logger(__name__)

router = APIRouter(tags=["invitations"])

INVITE_TTL = timedelta(days=15)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ── Models ───────────────────────────────────────────────────────────────────

class InviteCreate(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    role: Literal["admin", "member"] = "member"
    # Day 8: optional onboarding metadata. The OnboardingAgent uses these
    # to personalise the welcome email + Notion plan and to know who to DM
    # on Slack. All optional so existing invite flows keep working.
    role_title: str | None = Field(default=None, max_length=120)
    start_date: str | None = Field(default=None, max_length=10)  # YYYY-MM-DD
    manager_user_id: str | None = Field(default=None, max_length=64)


class InviteAccept(BaseModel):
    # The newly-created Supabase auth user id. The frontend signs up first
    # (creating the auth identity), then calls accept with this id so we can
    # bind the org/role + app_metadata.
    user_id: str = Field(..., min_length=8, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=80)


# Day 8 — combined "accept with credentials" payload. Replaces the
# accept-invite page's brittle "always signUp() then bind" path with one
# that asks the server whether the email belongs to an existing user.
class InviteAcceptCredentials(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=80)
    password: str = Field(..., min_length=8, max_length=200)


# Post-login bind path: an already-authenticated user accepting an invite
# only sends the token + their chosen display name (their identity is in
# the JWT). No password needed — they're already signed in.
class InviteAcceptAuthenticated(BaseModel):
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
        .gt("expires_at", datetime.now(UTC).isoformat())
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

    now_iso = datetime.now(UTC).isoformat()
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
    cap = seat_limit(org["plan"])

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
    expires_at = datetime.now(UTC) + INVITE_TTL

    # Validate manager_user_id (if provided) belongs to the same org.
    manager_user_id: str | None = None
    if body.manager_user_id:
        m = await asyncio.to_thread(
            lambda: svc.table("users")
            .select("id, org_id")
            .eq("id", body.manager_user_id)
            .maybe_single()
            .execute()
        )
        if not m or not m.data or m.data.get("org_id") != org_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Manager isn't a member of this workspace.",
            )
        manager_user_id = body.manager_user_id

    # Lightweight date validation — Postgres will reject malformed values
    # too, but we want a clean 400 not a 500.
    start_date: str | None = None
    if body.start_date:
        try:
            datetime.strptime(body.start_date, "%Y-%m-%d")
            start_date = body.start_date
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Start date must be YYYY-MM-DD.",
            ) from exc

    invite_payload = {
        "org_id": org_id,
        "email": email,
        "role": body.role,
        "token": token_str,
        "invited_by": user_id,
        "expires_at": expires_at.isoformat(),
        "role_title": (body.role_title or "").strip() or None,
        "start_date": start_date,
        "manager_user_id": manager_user_id,
    }

    def _do_insert():
        return svc.table("invitations").insert(invite_payload).execute()

    try:
        result = await asyncio.to_thread(_do_insert)
    except Exception as exc:
        msg = str(exc)
        is_unique_conflict = (
            "idx_invitations_unique_active" in msg
            or "duplicate key" in msg.lower()
        )
        if not is_unique_conflict:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create invite. Try again.",
            ) from exc

        # Unique-index conflict: a row with (org_id, lower(email)) and
        # accepted_at IS NULL already exists. The index predicate can't
        # include expiry (now() is STABLE, not IMMUTABLE), so expired rows
        # still block — and the list endpoint filters them out, leaving the
        # admin with no way to revoke. Auto-clean expired conflicts here so
        # re-inviting a stale email "just works"; preserve the 409 for
        # genuinely pending invites that the admin should revoke explicitly.
        now_iso = datetime.now(UTC).isoformat()
        existing = await asyncio.to_thread(
            lambda: svc.table("invitations")
            .select("id, expires_at")
            .eq("org_id", org_id)
            .eq("email", email)
            .is_("accepted_at", "null")
            .maybe_single()
            .execute()
        )
        existing_row = existing.data if existing else None
        if existing_row and (existing_row.get("expires_at") or "") < now_iso:
            await asyncio.to_thread(
                lambda: svc.table("invitations")
                .delete()
                .eq("id", existing_row["id"])
                .execute()
            )
            try:
                result = await asyncio.to_thread(_do_insert)
            except Exception as retry_exc:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create invite. Try again.",
                ) from retry_exc
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A pending invite already exists for that email.",
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
                "expires_in_days": INVITE_TTL.days,
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
    now_iso = datetime.now(UTC).isoformat()

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
    now_iso = datetime.now(UTC).isoformat()

    invite = await asyncio.to_thread(
        lambda: svc.table("invitations")
        .select(
            "id, email, role, org_id, expires_at, accepted_at, "
            "role_title, start_date, manager_user_id, invited_by"
        )
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
                "role_title": row.get("role_title"),
                "start_date": row.get("start_date"),
                "manager_user_id": row.get("manager_user_id"),
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

    # Day 8: fire the onboarding agent. The agent owns its own welcome
    # email (with personalised plan preview), so we DON'T send the generic
    # 'welcome' email anymore — they'd land at the same time and look
    # duplicative. If onboarding metadata is absent (legacy invites), the
    # agent still runs with sensible defaults.
    try:
        import inngest as _inngest_pkg

        from app.inngest.client import get_inngest_client

        client = get_inngest_client()
        await client.send(
            _inngest_pkg.Event(
                name="org/member-joined",
                data={
                    "org_id": row["org_id"],
                    "user_id": body.user_id,
                    "name": cleaned_name,
                    "email": auth_email,
                    "role": row["role"],
                    "role_title": row.get("role_title"),
                    "start_date": row.get("start_date"),
                    "manager_user_id": row.get("manager_user_id"),
                    "invited_by": row.get("invited_by"),
                },
                id=f"org-member-joined-{body.user_id}",
            )
        )
    except Exception as exc:
        log.warning("onboarding_agent_dispatch_failed", user_id=body.user_id, error=str(exc))

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
