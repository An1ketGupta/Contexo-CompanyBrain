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

Privacy model (mirrors Google Meet transcripts, migrations 084–086):
  * Per-user opt-in. The webhook covers every host in the Zoom account, so
    a meeting is only ingested when its host has opted in ("Sync my Zoom
    meeting transcripts" on the settings page). Opt-ins live in the org row's
    `metadata.transcript_optins` as {lowercased email: app user_id}; the
    webhook matches on the payload's `host_email`. Best-effort match — a
    user whose app email differs from their Zoom email can't opt in today.
    The connecting admin is auto-opted-in with their Zoom account email
    (they clearly consented, and it's the one email we know matches Zoom).
  * Attribution to the actual host. `created_by` is the opted-in host's
    user_id, not whoever connected the integration.
  * Private by default. The migration-086 trigger forces visibility='private'
    on insert, so a transcript is searchable only by its host until they
    publish it to the org.

Attendee auto-sharing (migration 089): a second, separate opt-in
("Auto-share transcripts of meetings I attend", `attendee_optins` in the
same metadata blob) lets an attendee automatically get read access to a
transcript of a meeting they were in, without the host publishing it
org-wide. Consent is deliberately distinct from the host toggle — being on
someone else's call isn't the same consent as recording your own.

Roster capture rides the `meeting.participant_joined` webhook (needs the
`meeting:read:participant` scope added on the Zoom Marketplace app —
already-connected orgs need to reconnect once for it to start firing).
Each join event upserts a row into `zoom_meeting_participants`, keyed on
(meeting_uuid, email) — idempotent against Zoom's known duplicate
redelivery. When `recording.transcript_completed` lands for that same
meeting_uuid, the buffered participant emails are matched against
`attendee_optins` and each match gets a `document_shares` grant on the new
transcript (and, once MeetingNotesAgent mints it, the derived summary doc
too). Email matching is best-effort and internal-only: Zoom only populates
`participant.email` for attendees who joined logged into the Zoom account,
so external guests never match.
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
OPTINS_KEY = "transcript_optins"  # metadata: {lowercased email: app user_id}
ATTENDEE_OPTINS_KEY = "attendee_optins"  # metadata: {lowercased email: app user_id}

_ZOOM_API = "https://api.zoom.us/v2"
_ZOOM_OAUTH = "https://zoom.us/oauth"

# Documentation only — Zoom's user-managed OAuth apps grant scopes from the
# Marketplace app's own config, not a `scope` param on the authorize URL, so
# this list isn't sent anywhere in this file. Keep it in sync with what's
# actually enabled on the Marketplace app. meeting:read:participant backs
# the meeting.participant_joined webhook (attendee auto-share, migration
# 089) — adding it there requires already-connected orgs to reconnect once.
_SCOPES = ["cloud_recording:read", "user:read:user", "meeting:read:participant"]

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

    # Preserve teammates' opt-ins across an admin re-connect, and auto-opt-in
    # the connector under their Zoom email (see module docstring).
    existing = await _unified.get_row(org_id=org_id, provider=PROVIDER)
    optins: dict[str, str] = dict(
        ((existing or {}).get("metadata") or {}).get(OPTINS_KEY) or {}
    )
    if account.get("email"):
        optins[account["email"].lower()] = user_id

    await _unified.upsert_row(
        org_id=org_id,
        provider=PROVIDER,
        connected_by=user_id,
        access_token=access_token,
        refresh_token=token_payload.get("refresh_token"),
        token_expiry=expiry,
        scopes=(token_payload.get("scope") or "").split(),
        metadata={
            "account_id": account["account_id"],
            "email": account.get("email"),
            OPTINS_KEY: optins,
        },
    )


async def disconnect(*, org_id: str) -> None:
    await _unified.delete_row(org_id=org_id, provider=PROVIDER)


async def find_org_by_account_id(account_id: str) -> dict[str, Any] | None:
    return await _unified.find_row_by_metadata(
        provider=PROVIDER, key="account_id", value=account_id
    )


# ── Per-user transcript opt-in ──────────────────────────────────────────────


def resolve_opted_in_host(row: dict[str, Any], host_email: str | None) -> str | None:
    """Map a webhook's host_email to the opted-in app user_id, or None.

    None means "this host never consented" and the meeting must be skipped —
    the webhook fires for every host in the Zoom account, opted-in or not.
    """
    if not host_email:
        return None
    optins = (row.get("metadata") or {}).get(OPTINS_KEY) or {}
    return optins.get(host_email.strip().lower())


def is_user_opted_in(row: dict[str, Any] | None, user_id: str) -> bool:
    if not row:
        return False
    optins = (row.get("metadata") or {}).get(OPTINS_KEY) or {}
    return user_id in optins.values()


async def set_transcript_optin(
    *, org_id: str, user_id: str, email: str, opted_in: bool
) -> None:
    """Add/remove the caller from the org row's opt-in map.

    Matching is by email (Zoom's host_email vs the app auth email), so a user
    whose Zoom email differs from their app email won't match — best effort,
    documented in the module docstring. Opting out also removes any stale
    entries pointing at this user_id under an old email.
    """
    row = await _unified.get_row(org_id=org_id, provider=PROVIDER)
    if not row:
        raise RuntimeError("zoom_not_connected")
    metadata = dict(row.get("metadata") or {})
    optins: dict[str, str] = {
        k: v for k, v in (metadata.get(OPTINS_KEY) or {}).items() if v != user_id
    }
    if opted_in:
        optins[email.strip().lower()] = user_id
    metadata[OPTINS_KEY] = optins
    await _unified.update_fields(
        org_id=org_id, provider=PROVIDER, fields={"metadata": metadata}
    )


# ── Attendee auto-share opt-in ──────────────────────────────────────────────
# Separate map from OPTINS_KEY (hosting consent). Same shape, same
# email-matching caveat — see module docstring.


def is_attendee_opted_in(row: dict[str, Any] | None, user_id: str) -> bool:
    if not row:
        return False
    optins = (row.get("metadata") or {}).get(ATTENDEE_OPTINS_KEY) or {}
    return user_id in optins.values()


async def set_attendee_optin(
    *, org_id: str, user_id: str, email: str, opted_in: bool
) -> None:
    row = await _unified.get_row(org_id=org_id, provider=PROVIDER)
    if not row:
        raise RuntimeError("zoom_not_connected")
    metadata = dict(row.get("metadata") or {})
    optins: dict[str, str] = {
        k: v for k, v in (metadata.get(ATTENDEE_OPTINS_KEY) or {}).items() if v != user_id
    }
    if opted_in:
        optins[email.strip().lower()] = user_id
    metadata[ATTENDEE_OPTINS_KEY] = optins
    await _unified.update_fields(
        org_id=org_id, provider=PROVIDER, fields={"metadata": metadata}
    )


def resolve_opted_in_attendees(
    row: dict[str, Any],
    *,
    participant_emails: list[str],
    exclude_user_id: str | None,
) -> list[str]:
    """Map buffered participant emails to opted-in app user_ids.

    Excludes exclude_user_id (the host — they already own the doc via
    created_by, a share grant would be redundant) and de-dupes: two
    participant rows resolving to the same user_id (rejoin, alt email)
    collapse to one share.
    """
    optins = (row.get("metadata") or {}).get(ATTENDEE_OPTINS_KEY) or {}
    resolved: set[str] = set()
    for email in participant_emails:
        user_id = optins.get(email.strip().lower())
        if user_id and user_id != exclude_user_id:
            resolved.add(user_id)
    return sorted(resolved)


# ── Meeting roster buffer ────────────────────────────────────────────────────


async def record_participant(*, org_id: str, meeting_uuid: str, email: str | None) -> None:
    """Upsert a joined-meeting participant into the roster buffer.

    Idempotent on (meeting_uuid, email) — Zoom is known to redeliver
    participant_joined webhooks. Silently no-ops without an email (external
    guests who joined without logging into Zoom never have one).
    """
    if not email:
        return
    svc = get_service_client()

    import asyncio

    def _upsert() -> None:
        svc.table("zoom_meeting_participants").upsert(
            {
                "org_id": org_id,
                "meeting_uuid": meeting_uuid,
                "email": email.strip().lower(),
            },
            on_conflict="meeting_uuid,email",
        ).execute()

    await asyncio.to_thread(_upsert)


async def get_participant_emails(meeting_uuid: str) -> list[str]:
    svc = get_service_client()

    import asyncio

    def _select() -> list[str]:
        result = (
            svc.table("zoom_meeting_participants")
            .select("email")
            .eq("meeting_uuid", meeting_uuid)
            .execute()
        )
        return [row["email"] for row in (result.data or [])]

    return await asyncio.to_thread(_select)


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


async def grant_document_shares(
    *, document_id: str, org_id: str, user_ids: list[str]
) -> None:
    """Grant read access to a document for a list of users (attendee
    auto-share). Idempotent — document_shares has a unique (document_id,
    user_id) constraint, upsert ignores rows that already exist.
    """
    if not user_ids:
        return
    svc = get_service_client()

    import asyncio

    def _upsert() -> None:
        svc.table("document_shares").upsert(
            [
                {"document_id": document_id, "org_id": org_id, "user_id": uid}
                for uid in user_ids
            ],
            on_conflict="document_id,user_id",
            ignore_duplicates=True,
        ).execute()

    await asyncio.to_thread(_upsert)


async def ingest_transcript_from_webhook(
    *,
    org_id: str,
    user_id: str | None,
    download_url: str,
    download_token: str,
    meeting_topic: str,
    meeting_uuid: str,
    attendee_user_ids: list[str] | None = None,
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

    if attendee_user_ids:
        await grant_document_shares(
            document_id=doc_id, org_id=org_id, user_ids=attendee_user_ids
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
