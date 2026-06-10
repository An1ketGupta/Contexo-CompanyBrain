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

_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


# ── OAuth flow ──────────────────────────────────────────────────────────────

def build_auth_url(*, state: str) -> str:
    """Construct Google's consent URL. `state` is an HMAC-signed JWT that
    encodes the calling user's id + org id; we verify on callback to prevent
    CSRF + to bind the resulting tokens to the right tenant."""
    settings = get_settings()
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": " ".join(_SCOPES),
        "access_type": "offline",
        "prompt": "consent",  # force refresh_token issuance on every install
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
    """Upsert the drive_integrations row with the issued tokens."""
    svc = get_service_client()
    expires_in = int(token_payload.get("expires_in") or 0)
    expiry = (
        datetime.now(timezone.utc).replace(microsecond=0)
        + _timedelta(seconds=expires_in)
        if expires_in
        else None
    )

    row = {
        "org_id": org_id,
        "connected_by": user_id,
        "access_token": token_payload["access_token"],
        # Some flows return only access_token on re-consent; preserve a prior
        # refresh_token rather than wiping it.
        "refresh_token": token_payload.get("refresh_token") or "",
        "token_expiry": expiry.isoformat() if expiry else None,
    }

    def _run() -> None:
        existing = (
            svc.table("drive_integrations").select("id, refresh_token").eq("org_id", org_id)
            .maybe_single().execute()
        )
        if existing and existing.data:
            # Preserve old refresh_token when Google didn't issue a new one.
            if not row["refresh_token"]:
                row["refresh_token"] = existing.data.get("refresh_token") or ""
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
