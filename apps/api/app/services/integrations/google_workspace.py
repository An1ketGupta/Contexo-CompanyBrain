"""Google Workspace per-user OAuth (Calendar + Docs + Drive.file + Gmail.send).

A single consent screen requests all four scopes so the Executive Assistant
(#25) and Calendar Intelligence (#51) features don't each need their own
re-consent loop. Persisted to the unified `integrations` table with
provider='google_workspace' and scope_user_id = user's id.

Why a fourth Google integration (we already have drive + gmail per-provider):
    The legacy Drive integration is *org-scoped*; the legacy Gmail integration
    is per-user but only carries gmail.send. Calendar reads MUST be per-user
    (people have private calendars), and Google Docs creation needs to land
    in the user's own Drive. Stacking these on existing tables would force
    schema changes; landing in the unified table keeps tomorrow's scope
    additions to a single ALTER on `scopes`.

Scope set:
    - calendar.readonly          read upcoming meetings
    - documents                  create + edit Google Docs
    - drive.file                 limited Drive access — only files/folders the
                                  user creates through us, or explicitly grants
                                  via the Picker (see google_meet_transcripts.py)
    - gmail.send                 send the briefing email
    - openid + email + profile   resolve the user's email

We deliberately do NOT request drive.readonly (blanket "see and download all
your Google Drive files"). Meet-transcript auto-ingest instead asks the user
to pick their "Meet Recordings" folder via the Google Picker UI, which grants
drive.file access scoped to just that folder — see add_folder() in
google_meet_transcripts.py. Users who never pick a folder simply don't get
Meet-transcript auto-ingest; everything else keeps working.

This module mirrors the shape of services/integrations/gmail.py: raw httpx,
no google-api-python-client, refresh handled inline. The Docs/Calendar API
calls go through the dedicated google_docs.py / google_calendar.py modules
which import `get_user_token()` from here.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import get_settings
from app.database import get_service_client

log = logging.getLogger(__name__)

PROVIDER = "google_workspace"

_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/gmail.send",
    "openid",
    "email",
    "profile",
]


# ── OAuth flow ──────────────────────────────────────────────────────────────


def build_auth_url(*, state: str) -> str:
    settings = get_settings()
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_workspace_oauth_redirect_uri,
        "response_type": "code",
        "scope": " ".join(_SCOPES),
        "access_type": "offline",
        # Force refresh + consent each time. Without `prompt=consent`, Google
        # omits refresh_token on re-installs and we get a row that goes silent
        # after the first access_token expires.
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


async def exchange_code(*, code: str) -> dict[str, Any]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_workspace_oauth_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"google_workspace_token_exchange_failed: {resp.status_code} {resp.text[:200]}"
        )
    return resp.json()


async def _fetch_userinfo(access_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"userinfo failed: {resp.status_code} {resp.text[:200]}")
    return resp.json()


async def store_credentials(
    *,
    org_id: str,
    user_id: str,
    token_payload: dict[str, Any],
) -> dict[str, Any]:
    """Upsert into integrations table. Preserves an existing refresh_token if
    Google didn't issue a new one (which it doesn't on re-consent if
    prompt=consent isn't honored)."""
    expires_in = int(token_payload.get("expires_in") or 0)
    expiry = (
        (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat()
        if expires_in
        else None
    )
    granted = (token_payload.get("scope") or "").split()
    if "https://www.googleapis.com/auth/calendar.readonly" not in granted:
        raise RuntimeError("calendar_scope_not_granted")
    if "https://www.googleapis.com/auth/documents" not in granted:
        raise RuntimeError("docs_scope_not_granted")

    userinfo = await _fetch_userinfo(token_payload["access_token"])
    email_address = userinfo.get("email")
    if not email_address:
        raise RuntimeError("userinfo_missing_email")

    svc = get_service_client()

    base_row = {
        "org_id": org_id,
        "provider": PROVIDER,
        "scope_user_id": user_id,
        "connected_by": user_id,
        "access_token": token_payload["access_token"],
        "refresh_token": token_payload.get("refresh_token") or "",
        "token_expiry": expiry,
        "scopes": granted,
        "metadata": {"email_address": email_address},
    }

    def _upsert() -> dict[str, Any]:
        existing = (
            svc.table("integrations")
            .select("id, refresh_token")
            .eq("org_id", org_id)
            .eq("provider", PROVIDER)
            .eq("scope_user_id", user_id)
            .maybe_single()
            .execute()
        )
        if existing and existing.data:
            if not base_row["refresh_token"]:
                base_row["refresh_token"] = existing.data.get("refresh_token") or ""
            if not base_row["refresh_token"]:
                raise RuntimeError("refresh_token_missing_on_update")
            svc.table("integrations").update(base_row).eq(
                "id", existing.data["id"]
            ).execute()
            return {**base_row, "id": existing.data["id"]}
        if not base_row["refresh_token"]:
            raise RuntimeError("first_install_missing_refresh_token")
        inserted = svc.table("integrations").insert(base_row).execute()
        return inserted.data[0] if inserted.data else base_row

    return await asyncio.to_thread(_upsert)


async def disconnect(*, org_id: str, user_id: str) -> None:
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("integrations")
        .delete()
        .eq("org_id", org_id)
        .eq("provider", PROVIDER)
        .eq("scope_user_id", user_id)
        .execute()
    )


# ── Token retrieval + refresh ───────────────────────────────────────────────


async def get_user_token(*, org_id: str, user_id: str) -> str | None:
    """Returns a fresh access_token, refreshing if necessary. None when the
    user hasn't connected Google Workspace."""
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("integrations")
        .select("id, access_token, refresh_token, token_expiry, scopes, metadata")
        .eq("org_id", org_id)
        .eq("provider", PROVIDER)
        .eq("scope_user_id", user_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        return None
    return await _ensure_fresh(row.data, org_id=org_id, user_id=user_id)


async def get_user_email(*, org_id: str, user_id: str) -> str | None:
    """Returns the Google-side email for this connection. Used as the From:
    address on Gmail send paths so we always send-from the authenticated
    mailbox (not the org's NirnayaIQ login email)."""
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("integrations")
        .select("metadata")
        .eq("org_id", org_id)
        .eq("provider", PROVIDER)
        .eq("scope_user_id", user_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        return None
    return (row.data.get("metadata") or {}).get("email_address")


async def _ensure_fresh(row: dict[str, Any], *, org_id: str, user_id: str) -> str:
    expiry = row.get("token_expiry")
    if expiry:
        try:
            exp = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            if exp - timedelta(seconds=60) > datetime.now(UTC):
                return row["access_token"]
        except ValueError:
            pass

    settings = get_settings()
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "refresh_token": row["refresh_token"],
                "grant_type": "refresh_token",
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"google_workspace_refresh_failed: {resp.status_code} {resp.text[:200]}"
        )
    payload = resp.json()
    new_access = payload["access_token"]
    new_expiry = (
        datetime.now(UTC) + timedelta(seconds=int(payload.get("expires_in") or 3600))
    ).isoformat()

    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("integrations")
        .update({"access_token": new_access, "token_expiry": new_expiry})
        .eq("id", row["id"])
        .execute()
    )
    return new_access
