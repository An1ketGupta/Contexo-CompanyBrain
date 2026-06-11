"""Document versioning — V4 #68.

Upload a new revision of an existing document:
  POST   /documents/{id}/versions/init      — return signed upload URL + version row
  POST   /documents/{id}/versions/complete  — archive old chunks, mark version current, reprocess
  GET    /documents/{id}/versions           — version history

Why two-step like the original upload? Same reasons:
  * Browser PUTs the file straight to Supabase Storage (saves our bandwidth).
  * We don't kick off the embed pipeline until the file actually landed.

Chunk archival is a soft-delete: `chunks.is_archived = true` flips on, the
search RPCs (vector_search / fts_search, migration 021) skip archived rows.
We keep the old chunks because `chunk_citations` references them — a hard
delete would cascade-nuke the audit trail.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import inngest
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.config import get_settings
from app.database import get_service_client, get_user_client
from app.inngest import get_inngest_client

log = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents", "versions"])

STORAGE_BUCKET = "document"


class VersionInitRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)
    content_type: str = Field(..., min_length=1, max_length=120)
    file_size: int = Field(..., ge=1)


class VersionCompleteRequest(BaseModel):
    version_id: str


@router.get("/{doc_id}/versions")
async def list_versions(
    doc_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Version history for one document. RLS already restricts to org docs."""
    _require_uuid(doc_id)
    org_id = current_user.get("org_id")
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No organization found.",
        )

    client = get_user_client(current_user["token"])
    res = await asyncio.to_thread(
        lambda: client.table("document_versions")
        .select(
            "id, document_id, version_number, file_path, file_size_bytes, "
            "uploaded_by, is_current, created_at"
        )
        .eq("document_id", doc_id)
        .order("version_number", desc=True)
        .execute()
    )
    versions = res.data or []

    # Hydrate uploader display names via the service client (cross-user reads
    # would otherwise be blocked by the `users` RLS).
    user_ids = list({v["uploaded_by"] for v in versions if v.get("uploaded_by")})
    names_by_id: dict[str, str] = {}
    if user_ids:
        svc = get_service_client()
        u_res = await asyncio.to_thread(
            lambda: svc.table("users")
            .select("id, display_name, email")
            .in_("id", user_ids)
            .execute()
        )
        for u in (u_res.data or []):
            name = u.get("display_name") or (u.get("email") or "").split("@")[0]
            names_by_id[u["id"]] = name or "Someone"

    return {
        "versions": [
            {
                **v,
                "uploaded_by_name": names_by_id.get(v.get("uploaded_by") or "", None),
            }
            for v in versions
        ]
    }


@router.post(
    "/{doc_id}/versions/init",
    status_code=status.HTTP_201_CREATED,
)
async def init_version_upload(
    doc_id: str,
    body: VersionInitRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Mint a signed upload URL for a new version of an existing document.

    Admin-only. We resolve the next version_number atomically by reading the
    current max — concurrent uploads on the same doc would race, but the
    UNIQUE(document_id, version_number) constraint in the DB is the safety
    net; the loser of the race gets a 409 and can retry.
    """
    _require_uuid(doc_id)
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    if not org_id or not user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No organization found.",
        )

    settings = get_settings()
    svc = get_service_client()

    await _require_admin(current_user)

    # Verify ownership AND fetch the canonical file_type — we never let the
    # caller change the type mid-version (a PDF→DOCX swap would be a wholly
    # different document semantically).
    doc_res = await asyncio.to_thread(
        lambda: svc.table("documents")
        .select("id, file_type")
        .eq("id", doc_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not doc_res or not doc_res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    file_type = doc_res.data["file_type"]

    # Next version number.
    latest_res = await asyncio.to_thread(
        lambda: svc.table("document_versions")
        .select("version_number")
        .eq("document_id", doc_id)
        .order("version_number", desc=True)
        .limit(1)
        .execute()
    )
    rows = latest_res.data or []
    next_version = (int(rows[0]["version_number"]) if rows else 0) + 1
    # If no version rows exist at all, the current file IS version 1 (legacy
    # pre-V4 docs). Backfill that row so the history shows it, then ship the
    # NEW upload as v2.
    if not rows:
        try:
            legacy_path_res = await asyncio.to_thread(
                lambda: svc.table("documents")
                .select("file_path, file_size_bytes, created_at, created_by")
                .eq("id", doc_id)
                .maybe_single()
                .execute()
            )
            legacy = (legacy_path_res and legacy_path_res.data) or {}
            await asyncio.to_thread(
                lambda: svc.table("document_versions")
                .insert({
                    "document_id": doc_id,
                    "version_number": 1,
                    "file_path": legacy.get("file_path"),
                    "file_size_bytes": legacy.get("file_size_bytes"),
                    "uploaded_by": legacy.get("created_by"),
                    "is_current": True,
                    "created_at": legacy.get("created_at"),
                })
                .execute()
            )
            next_version = 2
        except Exception as exc:
            log.warning("legacy_version_backfill_failed doc=%s err=%s", doc_id, exc)

    version_id = str(uuid.uuid4())
    safe_name = body.filename.replace("/", "_").replace("..", "_").strip()
    storage_path = f"orgs/{org_id}/docs/{doc_id}/v{next_version}/{safe_name}"

    try:
        upload_res = await asyncio.to_thread(
            lambda: svc.storage.from_(STORAGE_BUCKET).create_signed_upload_url(storage_path)
        )
    except Exception as exc:
        log.exception("version signed-url failed doc=%s err=%s", doc_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Storage failed. Try again.",
        ) from exc

    payload = upload_res if isinstance(upload_res, dict) else (getattr(upload_res, "data", None) or {})
    raw_url: str = payload.get("signed_url") or payload.get("signedURL") or ""
    if not raw_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Storage returned an empty signed URL.",
        )
    upload_url = raw_url if raw_url.startswith("http") else f"{settings.supabase_url}{raw_url}"

    # Insert pending version row. is_current stays false until /complete.
    try:
        await asyncio.to_thread(
            lambda: svc.table("document_versions")
            .insert({
                "id": version_id,
                "document_id": doc_id,
                "version_number": next_version,
                "file_path": storage_path,
                "file_size_bytes": body.file_size,
                "uploaded_by": user_id,
                "is_current": False,
            })
            .execute()
        )
    except Exception as exc:
        log.warning("version row insert failed doc=%s err=%s", doc_id, exc)
        # 409 on the UNIQUE(document_id, version_number) clash so the client
        # knows to retry (the user uploaded twice in a row).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another version is being uploaded. Retry in a moment.",
        ) from exc

    return {
        "version_id": version_id,
        "version_number": next_version,
        "upload_url": upload_url,
        "storage_path": storage_path,
        "file_type": file_type,
    }


@router.post("/{doc_id}/versions/complete")
async def complete_version_upload(
    doc_id: str,
    body: VersionCompleteRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Archive the old chunks, mark the new version current, kick off reprocess.

    Admin-only. The reprocessing event reuses the existing `doc/uploaded`
    Inngest function — the document_id stays the same, only `file_path` and
    `version_id` change.
    """
    _require_uuid(doc_id)
    _require_uuid(body.version_id)
    org_id = current_user.get("org_id")
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No organization found.",
        )

    await _require_admin(current_user)

    svc = get_service_client()

    version_res = await asyncio.to_thread(
        lambda: svc.table("document_versions")
        .select("id, document_id, version_number, file_path")
        .eq("id", body.version_id)
        .eq("document_id", doc_id)
        .maybe_single()
        .execute()
    )
    if not version_res or not version_res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found.")
    version = version_res.data

    # Confirm parent doc belongs to this org.
    doc_res = await asyncio.to_thread(
        lambda: svc.table("documents")
        .select("id, file_type")
        .eq("id", doc_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not doc_res or not doc_res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    file_type = doc_res.data["file_type"]

    # Archive every existing chunk for this document. Idempotent: re-running
    # complete on the same version just no-ops (chunks already archived stay
    # archived; the new version's chunks insert fresh later).
    try:
        await asyncio.to_thread(
            lambda: svc.table("chunks")
            .update({"is_archived": True})
            .eq("document_id", doc_id)
            .eq("is_archived", False)
            .execute()
        )
    except Exception as exc:
        log.warning("archive_chunks_failed doc=%s err=%s", doc_id, exc)

    # Flip is_current. Two updates: clear, then set — the partial unique
    # index on (document_id) WHERE is_current=true makes the order matter.
    try:
        await asyncio.to_thread(
            lambda: svc.table("document_versions")
            .update({"is_current": False})
            .eq("document_id", doc_id)
            .eq("is_current", True)
            .execute()
        )
        await asyncio.to_thread(
            lambda: svc.table("document_versions")
            .update({"is_current": True})
            .eq("id", version["id"])
            .execute()
        )
    except Exception as exc:
        log.exception("mark_current_failed doc=%s err=%s", doc_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Couldn't mark the new version current.",
        ) from exc

    # Reset the parent doc to processing and point file_path at the new file.
    try:
        await asyncio.to_thread(
            lambda: svc.table("documents")
            .update({
                "status": "processing",
                "file_path": version["file_path"],
            })
            .eq("id", doc_id)
            .execute()
        )
    except Exception as exc:
        log.exception("doc_reset_failed doc=%s err=%s", doc_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Couldn't queue the new version for processing.",
        ) from exc

    # Kick off the existing process-document pipeline. Idempotency key uses
    # the version id so a duplicate /complete call doesn't re-queue.
    client = get_inngest_client()
    try:
        await client.send(
            inngest.Event(
                name="doc/uploaded",
                data={
                    "doc_id": doc_id,
                    "org_id": org_id,
                    "file_path": version["file_path"],
                    "file_type": file_type,
                    "version_id": version["id"],
                },
                id=f"doc-uploaded-{version['id']}",
            )
        )
    except Exception as exc:
        log.exception("version_inngest_send_failed doc=%s err=%s", doc_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Couldn't queue reprocessing. Try again.",
        ) from exc

    return {
        "version_number": int(version["version_number"]),
        "status": "processing",
    }


# ── Helpers ─────────────────────────────────────────────────────────────────


def _require_uuid(value: str) -> None:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid id."
        ) from exc


async def _require_admin(current_user: dict) -> None:
    """Same pattern as routers/admin.py. Kept local to avoid a circular import."""
    user_client = get_user_client(current_user["token"])
    me = await asyncio.to_thread(
        lambda: user_client.table("users")
        .select("role")
        .eq("id", current_user["user_id"])
        .maybe_single()
        .execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can upload new versions.",
        )
