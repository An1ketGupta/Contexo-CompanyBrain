"""Google Meet transcript auto-ingest (per-user, via Google Workspace OAuth).

Google Meet auto-saves a transcript as a Google Doc in a "Meet Recordings"
folder in the organizer/recorder's My Drive shortly after a recorded call
ends. This module detects those docs and feeds them into the knowledge base
as regular searchable documents — same "export Google Doc -> text ->
doc/process-text" path the org-scoped Drive integration already uses for
Google Docs (see services/integrations/drive.py:_ingest_file). No new
parser, no MeetingNotesAgent routing — the transcript just becomes another
document that hybrid search can surface (e.g. when a meeting-prep brief
searches the KB for an attendee's company).

Folder-scoped, not Drive-wide: the Google Workspace OAuth grant is drive.file
only (see google_workspace.py) — no blanket "see all your Drive files" scope.
The user explicitly picks one or more folders via the Google Picker UI
(settings/integrations page), which grants drive.file access limited to
those folders. We store the chosen IDs in this row's
`metadata.meet_transcript_folder_ids` and only ever list files inside them.
Users who haven't picked a folder are skipped silently by poll_all_users().
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from app.database import get_service_client
from app.services.integrations import google_workspace
from app.services.integrations.text_ingest import upsert_external_document

log = logging.getLogger(__name__)

SOURCE_TAG = "google_meet_transcript"
_PROVIDER = "google_workspace"
_TRANSCRIPT_MIME = "application/vnd.google-apps.document"
_FOLDER_IDS_KEY = "meet_transcript_folder_ids"


async def poll_all_users() -> dict[str, Any]:
    """Inngest cron entry point: iterate over Google Workspace connections
    that have at least one Meet-transcript folder configured and sync each."""
    svc = get_service_client()
    rows = await asyncio.to_thread(
        lambda: svc.table("integrations")
        .select("org_id, scope_user_id, metadata")
        .eq("provider", _PROVIDER)
        .execute()
    )
    synced = 0
    skipped = 0
    errors = 0
    for row in rows.data or []:
        if not (row.get("metadata") or {}).get(_FOLDER_IDS_KEY):
            skipped += 1
            continue
        user_id = row.get("scope_user_id")
        if not user_id:
            continue
        try:
            await sync_user(org_id=row["org_id"], user_id=user_id)
            synced += 1
        except Exception as exc:
            errors += 1
            log.warning(
                "meet_transcript_sync_failed user=%s err=%s", user_id, exc
            )
    return {"synced": synced, "skipped": skipped, "errors": errors}


async def sync_user(*, org_id: str, user_id: str) -> dict[str, Any]:
    """Pull new Meet transcript docs from the user's connected folders since
    their last synced cursor, ingest each as a KB document."""
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("integrations")
        .select("id, metadata")
        .eq("org_id", org_id)
        .eq("provider", _PROVIDER)
        .eq("scope_user_id", user_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        return {"status": "no-integration"}

    metadata = row.data.get("metadata") or {}
    folder_ids = metadata.get(_FOLDER_IDS_KEY) or []
    if not folder_ids:
        return {"status": "no-folders-configured"}

    access_token = await google_workspace.get_user_token(org_id=org_id, user_id=user_id)
    if not access_token:
        return {"status": "no-token"}

    last_synced = metadata.get("meet_transcripts_last_synced_at") or "1970-01-01T00:00:00Z"

    seen_ids: set[str] = set()
    files: list[dict[str, Any]] = []
    for folder_id in folder_ids:
        for f in await _list_transcript_files(
            access_token=access_token, folder_id=folder_id, modified_since=last_synced
        ):
            if f["id"] not in seen_ids:
                seen_ids.add(f["id"])
                files.append(f)

    ingested = 0
    for f in files:
        try:
            await _ingest_transcript(
                org_id=org_id, user_id=user_id, access_token=access_token, file=f
            )
            ingested += 1
        except Exception as exc:
            log.warning(
                "meet_transcript_ingest_failed file=%s err=%s", f.get("id"), exc
            )

    metadata["meet_transcripts_last_synced_at"] = datetime.now(UTC).isoformat()
    await asyncio.to_thread(
        lambda: svc.table("integrations")
        .update({"metadata": metadata})
        .eq("id", row.data["id"])
        .execute()
    )
    return {"status": "ok", "ingested": ingested}


async def _list_transcript_files(
    *, access_token: str, folder_id: str, modified_since: str
) -> list[dict[str, Any]]:
    """Single page of Drive v3 files.list, scoped to one connected folder and
    filtered to Google Docs whose name matches Meet's transcript naming
    convention ("... - Transcript"). Capped at 50/cycle so a busy folder
    doesn't blow the polling window."""
    q = (
        f"'{folder_id}' in parents and mimeType = '{_TRANSCRIPT_MIME}' "
        f"and name contains 'Transcript' and trashed = false "
        f"and modifiedTime > '{modified_since}'"
    )
    params = {
        "q": q,
        "fields": "files(id,name,mimeType,modifiedTime)",
        "pageSize": "50",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
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


# ── Folder management (called by the integrations router) ──────────────────


async def add_folder(
    *, org_id: str, user_id: str, folder_id: str, folder_name: str | None
) -> list[str]:
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("integrations")
        .select("id, metadata")
        .eq("org_id", org_id)
        .eq("provider", _PROVIDER)
        .eq("scope_user_id", user_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        raise RuntimeError("Google Workspace is not connected for this user.")
    metadata = row.data.get("metadata") or {}
    folders = list(metadata.get(_FOLDER_IDS_KEY) or [])
    if folder_id not in folders:
        folders.append(folder_id)
    metadata[_FOLDER_IDS_KEY] = folders
    await asyncio.to_thread(
        lambda: svc.table("integrations")
        .update({"metadata": metadata})
        .eq("id", row.data["id"])
        .execute()
    )
    return folders


async def remove_folder(*, org_id: str, user_id: str, folder_id: str) -> list[str]:
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("integrations")
        .select("id, metadata")
        .eq("org_id", org_id)
        .eq("provider", _PROVIDER)
        .eq("scope_user_id", user_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        return []
    metadata = row.data.get("metadata") or {}
    folders = [f for f in (metadata.get(_FOLDER_IDS_KEY) or []) if f != folder_id]
    metadata[_FOLDER_IDS_KEY] = folders
    await asyncio.to_thread(
        lambda: svc.table("integrations")
        .update({"metadata": metadata})
        .eq("id", row.data["id"])
        .execute()
    )
    return folders


async def get_folder_names(
    *, org_id: str, user_id: str, folder_ids: list[str]
) -> dict[str, str]:
    """Resolve {folder_id: friendly_name} for already-configured folders.

    Drive doesn't support batch `files.list` by ID, so we issue one `files.get`
    per ID in parallel. Folders the token can no longer see (deleted, perms
    revoked) silently fall out — the UI shows the raw ID for those.
    """
    if not folder_ids:
        return {}
    access_token = await google_workspace.get_user_token(org_id=org_id, user_id=user_id)
    if not access_token:
        return {}

    async def _one(client: httpx.AsyncClient, fid: str) -> tuple[str, str | None]:
        try:
            resp = await client.get(
                f"https://www.googleapis.com/drive/v3/files/{fid}",
                params={"fields": "id,name", "supportsAllDrives": "true"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except Exception:
            return fid, None
        if resp.status_code != 200:
            return fid, None
        return fid, resp.json().get("name")

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        results = await asyncio.gather(*[_one(client, fid) for fid in folder_ids])
    return {fid: name for fid, name in results if name}


async def _ingest_transcript(
    *, org_id: str, user_id: str, access_token: str, file: dict[str, Any]
) -> None:
    file_id = file["id"]
    name = file.get("name") or f"Meet transcript {file_id}"

    text = await _export_text(access_token, file_id)
    if not text:
        log.warning("meet_transcript_empty_export file=%s", file_id)
        return

    doc_id = await upsert_external_document(
        org_id=org_id,
        source=SOURCE_TAG,
        external_id=file_id,
        name=name,
        file_type="txt",
        user_id=user_id,
    )

    import inngest

    from app.inngest.client import get_inngest_client
    client = get_inngest_client()
    await client.send(
        inngest.Event(
            name="doc/process-text",
            data={"doc_id": doc_id, "org_id": org_id, "text": text},
            id=f"meet-transcript-{doc_id}-{file.get('modifiedTime') or ''}",
        )
    )


async def _export_text(access_token: str, file_id: str) -> str:
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.get(
            f"https://www.googleapis.com/drive/v3/files/{file_id}/export",
            params={"mimeType": "text/plain"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"export failed {file_id}: {resp.status_code}")
    return resp.text
