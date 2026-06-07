import asyncio
import logging
import uuid
from typing import Any

import inngest
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import verify_jwt
from app.config import get_settings
from app.database import get_service_client, get_user_client
from app.inngest import get_inngest_client

log = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
STORAGE_BUCKET = "document"

_MIME_TO_TYPE: dict[str, str] = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/plain": "txt",
    "text/markdown": "md",
}
_EXT_TO_TYPE: dict[str, str] = {"pdf": "pdf", "docx": "docx", "txt": "txt", "md": "md"}


def _resolve_file_type(filename: str, content_type: str) -> str:
    if content_type in _MIME_TO_TYPE:
        return _MIME_TO_TYPE[content_type]
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in _EXT_TO_TYPE:
        return _EXT_TO_TYPE[ext]
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Unsupported file type. Allowed types: PDF, DOCX, TXT, MD.",
    )


# ── Request models ─────────────────────────────────────────────────────────────

class UploadInitRequest(BaseModel):
    filename: str
    content_type: str
    file_size: int


class UploadCompleteRequest(BaseModel):
    doc_id: str


# ── Step 1: Init upload — validate, create DB row, return signed URL ───────────

@router.post("/upload/init", status_code=status.HTTP_201_CREATED)
async def init_upload(
    body: UploadInitRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id: str | None = current_user["org_id"]
    user_id: str = current_user["user_id"]

    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No organization found. Please sign out and sign back in.",
        )

    if body.file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Maximum size is {MAX_FILE_SIZE // 1024 // 1024} MB.",
        )

    file_type = _resolve_file_type(body.filename, body.content_type)

    doc_id = str(uuid.uuid4())
    safe_name = body.filename.replace("/", "_").replace("..", "_").strip()
    storage_path = f"orgs/{org_id}/docs/{doc_id}/{safe_name}"

    settings = get_settings()
    svc = get_service_client()

    # Generate a Supabase Storage signed upload URL — the browser PUTs to this directly
    try:
        result = await asyncio.to_thread(
            lambda: svc.storage.from_(STORAGE_BUCKET).create_signed_upload_url(storage_path)
        )
    except Exception as exc:
        print(f"[documents] create_signed_upload_url failed ({type(exc).__name__}): {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create upload URL: {exc}",
        ) from exc

    # storage3 returns {"signed_url": "...", "token": "...", "path": "..."}.
    # Older versions used "signedURL". Some return a wrapper with `.data`.
    if isinstance(result, dict):
        payload = result
    else:
        payload = getattr(result, "data", None) or {}

    raw_url: str = payload.get("signed_url") or payload.get("signedURL") or ""

    if not raw_url:
        print(f"[documents] unexpected signed-url response shape: {result!r}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Storage returned an empty signed URL.",
        )

    upload_url = raw_url if raw_url.startswith("http") else f"{settings.supabase_url}{raw_url}"

    # Insert pending document row before the browser starts uploading
    try:
        await asyncio.to_thread(
            lambda: svc.table("documents")
            .insert({
                "id": doc_id,
                "org_id": org_id,
                "name": safe_name,
                "file_path": storage_path,
                "file_type": file_type,
                "file_size_bytes": body.file_size,
                "status": "pending",
                "created_by": user_id,
            })
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to record the document. Please try again.",
        ) from exc

    return {"doc_id": doc_id, "upload_url": upload_url}


# ── Step 2 (browser → Supabase Storage directly via signed URL) ───────────────
# Nothing happens server-side during the actual file upload.


# ── Step 3: Complete — trigger async processing ───────────────────────────────

@router.post("/upload/complete")
async def complete_upload(
    body: UploadCompleteRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id: str | None = current_user["org_id"]
    if not org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No organization found.")

    svc = get_service_client()

    # Verify ownership and fetch the storage path + type for the Inngest event
    result = await asyncio.to_thread(
        lambda: svc.table("documents")
        .select("file_path, file_type")
        .eq("id", body.doc_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    await _trigger_processing(
        body.doc_id, org_id, result.data["file_path"], result.data["file_type"]
    )

    return {"doc_id": body.doc_id, "status": "pending"}


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("")
async def list_documents(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id: str | None = current_user["org_id"]
    if not org_id:
        return {"documents": []}

    client = get_user_client(current_user["token"])
    result = await asyncio.to_thread(
        lambda: client.table("documents")
        .select("id, name, file_type, file_size_bytes, status, chunk_count, created_at")
        .order("created_at", desc=True)
        .execute()
    )
    return {"documents": result.data or []}


# ── Reprocess ─────────────────────────────────────────────────────────────────

@router.post("/{doc_id}/reprocess", status_code=status.HTTP_202_ACCEPTED)
async def reprocess_document(
    doc_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Re-run the ingestion pipeline on an already-uploaded document.

    Use for: failed documents, chunker/embedder config changes, or pipeline bugs.
    The pipeline is idempotent — prior chunks/embeddings are replaced.
    """
    org_id: str | None = current_user["org_id"]
    if not org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No organization found.")

    svc = get_service_client()
    result = await asyncio.to_thread(
        lambda: svc.table("documents")
        .select("file_path, file_type, status")
        .eq("id", doc_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    await asyncio.to_thread(
        lambda: svc.table("documents")
        .update({"status": "pending", "chunk_count": None})
        .eq("id", doc_id)
        .execute()
    )

    await _trigger_processing(
        doc_id, org_id, result.data["file_path"], result.data["file_type"]
    )
    return {"doc_id": doc_id, "status": "pending"}


# ── Delete ────────────────────────────────────────────────────────────────────

@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    doc_id: str,
    current_user: dict = Depends(verify_jwt),
) -> None:
    org_id: str | None = current_user["org_id"]
    if not org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No organization found.")

    svc = get_service_client()

    result = await asyncio.to_thread(
        lambda: svc.table("documents")
        .select("file_path")
        .eq("id", doc_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    file_path: str = result.data["file_path"]

    await asyncio.to_thread(
        lambda: svc.table("documents").delete().eq("id", doc_id).execute()
    )

    try:
        await asyncio.to_thread(
            lambda: svc.storage.from_(STORAGE_BUCKET).remove([file_path])
        )
    except Exception:
        pass


# ── Inngest trigger ───────────────────────────────────────────────────────────

async def _trigger_processing(doc_id: str, org_id: str, file_path: str, file_type: str) -> None:
    """Emit the doc/uploaded event. Inngest's process-document function picks it up.

    Uses idempotency_key on the event so a duplicate user click doesn't kick off
    a second pipeline run (Inngest deduplicates within a 24h window).
    """
    client = get_inngest_client()
    try:
        await client.send(
            inngest.Event(
                name="doc/uploaded",
                data={
                    "doc_id": doc_id,
                    "org_id": org_id,
                    "file_path": file_path,
                    "file_type": file_type,
                },
                id=f"doc-uploaded-{doc_id}",
            )
        )
    except Exception as exc:
        log.error("Failed to send doc/uploaded event for %s: %s", doc_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to queue document for processing. Please try again.",
        ) from exc
