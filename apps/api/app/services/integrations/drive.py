"""Google Drive integration (Day 14 / #86).

Surface area:
    * OAuth flow: build_auth_url() / exchange_code()
    * Polling: poll_all_integrations() — invoked by Inngest every 5 min
    * Per-org sync: sync_org() — drains pending Drive changes for one org

Implementation note on dependencies: `google-auth*` and `google-api-python-
client` aren't in the base install (they're heavy + only Drive needs them).
We import them lazily inside the functions that need them so the rest of
the app boots cleanly without the package.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import get_settings
from app.database import get_service_client
from app.services.integrations.text_ingest import upsert_external_document

log = logging.getLogger(__name__)

SOURCE_TAG = "google_drive"

# MIME -> our internal file_type label. Google Docs/Sheets aren't downloaded
# as their native binary format; we export them as plain text + xlsx
# respectively in `_download_for_ingest`.
_SUPPORTED_MIME = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "text/plain": "txt",
    "text/markdown": "md",
    "application/vnd.google-apps.document": "txt",   # export to text/plain
    "application/vnd.google-apps.spreadsheet": "xlsx",  # export to xlsx
}

# Drive ingestion only needs read. The Docs write surface (Agent Day 4) needs
# `documents` AND `drive.file` so we can both create the doc and share it back
# with the requesting user. `drive.file` is the minimum-trust write scope —
# it only sees files the app creates, NOT the user's existing Drive. That
# matches the "create AI output as a new doc" UX exactly.
DOCS_WRITE_SCOPE = "https://www.googleapis.com/auth/documents"
DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    DOCS_WRITE_SCOPE,
    DRIVE_FILE_SCOPE,
]


def has_docs_write_scope(scopes: list[str] | None) -> bool:
    if not scopes:
        return False
    return DOCS_WRITE_SCOPE in scopes and DRIVE_FILE_SCOPE in scopes


# ── OAuth flow ──────────────────────────────────────────────────────────────

def build_auth_url(*, state: str) -> str:
    """Construct Google's consent URL. `state` is an HMAC-signed JWT that
    encodes the calling user's id + org id; we verify on callback to prevent
    CSRF + to bind the resulting tokens to the right tenant.

    Scopes requested: drive.readonly (existing ingest path), documents +
    drive.file (Agent Day 4 — Docs export). `include_granted_scopes=true`
    means Google folds previously-granted scopes into the new token, so a
    user who only had drive.readonly before getting Day 4 doesn't lose their
    existing ingest while gaining Docs write.
    """
    settings = get_settings()
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": " ".join(_SCOPES),
        "access_type": "offline",
        "prompt": "consent",  # force refresh_token issuance on every install
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


async def exchange_code(*, code: str) -> dict[str, Any]:
    """POST to Google's token endpoint. Returns the raw token payload."""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_oauth_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Google token exchange failed: {resp.status_code} {resp.text[:200]}")
    return resp.json()


async def store_credentials(
    *,
    org_id: str,
    user_id: str,
    token_payload: dict[str, Any],
) -> None:
    """Upsert the drive_integrations row with the issued tokens.

    Also persists the granted scope list so the UI can decide whether to
    show Docs-write affordances (Agent Day 4). When Google omits the scope
    field on token refresh, we fall back to whatever the row already had.
    """
    svc = get_service_client()
    expires_in = int(token_payload.get("expires_in") or 0)
    expiry = (
        datetime.now(timezone.utc).replace(microsecond=0)
        + _timedelta(seconds=expires_in)
        if expires_in
        else None
    )

    granted_scope = token_payload.get("scope") or ""
    scopes = [s for s in granted_scope.split() if s]

    row = {
        "org_id": org_id,
        "connected_by": user_id,
        "access_token": token_payload["access_token"],
        # Some flows return only access_token on re-consent; preserve a prior
        # refresh_token rather than wiping it.
        "refresh_token": token_payload.get("refresh_token") or "",
        "token_expiry": expiry.isoformat() if expiry else None,
        "scopes": scopes,
    }

    def _run() -> None:
        existing = (
            svc.table("drive_integrations").select("id, refresh_token, scopes").eq("org_id", org_id)
            .maybe_single().execute()
        )
        if existing and existing.data:
            # Preserve old refresh_token when Google didn't issue a new one.
            if not row["refresh_token"]:
                row["refresh_token"] = existing.data.get("refresh_token") or ""
            # Preserve prior scopes if the refresh response didn't carry them.
            if not row["scopes"]:
                row["scopes"] = existing.data.get("scopes") or []
            svc.table("drive_integrations").update(row).eq("id", existing.data["id"]).execute()
        else:
            if not row["refresh_token"]:
                raise RuntimeError("First Drive install must include a refresh_token.")
            svc.table("drive_integrations").insert(row).execute()

    await asyncio.to_thread(_run)


def _timedelta(*, seconds: int):
    from datetime import timedelta
    return timedelta(seconds=seconds)


async def disconnect(*, org_id: str) -> None:
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("drive_integrations").delete().eq("org_id", org_id).execute()
    )


# ── Polling sync ────────────────────────────────────────────────────────────

async def poll_all_integrations() -> dict[str, Any]:
    """Inngest cron entry point: iterate over connected orgs and sync each."""
    svc = get_service_client()
    rows = await asyncio.to_thread(
        lambda: svc.table("drive_integrations")
        .select("org_id, folder_ids, last_synced_at")
        .execute()
    )
    synced = 0
    errors = 0
    for row in rows.data or []:
        try:
            await sync_org(org_id=row["org_id"])
            synced += 1
        except Exception as exc:
            errors += 1
            log.warning("drive_sync_failed", org_id=row["org_id"], error=str(exc))
    return {"synced": synced, "errors": errors}


async def sync_org(*, org_id: str) -> dict[str, Any]:
    """Pull new/updated files from the configured folders for one org.

    Strategy: list files modified since `last_synced_at` in each configured
    folder, ingest each supported MIME, then bump `last_synced_at`. We DON'T
    rely on Drive's "changes" API to keep auth simple — the polling cost is
    trivial at our scale.
    """
    svc = get_service_client()
    integ = await asyncio.to_thread(
        lambda: svc.table("drive_integrations")
        .select("org_id, access_token, refresh_token, token_expiry, folder_ids, last_synced_at, connected_by")
        .eq("org_id", org_id)
        .maybe_single().execute()
    )
    if not integ or not integ.data:
        return {"status": "no-integration"}

    data = integ.data
    if not data.get("folder_ids"):
        return {"status": "no-folders"}

    access_token = await _ensure_fresh_token(data)
    last_synced = data.get("last_synced_at") or "1970-01-01T00:00:00Z"

    ingested = 0
    for folder_id in data["folder_ids"]:
        files = await _list_modified_files(
            access_token=access_token, folder_id=folder_id, modified_since=last_synced
        )
        for f in files:
            mime = f.get("mimeType")
            if mime not in _SUPPORTED_MIME:
                continue
            try:
                await _ingest_file(
                    org_id=org_id,
                    user_id=data.get("connected_by"),
                    access_token=access_token,
                    file=f,
                )
                ingested += 1
            except Exception as exc:
                log.warning("drive_file_ingest_failed", file_id=f.get("id"), error=str(exc))

    await asyncio.to_thread(
        lambda: svc.table("drive_integrations")
        .update({"last_synced_at": datetime.now(timezone.utc).isoformat()})
        .eq("org_id", org_id).execute()
    )
    return {"status": "ok", "ingested": ingested}


async def _ensure_fresh_token(integ: dict[str, Any]) -> str:
    """Return a non-expired access token. Refresh via refresh_token if needed."""
    expiry = integ.get("token_expiry")
    if expiry:
        try:
            exp = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            if exp > datetime.now(timezone.utc):
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
        raise RuntimeError(f"refresh failed: {resp.status_code} {resp.text[:200]}")
    payload = resp.json()
    new_access = payload["access_token"]
    new_expiry = (
        datetime.now(timezone.utc) + _timedelta(seconds=int(payload.get("expires_in") or 3600))
    ).isoformat()

    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("drive_integrations")
        .update({"access_token": new_access, "token_expiry": new_expiry})
        .eq("org_id", integ["org_id"]).execute()
    )
    return new_access


async def _list_modified_files(
    *,
    access_token: str,
    folder_id: str,
    modified_since: str,
) -> list[dict[str, Any]]:
    """Single page of Drive v3 files.list. We cap at 50 per cycle so a giant
    folder doesn't blow the polling window."""
    q = (
        f"'{folder_id}' in parents and trashed = false "
        f"and modifiedTime > '{modified_since}'"
    )
    params = {
        "q": q,
        "fields": "files(id,name,mimeType,modifiedTime)",
        "pageSize": "50",
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.get(
            "https://www.googleapis.com/drive/v3/files",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"drive list failed: {resp.status_code} {resp.text[:200]}")
    return resp.json().get("files", [])


async def _ingest_file(
    *,
    org_id: str,
    user_id: str | None,
    access_token: str,
    file: dict[str, Any],
) -> None:
    """Pull plaintext (or export) and upsert the documents row.

    For Drive-native types (Docs, Sheets) we use the export endpoint; for
    binary types (PDF, DOCX) we'd ideally pipe them through the regular
    parser, but Day-14 scaffolding scope: Docs/Sheets via export, everything
    else gets a "supported but not yet wired" log line.
    """
    file_id = file["id"]
    mime = file["mimeType"]
    name = file.get("name") or f"Drive file {file_id}"
    file_type = _SUPPORTED_MIME[mime]

    text: str | None = None
    if mime == "application/vnd.google-apps.document":
        text = await _export(access_token, file_id, "text/plain")
    elif mime == "application/vnd.google-apps.spreadsheet":
        # Export as CSV — embeddings work fine on tabular text.
        text = await _export(access_token, file_id, "text/csv")
        file_type = "csv"
    elif mime == "text/plain" or mime == "text/markdown":
        text = await _download_raw(access_token, file_id)
    else:
        # PDF/DOCX/XLSX/PPTX flow lands in a follow-up — they need the binary
        # to go through services.ingestion.parser. For Day-14 scaffolding we
        # log and skip so admins can see progress in the integration UI.
        log.info("drive_skip_binary_type", file_id=file_id, mime=mime)
        return

    if not text:
        log.warning("drive_empty_export", file_id=file_id)
        return

    doc_id = await upsert_external_document(
        org_id=org_id,
        source=SOURCE_TAG,
        external_id=file_id,
        name=name,
        file_type=file_type,
        user_id=user_id,
    )
    # Queue ingestion via the standard text-document event so we share the
    # chunk/embed pipeline with the email-forward path.
    import inngest
    from app.inngest.client import get_inngest_client
    client = get_inngest_client()
    await client.send(
        inngest.Event(
            name="doc/process-text",
            data={"doc_id": doc_id, "org_id": org_id, "text": text},
            id=f"drive-{doc_id}-{file.get('modifiedTime') or ''}",
        )
    )


async def _export(access_token: str, file_id: str, mime: str) -> str:
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.get(
            f"https://www.googleapis.com/drive/v3/files/{file_id}/export",
            params={"mimeType": mime},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"export failed {file_id}: {resp.status_code}")
    return resp.text


async def _download_raw(access_token: str, file_id: str) -> str:
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.get(
            f"https://www.googleapis.com/drive/v3/files/{file_id}",
            params={"alt": "media"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"download failed {file_id}: {resp.status_code}")
    return resp.text


# ── Folder management (called by the integrations router) ───────────────────

# ── Org credentials helper (used by Docs write surface) ─────────────────────


async def get_org_credentials(org_id: str) -> dict[str, Any] | None:
    """Return {access_token, scopes} for an org's Drive install, refreshing
    if expired. None if Drive isn't connected.

    The Docs write path (create_document) uses this — it needs a fresh
    access_token + the granted scope list so it can fail fast if the
    documents scope wasn't granted.
    """
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("drive_integrations")
        .select("org_id, access_token, refresh_token, token_expiry, scopes")
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        return None
    data = row.data
    fresh = await _ensure_fresh_token(data)
    return {
        "access_token": fresh,
        "scopes": data.get("scopes") or [],
    }


# ── Google Docs write (Agent Day 4) ─────────────────────────────────────────
#
# We POST directly to the Docs + Drive REST APIs rather than pulling
# google-api-python-client. Two endpoints:
#   1. POST https://docs.googleapis.com/v1/documents — create empty doc
#   2. POST https://docs.googleapis.com/v1/documents/{id}:batchUpdate — insert
#      text via an insertText request. (`documents.create` doesn't accept body
#      content; you create empty then batchUpdate to fill.)
# Optional:
#   3. POST https://www.googleapis.com/drive/v3/files/{id}/permissions — share
#      the doc back to the requesting user as writer so it shows up in their
#      "Shared with me" without an extra step.


async def create_document(
    *,
    org_id: str,
    title: str,
    content: str,
    share_with_email: str | None = None,
) -> dict[str, Any]:
    """Create a Google Doc, fill it with `content`, optionally share back.

    Raises PermissionError on missing-scope / revoked-token so the Inngest
    worker can fail fast instead of burning retry budget. The "share with
    requester" step is best-effort — a failure there doesn't roll back the
    doc, because the user can always open the doc URL.
    """
    creds = await get_org_credentials(org_id)
    if not creds:
        raise PermissionError("drive_not_connected")
    if not has_docs_write_scope(creds.get("scopes")):
        raise PermissionError("docs_write_scope_missing")

    access_token = creds["access_token"]
    title = (title or "Untitled").strip() or "Untitled"

    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        # Step 1: create empty doc.
        create_resp = await client.post(
            "https://docs.googleapis.com/v1/documents",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"title": title[:512]},  # Docs caps title at ~512 chars
        )
        if create_resp.status_code in (401, 403):
            raise PermissionError("docs_unauthorized")
        if create_resp.status_code >= 400:
            raise RuntimeError(
                f"docs_create_failed: {create_resp.status_code} {create_resp.text[:200]}"
            )
        doc = create_resp.json()
        doc_id = doc.get("documentId")
        if not doc_id:
            raise RuntimeError("docs_create_missing_id")

        # Step 2: insert content via batchUpdate. Index 1 = right after the
        # auto-inserted "title heading" Docs starts every doc with.
        body_text = content or ""
        if body_text:
            update_resp = await client.post(
                f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "requests": [
                        {
                            "insertText": {
                                "location": {"index": 1},
                                "text": body_text,
                            }
                        }
                    ]
                },
            )
            if update_resp.status_code >= 400:
                # Doc exists but the insert failed — surface as RuntimeError
                # so the worker retries. (The doc itself can be reused on
                # retry — we'll just append again, which is acceptable for a
                # failure mode that essentially never fires.)
                raise RuntimeError(
                    f"docs_insert_failed: {update_resp.status_code} {update_resp.text[:200]}"
                )

        # Step 3: optionally share with the requesting user. Best-effort.
        if share_with_email:
            try:
                await client.post(
                    f"https://www.googleapis.com/drive/v3/files/{doc_id}/permissions",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                    params={"sendNotificationEmail": "false"},
                    json={
                        "type": "user",
                        "role": "writer",
                        "emailAddress": share_with_email,
                    },
                )
            except Exception as exc:
                log.warning("docs_share_failed", doc_id=doc_id, error=str(exc))

    return {
        "doc_id": doc_id,
        "url": f"https://docs.google.com/document/d/{doc_id}/edit",
        "title": title,
    }


async def add_folder(*, org_id: str, folder_id: str, folder_name: str | None) -> list[str]:
    svc = get_service_client()
    integ = await asyncio.to_thread(
        lambda: svc.table("drive_integrations").select("folder_ids").eq("org_id", org_id)
        .maybe_single().execute()
    )
    if not integ or not integ.data:
        raise RuntimeError("Drive is not connected for this organization.")
    folders = list(integ.data.get("folder_ids") or [])
    if folder_id not in folders:
        folders.append(folder_id)
    await asyncio.to_thread(
        lambda: svc.table("drive_integrations").update({"folder_ids": folders})
        .eq("org_id", org_id).execute()
    )
    return folders


async def remove_folder(*, org_id: str, folder_id: str) -> list[str]:
    svc = get_service_client()
    integ = await asyncio.to_thread(
        lambda: svc.table("drive_integrations").select("folder_ids").eq("org_id", org_id)
        .maybe_single().execute()
    )
    if not integ or not integ.data:
        return []
    folders = [f for f in (integ.data.get("folder_ids") or []) if f != folder_id]
    await asyncio.to_thread(
        lambda: svc.table("drive_integrations").update({"folder_ids": folders})
        .eq("org_id", org_id).execute()
    )
    return folders
