"""Gmail Send integration (per-user OAuth).

This module mirrors the shape of `drive.py` (raw httpx — no
google-api-python-client / google-auth) so the FastAPI image stays light and
the OAuth flow is auditable in one file.

Per-user, not per-org: a Gmail Send call writes from a specific mailbox. The
authenticated user's mailbox is the only correct sender, so each user authorizes
separately. Drive remains per-org because Drive ingest is org-level knowledge.

Surface area:
    * build_auth_url() / exchange_code() / store_credentials() / disconnect()
    * get_user_credentials() — fetch + refresh if needed
    * send_email() — Gmail REST users.messages.send
    * has_send_scope() — read-side scope check for the re-auth banner
"""
from __future__ import annotations

import asyncio
import base64
import logging
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import get_settings
from app.database import get_service_client

log = logging.getLogger(__name__)

# `openid` + `email` so we can resolve the user's gmail address at install
# time without a second call. `drive.readonly` is intentionally NOT requested
# here — Drive owns that scope through its own flow.
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
_SCOPES = [
    GMAIL_SEND_SCOPE,
    "openid",
    "email",
    "profile",
]


# ── OAuth flow ──────────────────────────────────────────────────────────────

def build_auth_url(*, state: str) -> str:
    """Construct Google's consent URL for the Gmail send scope."""
    settings = get_settings()
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.gmail_oauth_redirect_uri,
        "response_type": "code",
        "scope": " ".join(_SCOPES),
        "access_type": "offline",
        # Force the refresh token + consent screen each time so we always
        # capture refresh credentials even on re-installs (Google omits the
        # refresh_token after the first grant otherwise).
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


async def exchange_code(*, code: str) -> dict[str, Any]:
    """POST to Google's token endpoint. Returns the raw token payload (which
    contains access_token, refresh_token, expires_in, scope, id_token)."""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.gmail_oauth_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Gmail token exchange failed: {resp.status_code} {resp.text[:200]}")
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
    """Persist a gmail_integrations row for this (org, user). Resolves the
    Gmail address via the userinfo endpoint. Returns the stored row."""
    expires_in = int(token_payload.get("expires_in") or 0)
    expiry = (
        datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=expires_in)
        if expires_in
        else None
    )
    granted_scope = token_payload.get("scope") or ""
    scopes = [s for s in granted_scope.split() if s]

    if GMAIL_SEND_SCOPE not in scopes:
        # Don't persist a useless row — the user denied the send scope on
        # consent. Router translates this into a UI error.
        raise RuntimeError("gmail_send_scope_not_granted")

    userinfo = await _fetch_userinfo(token_payload["access_token"])
    email_address = userinfo.get("email")
    if not email_address:
        raise RuntimeError("Could not resolve Gmail address from userinfo.")

    svc = get_service_client()

    row = {
        "org_id": org_id,
        "user_id": user_id,
        "email_address": email_address,
        "access_token": token_payload["access_token"],
        "refresh_token": token_payload.get("refresh_token") or "",
        "token_expiry": expiry.isoformat() if expiry else None,
        "scopes": scopes,
    }

    def _upsert() -> dict[str, Any]:
        existing = (
            svc.table("gmail_integrations")
            .select("id, refresh_token")
            .eq("org_id", org_id)
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        if existing and existing.data:
            # Preserve a prior refresh_token if Google didn't issue a new one.
            if not row["refresh_token"]:
                row["refresh_token"] = existing.data.get("refresh_token") or ""
            if not row["refresh_token"]:
                raise RuntimeError("Refresh token missing on update; force re-consent.")
            svc.table("gmail_integrations").update(row).eq("id", existing.data["id"]).execute()
            return {**row, "id": existing.data["id"]}
        if not row["refresh_token"]:
            raise RuntimeError("First Gmail install must include a refresh_token.")
        inserted = svc.table("gmail_integrations").insert(row).execute()
        return inserted.data[0] if inserted.data else row

    return await asyncio.to_thread(_upsert)


async def disconnect(*, org_id: str, user_id: str) -> None:
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("gmail_integrations")
        .delete()
        .eq("org_id", org_id)
        .eq("user_id", user_id)
        .execute()
    )


# ── Credential retrieval + refresh ──────────────────────────────────────────

async def get_user_credentials(*, org_id: str, user_id: str) -> dict[str, Any] | None:
    """Return a credentials dict {access_token, email_address, scopes} with a
    fresh access token, refreshing via refresh_token if expired. Returns None
    when the user hasn't connected Gmail."""
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("gmail_integrations")
        .select("id, access_token, refresh_token, token_expiry, scopes, email_address")
        .eq("org_id", org_id)
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        return None
    data = row.data
    access_token = await _ensure_fresh_token(data, org_id=org_id, user_id=user_id)
    return {
        "access_token": access_token,
        "email_address": data["email_address"],
        "scopes": data.get("scopes") or [],
    }


async def _ensure_fresh_token(integ: dict[str, Any], *, org_id: str, user_id: str) -> str:
    """Return a non-expired access token; refresh via refresh_token if needed."""
    expiry = integ.get("token_expiry")
    if expiry:
        try:
            exp = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            # Refresh ~60s ahead of expiry to avoid races on the wire.
            if exp - timedelta(seconds=60) > datetime.now(timezone.utc):
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
        raise RuntimeError(f"gmail refresh failed: {resp.status_code} {resp.text[:200]}")
    payload = resp.json()
    new_access = payload["access_token"]
    new_expiry = (
        datetime.now(timezone.utc) + timedelta(seconds=int(payload.get("expires_in") or 3600))
    ).isoformat()

    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("gmail_integrations")
        .update({"access_token": new_access, "token_expiry": new_expiry})
        .eq("org_id", org_id)
        .eq("user_id", user_id)
        .execute()
    )
    return new_access


def has_send_scope(scopes: list[str] | None) -> bool:
    return bool(scopes) and GMAIL_SEND_SCOPE in scopes


# ── Send ────────────────────────────────────────────────────────────────────

def _build_mime(
    *,
    sender: str,
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    reply_to: str | None = None,
) -> str:
    """Compose a multipart/alternative MIME message (plain + simple HTML).

    Returns a base64url-encoded string ready for users.messages.send's `raw`.
    """
    message = MIMEMultipart("alternative")
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    if cc:
        message["Cc"] = cc
    if reply_to:
        message["Reply-To"] = reply_to

    plain = MIMEText(body, "plain", "utf-8")
    message.attach(plain)

    # Minimal HTML rendering: paragraph per blank line, <br> inside.
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    html_body = "".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paragraphs) or "<p></p>"
    html = MIMEText(
        f"<html><body style=\"font-family:-apple-system,sans-serif;font-size:14px;line-height:1.5;\">{html_body}</body></html>",
        "html",
        "utf-8",
    )
    message.attach(html)

    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


async def send_email(
    *,
    access_token: str,
    sender: str,
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    reply_to: str | None = None,
) -> dict[str, Any]:
    """POST to https://gmail.googleapis.com/gmail/v1/users/me/messages/send.

    Returns {message_id, thread_id}. Raises RuntimeError on a non-2xx
    response so the Inngest retry policy can do its work.
    """
    raw = _build_mime(sender=sender, to=to, subject=subject, body=body, cc=cc, reply_to=reply_to)

    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        resp = await client.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            json={"raw": raw},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )

    if resp.status_code == 401:
        # Token revoked or scope removed by the user; surface a distinct error
        # so the worker can park the row instead of retrying forever.
        raise PermissionError("gmail_send_unauthorized")
    if resp.status_code == 403:
        raise PermissionError("gmail_send_forbidden")
    if resp.status_code >= 400:
        raise RuntimeError(f"gmail send failed: {resp.status_code} {resp.text[:200]}")

    payload = resp.json()
    return {
        "message_id": payload.get("id"),
        "thread_id": payload.get("threadId"),
    }
