"""Zoom cloud-recording transcript integration.

Unlike OneDrive/Dropbox/Confluence (which use `_unified.queue_binary_ingest`
to skip the Supabase Storage round trip entirely), Zoom transcripts need to
actually land in Storage: `MeetingNotesAgent.run()` (services/agents/
meeting_notes_agent.py) downloads via `download_from_storage(file_path)`,
the same way a manually-uploaded transcript does. So the ingest path here
mirrors `services/integrations/slack_ingest.py::_ingest_binary_file` —
upsert a placeholder documents row, upload the real bytes to Storage, patch
`file_path`, then fire the same `doc/uploaded` event a manual upload fires
(standard chunk/embed pipeline) followed by `meeting/transcript-uploaded`
(MeetingNotesAgent: decisions, action items, derived summary doc). No new
transcript parser needed — services/parsers/meeting_transcript.py already
handles Zoom's native WebVTT format.

Delivery is a webhook (`recording.transcript_completed`), not polling. Zoom
fires it account-wide, independent of any org-scoped request, so we resolve
org_id from the webhook payload's `account_id` via
`_unified.find_row_by_metadata`.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import get_settings
from app.database import get_service_client
from app.services.integrations import _unified
from app.services.integrations.text_ingest import upsert_external_document

log = logging.getLogger(__name__)

PROVIDER = "zoom"
STORAGE_BUCKET = "document"

_ZOOM_API = "https://api.zoom.us/v2"
_ZOOM_OAUTH = "https://zoom.us/oauth"

_SCOPES = ["cloud_recording:read", "user:read:user"]

_unified.register_refresh(
    PROVIDER,
    token_url=f"{_ZOOM_OAUTH}/token",
    client_id_attr="zoom_client_id",
    client_secret_attr="zoom_client_secret",
    auth_style="basic",  # Zoom expects client credentials as Basic auth on refresh
)


# ── OAuth flow ──────────────────────────────────────────────────────────────


def build_auth_url(*, state: str) -> str:
    settings = get_settings()
    params = {
        "client_id": settings.zoom_client_id,
        "response_type": "code",
        "redirect_uri": settings.zoom_oauth_redirect_uri,
        "state": state,
    }
    return f"{_ZOOM_OAUTH}/authorize?{urlencode(params)}"


async def exchange_code(*, code: str) -> dict[str, Any]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(
            f"{_ZOOM_OAUTH}/token",
            data={
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": settings.zoom_oauth_redirect_uri,
            },
            auth=(settings.zoom_client_id, settings.zoom_client_secret),
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"zoom_token_exchange_failed: {resp.status_code} {resp.text[:200]}"
        )
    return resp.json()


async def _fetch_account(token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.get(
            f"{_ZOOM_API}/users/me",
            headers={"Authorization": f"Bearer {token}"},
        )
    if resp.status_code != 200:
        return {}
    body = resp.json()
    return {"account_id": body.get("account_id"), "email": body.get("email")}


async def store_credentials(
    *, org_id: str, user_id: str, token_payload: dict[str, Any]
) -> None:
    expires_in = int(token_payload.get("expires_in") or 3600)
    expiry = datetime.now(UTC) + timedelta(seconds=expires_in)
    access_token = token_payload["access_token"]
    account = await _fetch_account(access_token)
    if not account.get("account_id"):
        raise RuntimeError("zoom_account_lookup_failed")
    await _unified.upsert_row(
        org_id=org_id,
        provider=PROVIDER,
        connected_by=user_id,
        access_token=access_token,
        refresh_token=token_payload.get("refresh_token"),
        token_expiry=expiry,
        scopes=(token_payload.get("scope") or "").split(),
        metadata={"account_id": account["account_id"], "email": account.get("email")},
    )


async def disconnect(*, org_id: str) -> None:
    await _unified.delete_row(org_id=org_id, provider=PROVIDER)


async def find_org_by_account_id(account_id: str) -> dict[str, Any] | None:
    return await _unified.find_row_by_metadata(
        provider=PROVIDER, key="account_id", value=account_id
    )


# ── Webhook verification ────────────────────────────────────────────────────


def verify_webhook_signature(*, body: bytes, timestamp: str, signature: str) -> bool:
    settings = get_settings()
    if not settings.zoom_webhook_secret_token:
        return False
    message = f"v0:{timestamp}:{body.decode('utf-8')}"
    expected = "v0=" + hmac.new(
        settings.zoom_webhook_secret_token.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def build_url_validation_response(plain_token: str) -> dict[str, str]:
    settings = get_settings()
    encrypted = hmac.new(
        settings.zoom_webhook_secret_token.encode(),
        plain_token.encode(),
        hashlib.sha256,
    ).hexdigest()
    return {"plainToken": plain_token, "encryptedToken": encrypted}


# ── Transcript ingest ────────────────────────────────────────────────────────


async def ingest_transcript_from_webhook(
    *,
    org_id: str,
    user_id: str | None,
    download_url: str,
    download_token: str,
    meeting_topic: str,
    meeting_uuid: str,
) -> dict[str, Any]:
    """Download the .vtt transcript and run it through the exact same
    pipeline a manual upload uses, so MeetingNotesAgent picks it up.

    Idempotent on (org_id, source='zoom', external_id=meeting_uuid) via
    `upsert_external_document` — safe against Zoom's webhook retries.
    """
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True) as client:
        resp = await client.get(download_url, params={"access_token": download_token})
    if resp.status_code != 200:
        raise RuntimeError(f"zoom_transcript_download_failed: {resp.status_code}")
    raw = resp.content

    display_name = f"{meeting_topic or 'Zoom meeting'} — transcript"
    doc_id = await upsert_external_document(
        org_id=org_id,
        source=PROVIDER,
        external_id=meeting_uuid,
        name=display_name,
        file_type="vtt",
        user_id=user_id,
    )

    safe_name = display_name.replace("/", "_").replace("..", "_").strip() or "zoom_transcript"
    storage_path = f"orgs/{org_id}/docs/{doc_id}/{safe_name}.vtt"

    svc = get_service_client()

    import asyncio

    def _upload() -> None:
        svc.storage.from_(STORAGE_BUCKET).upload(
            path=storage_path,
            file=raw,
            file_options={"content-type": "text/vtt", "upsert": "true"},
        )

    await asyncio.to_thread(_upload)

    def _patch_path() -> None:
        svc.table("documents").update({"file_path": storage_path}).eq(
            "id", doc_id
        ).execute()

    await asyncio.to_thread(_patch_path)

    import inngest

    from app.inngest.client import get_inngest_client

    ingest_client = get_inngest_client()
    await ingest_client.send(
        inngest.Event(
            name="doc/uploaded",
            data={"doc_id": doc_id, "org_id": org_id},
            id=f"zoom-{doc_id}-uploaded",
        )
    )
    await ingest_client.send(
        inngest.Event(
            name="meeting/transcript-uploaded",
            data={"doc_id": doc_id, "org_id": org_id, "file_type": "vtt"},
            id=f"zoom-{doc_id}-meeting-notes",
        )
    )
    return {"status": "ok", "doc_id": doc_id}
