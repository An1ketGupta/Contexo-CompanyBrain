"""Support mailbox integration (org-level OAuth).

The org's real support address — support@theircompany.com — connected once by
an admin. Customers write to it directly, a cron polls it for new mail, and
replies go back out from that same address so the thread holds together in the
customer's client.

Deliberately separate from `gmail.py`'s per-user flow:

  * **Org-level, not per-user.** One inbox per org, shared by the team. The
    per-user Gmail connection exists so each teammate can send AI-drafted mail
    as *themselves* from chat — a different job with a different identity.
  * **Needs read scope.** Polling an inbox requires `gmail.readonly`, which is
    reasonable to ask for a dedicated support account and unreasonable to ask
    every teammate for on their personal mail.
  * **Its own consent screen.** A distinct redirect URI, because the callback
    mints a `support_mailbox` state that `gmail_callback` would reject as a
    provider mismatch (same reason google_workspace has its own).

The Gmail REST plumbing itself is not duplicated — `gmail.py` owns every call
to Google's API and this module supplies it a token.
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
from app.services.integrations import gmail
from app.services.integrations.email_forward import fire_classify_event

log = logging.getLogger(__name__)

# Read to poll the inbox, send so replies come from the address the customer
# wrote to. openid/email/profile resolve which mailbox was authorized.
_SCOPES = [
    gmail.GMAIL_READONLY_SCOPE,
    gmail.GMAIL_SEND_SCOPE,
    "openid",
    "email",
    "profile",
]


# ── OAuth flow ──────────────────────────────────────────────────────────────

def build_auth_url(*, state: str) -> str:
    settings = get_settings()
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.support_mailbox_oauth_redirect_uri,
        "response_type": "code",
        "scope": " ".join(_SCOPES),
        "access_type": "offline",
        # Force consent so we always capture a refresh token, even when the
        # admin has authorized this Google account for something else before.
        "prompt": "consent",
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
                "redirect_uri": settings.support_mailbox_oauth_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Support mailbox token exchange failed: {resp.status_code} {resp.text[:200]}"
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
    *, org_id: str, connected_by: str, token_payload: dict[str, Any],
) -> dict[str, Any]:
    """Persist the org's single support_mailboxes row. Refuses a grant that
    can't do the job so the admin gets a clear error instead of a mailbox that
    silently never polls."""
    granted = token_payload.get("scope") or ""
    scopes = [s for s in granted.split() if s]
    if not gmail.has_read_scope(scopes):
        raise RuntimeError("support_mailbox_read_scope_not_granted")
    if not gmail.has_send_scope(scopes):
        raise RuntimeError("support_mailbox_send_scope_not_granted")

    userinfo = await _fetch_userinfo(token_payload["access_token"])
    email_address = userinfo.get("email")
    if not email_address:
        raise RuntimeError("Could not resolve the mailbox address from userinfo.")

    expires_in = int(token_payload.get("expires_in") or 0)
    expiry = (
        datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=expires_in)
        if expires_in
        else None
    )

    row: dict[str, Any] = {
        "org_id": org_id,
        "connected_by": connected_by,
        "email_address": email_address,
        "access_token": token_payload["access_token"],
        "refresh_token": token_payload.get("refresh_token") or "",
        "token_expiry": expiry.isoformat() if expiry else None,
        "scopes": scopes,
    }

    svc = get_service_client()

    def _upsert() -> dict[str, Any]:
        existing = (
            svc.table("support_mailboxes")
            .select("id, refresh_token, email_address, history_id")
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        if existing and existing.data:
            if not row["refresh_token"]:
                row["refresh_token"] = existing.data.get("refresh_token") or ""
            if not row["refresh_token"]:
                raise RuntimeError("Refresh token missing on update; force re-consent.")
            # Reconnecting a *different* address invalidates the old cursor —
            # a historyId is only meaningful within one mailbox. Reset so the
            # new inbox bootstraps instead of resuming from a foreign cursor.
            if (existing.data.get("email_address") or "") != email_address:
                row["history_id"] = None
            svc.table("support_mailboxes").update(row).eq(
                "id", existing.data["id"]
            ).execute()
            return {**row, "id": existing.data["id"]}
        if not row["refresh_token"]:
            raise RuntimeError("First connect must include a refresh_token.")
        inserted = svc.table("support_mailboxes").insert(row).execute()
        return inserted.data[0] if inserted.data else row

    return await asyncio.to_thread(_upsert)


async def disconnect(*, org_id: str) -> None:
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("support_mailboxes").delete().eq("org_id", org_id).execute()
    )


# ── Status + credentials ────────────────────────────────────────────────────

async def get_status(*, org_id: str) -> dict[str, Any]:
    """Shape the settings UI renders. Never returns token material."""
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("support_mailboxes")
        .select("email_address, scopes, history_id, connected_at, last_polled_at")
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    settings = get_settings()
    available = bool(settings.google_client_id and settings.google_client_secret)
    if not row or not row.data:
        return {
            "available": available,
            "connected": False,
            "email_address": None,
            "has_read_scope": False,
            "has_send_scope": False,
        }
    data = row.data
    scopes = data.get("scopes") or []
    return {
        "available": available,
        "connected": True,
        "email_address": data.get("email_address"),
        "has_read_scope": gmail.has_read_scope(scopes),
        "has_send_scope": gmail.has_send_scope(scopes),
        # None until the first poll bootstraps the cursor — the UI uses this to
        # explain that mail sent before connecting won't appear.
        "polling_active": bool(data.get("history_id")),
        "connected_at": data.get("connected_at"),
        "last_polled_at": data.get("last_polled_at"),
    }


async def get_credentials(*, org_id: str) -> dict[str, Any] | None:
    """Return {access_token, email_address, scopes} with a fresh token, or None
    when the org hasn't connected a support mailbox."""
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("support_mailboxes")
        .select("id, access_token, refresh_token, token_expiry, scopes, email_address")
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        return None
    data = row.data
    access_token = await _ensure_fresh_token(data, org_id=org_id)
    return {
        "access_token": access_token,
        "email_address": data["email_address"],
        "scopes": data.get("scopes") or [],
    }


async def _ensure_fresh_token(integ: dict[str, Any], *, org_id: str) -> str:
    expiry = integ.get("token_expiry")
    if expiry:
        try:
            exp = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            # Refresh ~60s early to avoid races on the wire.
            if exp - timedelta(seconds=60) > datetime.now(UTC):
                return integ["access_token"]
        except ValueError:
            pass

    settings = get_settings()
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "refresh_token": integ["refresh_token"],
                "grant_type": "refresh_token",
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"support mailbox refresh failed: {resp.status_code} {resp.text[:200]}"
        )
    payload = resp.json()
    new_access = payload["access_token"]
    new_expiry = (
        datetime.now(UTC) + timedelta(seconds=int(payload.get("expires_in") or 3600))
    ).isoformat()

    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("support_mailboxes")
        .update({"access_token": new_access, "token_expiry": new_expiry})
        .eq("org_id", org_id)
        .execute()
    )
    return new_access


# ── Polling ─────────────────────────────────────────────────────────────────

async def poll_all_support_inboxes() -> dict[str, Any]:
    """Inngest cron entry point: delta-read every connected support inbox and
    queue new customer mail for classification.

    One org's revoked token must not stall the rest, so each mailbox is
    isolated the way `drive.poll_all_integrations` does it.
    """
    svc = get_service_client()
    # Only orgs that have actually switched the agent on — polling an inbox
    # whose tickets nothing would act on is pure API spend.
    enabled = await asyncio.to_thread(
        lambda: svc.table("support_settings")
        .select("org_id")
        .eq("enabled", True)
        .execute()
    )
    enabled_org_ids = [r["org_id"] for r in (enabled.data or [])]
    if not enabled_org_ids:
        return {"polled": 0, "queued": 0, "skipped": 0, "errors": 0}

    rows = await asyncio.to_thread(
        lambda: svc.table("support_mailboxes")
        .select("org_id")
        .in_("org_id", enabled_org_ids)
        .execute()
    )

    polled = queued = skipped = errors = 0
    for row in rows.data or []:
        try:
            result = await poll_one(org_id=row["org_id"])
        except Exception as exc:
            errors += 1
            log.warning(
                "support_mailbox_poll_failed org=%s err=%s", row["org_id"], exc
            )
            continue
        if result.get("status") == "ok":
            polled += 1
            queued += int(result.get("queued") or 0)
        else:
            skipped += 1

    return {"polled": polled, "queued": queued, "skipped": skipped, "errors": errors}


async def poll_one(*, org_id: str) -> dict[str, Any]:
    """Delta-read one org's support inbox and fan each new message out to the
    classifier. Public so an admin can force a poll from the settings UI
    instead of waiting for the next cron tick."""
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("support_mailboxes")
        .select("history_id, scopes")
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        return {"status": "skipped", "reason": "mailbox_not_connected"}
    if not gmail.has_read_scope(row.data.get("scopes")):
        return {"status": "skipped", "reason": "read_scope_missing"}

    creds = await get_credentials(org_id=org_id)
    if not creds:
        return {"status": "skipped", "reason": "mailbox_not_connected"}

    previous_history_id = row.data.get("history_id")
    envelopes, next_history_id = await gmail.list_new_inbox_messages(
        access_token=creds["access_token"],
        mailbox_email=creds["email_address"],
        since_history_id=previous_history_id,
    )

    # Fan out before advancing the cursor: if this raises we re-read the same
    # window next tick, and the classifier's idempotency key absorbs the
    # duplicates. The reverse order would lose mail outright.
    for envelope in envelopes:
        await fire_classify_event(
            org_id=org_id,
            from_email=envelope["from_email"],
            from_raw=envelope["from_raw"],
            subject=envelope["subject"] or "(no subject)",
            body=envelope["body"],
        )

    update: dict[str, Any] = {"last_polled_at": datetime.now(UTC).isoformat()}
    if next_history_id and next_history_id != previous_history_id:
        update["history_id"] = next_history_id
    await asyncio.to_thread(
        lambda: svc.table("support_mailboxes")
        .update(update)
        .eq("org_id", org_id)
        .execute()
    )

    return {"status": "ok", "queued": len(envelopes)}
