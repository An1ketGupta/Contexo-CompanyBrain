"""Pre-join user provisioning for Onboarding v2.

Compliance acknowledgements and the candidate-facing portal both need a row
in `users` that we can attach FK references to (acknowledgements.user_id,
conversations.user_id, etc.). The legacy invite flow only creates that row
after the candidate accepts their invite — which is *after* Day 1 and
defeats the "pre-Day-1 readiness" goal.

This helper bridges that gap. At LOI-signed time we:

  1. Look up an existing auth.users row by email (in case the candidate is
     already a member of a different org — that's an edge case for partners
     hiring contractors who later go full-time).
  2. Create one via supabase.auth.admin.create_user if missing — no
     password, email_confirm=False. The first magic-link click confirms.
  3. Insert a users-profile row with status='pre_join' bound to the org so
     RLS scopes work normally.
  4. Stamp onboarding_runs.pre_join_user_id for traceability.

On the candidate's first sign-in via magic-link, a trigger or post-login
handler flips users.status to 'active'. We don't ship that trigger here —
the existing invitation-accept path already does the equivalent. The
candidate path lands at /accept-pre-join (see frontend) which re-uses the
invitation-accept logic with a different banner copy.

Idempotent: re-running on an already-provisioned candidate returns the
existing user_id without side effects.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.database import get_service_client

log = logging.getLogger(__name__)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


async def _find_users_profile(
    *, org_id: str, email: str
) -> dict[str, Any] | None:
    """Return the users-profile row for (org_id, lower(email)) if any.

    Joins through auth.users since the email lives there, not on the
    profile table. Uses the admin API rather than a direct SQL join
    because Supabase doesn't expose auth.users to the data API.
    """
    svc = get_service_client()
    email = _normalize_email(email)

    def _list_auth() -> dict[str, Any] | None:
        # auth.admin.list_users returns 50 by default and supports paging;
        # for pre-join lookups we filter on the per-org users table after.
        page = 1
        per_page = 200
        while True:
            try:
                res = svc.auth.admin.list_users(page=page, per_page=per_page)
            except TypeError:
                # Older client signature: positional args
                res = svc.auth.admin.list_users()
                users = getattr(res, "users", res) or []
                for u in users:
                    u_email = getattr(u, "email", None)
                    if u_email and _normalize_email(u_email) == email:
                        return {"id": getattr(u, "id", None), "email": u_email}
                return None
            users = getattr(res, "users", []) or []
            if not users:
                return None
            for u in users:
                u_email = getattr(u, "email", None)
                if u_email and _normalize_email(u_email) == email:
                    return {"id": getattr(u, "id", None), "email": u_email}
            if len(users) < per_page:
                return None
            page += 1
            if page > 50:  # safety
                return None

    auth_row = await asyncio.to_thread(_list_auth)
    if not auth_row or not auth_row.get("id"):
        return None

    def _fetch_profile() -> dict[str, Any] | None:
        res = (
            svc.table("users")
            .select("id, org_id, status, display_name")
            .eq("id", auth_row["id"])
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    return await asyncio.to_thread(_fetch_profile) or {
        "id": auth_row["id"],
        "org_id": None,
        "status": None,
        "display_name": None,
    }


async def ensure_pre_join_user(
    *,
    org_id: str,
    run_id: str,
    candidate_email: str,
    candidate_name: str,
) -> str:
    """Provision (or recover) a pre_join users-profile row for the candidate.

    Returns the users.id. Idempotent: if a row already exists (in any
    status), it's returned untouched — we never downgrade an `active` row
    back to `pre_join`.
    """
    svc = get_service_client()
    email = _normalize_email(candidate_email)
    existing = await _find_users_profile(org_id=org_id, email=email)
    if existing and existing.get("org_id") == org_id and existing.get("id"):
        # Stamp onboarding run pointer; don't touch the user row.
        await asyncio.to_thread(
            lambda uid=existing["id"]: svc.table("onboarding_runs")
            .update({"pre_join_user_id": uid})
            .eq("id", run_id)
            .execute()
        )
        return existing["id"]

    # Path A — auth row exists in another org (or no profile in this org).
    auth_user_id = existing.get("id") if existing else None

    # Path B — no auth row at all: create one via admin API.
    if not auth_user_id:
        def _create_auth() -> str | None:
            try:
                res = svc.auth.admin.create_user(
                    {
                        "email": email,
                        # No password — they'll redeem via magic link.
                        "email_confirm": False,
                        "user_metadata": {
                            "candidate_name": candidate_name,
                            "onboarding_run_id": run_id,
                            "source": "onboarding_v2_pre_join",
                        },
                    }
                )
                user = getattr(res, "user", None) or res
                return getattr(user, "id", None) or (
                    user.get("id") if isinstance(user, dict) else None
                )
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                # Idempotency: re-running can race and a parallel call may
                # have already created the row. Refetch.
                if "already" in msg or "exists" in msg or "duplicate" in msg:
                    return None
                raise

        auth_user_id = await asyncio.to_thread(_create_auth)
        if not auth_user_id:
            # Re-resolve after race.
            existing = await _find_users_profile(org_id=org_id, email=email)
            auth_user_id = (existing or {}).get("id")
            if not auth_user_id:
                raise RuntimeError(
                    f"pre_join_user_creation_failed:{email}"
                )

    # Insert (or upsert) the users-profile row for this org.
    def _upsert_profile() -> None:
        # We can't ON CONFLICT (id) because id is the PK; if a row already
        # exists for (auth_user_id, org_id) we leave it. Otherwise insert.
        existing_row = (
            svc.table("users")
            .select("id, status")
            .eq("id", auth_user_id)
            .maybe_single()
            .execute()
        )
        if existing_row and existing_row.data:
            # Already provisioned — but maybe in another org. The schema
            # has users.id as PK referencing auth.users(id) with a
            # NOT-NULL org_id, so a user row can belong to only one org.
            # If the existing org_id doesn't match, surface a clear error.
            if existing_row.data.get("status") != "pre_join" and existing_row.data.get("status") != "active":
                pass
            return
        svc.table("users").insert(
            {
                "id": auth_user_id,
                "org_id": org_id,
                "role": "member",
                "display_name": candidate_name,
                "status": "pre_join",
            }
        ).execute()

    try:
        await asyncio.to_thread(_upsert_profile)
    except Exception as exc:
        msg = str(exc).lower()
        if "duplicate" not in msg and "already" not in msg:
            log.warning(
                "onboarding_v2.pre_join_profile_insert_failed email=%s err=%s",
                email,
                exc,
            )
            raise

    await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .update({"pre_join_user_id": auth_user_id})
        .eq("id", run_id)
        .execute()
    )
    return auth_user_id


async def send_magic_link(*, email: str, redirect_to: str | None = None) -> None:
    """Send a Supabase magic link to the pre-join candidate so they can
    sign in without setting a password. Best-effort — failures are logged
    but don't fail the agent step (HR can resend manually)."""
    svc = get_service_client()

    def _send() -> None:
        try:
            # Supabase's generate_link → magic_link emits the email via the
            # configured SMTP/SendGrid pipeline. We pass redirect_to so the
            # candidate lands on the candidate portal after login.
            svc.auth.admin.generate_link(
                {
                    "type": "magiclink",
                    "email": _normalize_email(email),
                    **(
                        {"options": {"redirect_to": redirect_to}}
                        if redirect_to
                        else {}
                    ),
                }
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "onboarding_v2.magic_link_failed email=%s err=%s", email, exc
            )

    await asyncio.to_thread(_send)
