import asyncio
import logging
import re
import uuid
from typing import Any, Literal

import inngest
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from app.auth import verify_jwt
from app.config import get_settings
from app.database import get_service_client, get_user_client
from app.inngest import get_inngest_client
from app.services.documents_cache import (
    get_cached_document_list,
    invalidate_document_caches,
    set_cached_document_list,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
STORAGE_BUCKET = "document"

# Bulk operations need a ceiling — protects against a runaway loop accidentally
# wiping an entire org with one request, and keeps the DELETE round-trip
# small enough that we can include every storage path in a single Storage call.
BULK_MAX_IDS = 200

# Tag normalisation: lowercase, trim, max 32 chars, no commas (commas are the
# CSV-style hint we use in the filter UI). Enforced server-side so the DB
# stays clean regardless of the client.
_TAG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9\-_ .]{0,31}$")
TAG_MAX_PER_DOC = 20
TAG_BULK_MAX = 10  # tags appended in a single bulk request

DocumentSortField = Literal["created_at", "name", "file_size_bytes"]
DocumentSortDir = Literal["asc", "desc"]
DocumentStatus = Literal["pending", "processing", "ready", "failed"]
DocumentFileType = Literal["pdf", "docx", "txt", "md", "xlsx", "pptx", "html", "csv"]


def _normalise_tag(raw: str) -> str | None:
    cleaned = raw.strip().lower()
    if not cleaned or not _TAG_PATTERN.match(cleaned):
        return None
    return cleaned


def _normalise_tags(raw: list[str], *, cap: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for r in raw:
        norm = _normalise_tag(r)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
        if len(out) >= cap:
            break
    return out

_MIME_TO_TYPE: dict[str, str] = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "text/plain": "txt",
    "text/markdown": "md",
    "text/html": "html",
    "application/xhtml+xml": "html",
    "text/csv": "csv",
    # Day 13 — meeting transcripts. Zoom serves VTT as text/vtt, some MTAs as
    # application/octet-stream so we still fall back to the extension below.
    "text/vtt": "vtt",
}
_EXT_TO_TYPE: dict[str, str] = {
    "pdf": "pdf",
    "docx": "docx",
    "txt": "txt",
    "md": "md",
    "markdown": "md",
    "xlsx": "xlsx",
    "pptx": "pptx",
    "html": "html",
    "htm": "html",
    "csv": "csv",
    # Day 13: meeting-transcript extensions. The classifier upstream uses
    # these to route into the MeetingNotesAgent pipeline.
    "vtt": "vtt",
}


def _resolve_file_type(filename: str, content_type: str) -> str:
    # Prefer the file extension when present — content_type from a browser PUT
    # is often "application/octet-stream" or just wrong, but the user's chosen
    # extension is usually trustworthy and disambiguates Office formats that
    # share root MIME prefixes.
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in _EXT_TO_TYPE:
        return _EXT_TO_TYPE[ext]
    if content_type in _MIME_TO_TYPE:
        return _MIME_TO_TYPE[content_type]
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Unsupported file type. Allowed: PDF, DOCX, XLSX, PPTX, TXT, MD, HTML, CSV.",
    )


# ── Request models ─────────────────────────────────────────────────────────────

class UploadInitRequest(BaseModel):
    filename: str
    content_type: str
    file_size: int


class UploadCompleteRequest(BaseModel):
    doc_id: str


class UploadRefreshRequest(BaseModel):
    doc_id: str


# V4 Day 3 #32 — Chrome Extension "Add to Brain". The browser scrapes the
# page with Readability + DOMPurify and hands us clean text; this endpoint
# is the only entry point for non-file sources.
class DocumentFromUrlRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048)
    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1, max_length=200_000)
    hostname: str | None = Field(default=None, max_length=255)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^https?://", v, re.IGNORECASE):
            raise ValueError("url must be http(s)")
        return v


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


# ── Retry path: re-mint signed URL for an existing failed/pending row ─────────
# Frontend calls this when the user clicks "Retry failed" on an upload that
# never reached `ready`. We reuse the existing doc row + storage path so the
# documents grid doesn't accumulate duplicate rows for the same file.

@router.post("/upload/refresh")
async def refresh_upload(
    body: UploadRefreshRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id: str | None = current_user["org_id"]
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No organization found.",
        )

    settings = get_settings()
    svc = get_service_client()

    existing = await asyncio.to_thread(
        lambda: svc.table("documents")
        .select("file_path, status")
        .eq("id", body.doc_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not existing or not existing.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    current_status = existing.data.get("status")
    # Only retry rows that never finished. `processing` means Inngest already
    # owns the row — use the reprocess endpoint for that. `ready` is a no-op.
    if current_status not in ("pending", "failed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot retry upload in status '{current_status}'.",
        )

    storage_path: str = existing.data["file_path"]

    # Best-effort cleanup of any partial blob from the prior attempt — Supabase
    # signed-upload tokens won't overwrite an existing object. Ignore failures:
    # if the object never landed, remove() returns an empty list, not an error.
    try:
        await asyncio.to_thread(
            lambda: svc.storage.from_(STORAGE_BUCKET).remove([storage_path])
        )
    except Exception as exc:
        log.info("[documents] storage remove during refresh failed (non-fatal): %s", exc)

    try:
        result = await asyncio.to_thread(
            lambda: svc.storage.from_(STORAGE_BUCKET).create_signed_upload_url(storage_path)
        )
    except Exception as exc:
        log.error("[documents] create_signed_upload_url (refresh) failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create upload URL: {exc}",
        ) from exc

    if isinstance(result, dict):
        payload = result
    else:
        payload = getattr(result, "data", None) or {}

    raw_url: str = payload.get("signed_url") or payload.get("signedURL") or ""
    if not raw_url:
        log.error("[documents] unexpected signed-url response shape on refresh: %r", result)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Storage returned an empty signed URL.",
        )

    upload_url = raw_url if raw_url.startswith("http") else f"{settings.supabase_url}{raw_url}"

    # Reset row to pending so the documents grid reflects the retry attempt.
    try:
        await asyncio.to_thread(
            lambda: svc.table("documents")
            .update({"status": "pending"})
            .eq("id", body.doc_id)
            .eq("org_id", org_id)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reset document status.",
        ) from exc

    return {"doc_id": body.doc_id, "upload_url": upload_url}


# ── Step 2 (browser → Supabase Storage directly via signed URL) ───────────────
# Nothing happens server-side during the actual file upload.


# ── Step 3: Complete — trigger async processing ───────────────────────────────

@router.post("/upload/complete")
async def complete_upload(
    body: UploadCompleteRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id: str | None = current_user["org_id"]
    user_id: str | None = current_user.get("user_id")
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

    initial_version = await _ensure_initial_version_row(
        doc_id=body.doc_id,
        org_id=org_id,
        user_id=user_id,
        client=svc,
    )

    await _trigger_processing(
        body.doc_id,
        org_id,
        result.data["file_path"],
        result.data["file_type"],
        version_id=initial_version["id"] if initial_version else None,
    )

    # V3 #80 — the doc row now exists in 'pending'; an org refreshing the
    # documents page should see it immediately. Status flips later (→ ready
    # / failed) also invalidate from the Inngest pipeline.
    await invalidate_document_caches(org_id)

    # V3 #50 — auto-match uploaded doc against the org's recommendation list.
    # Best-effort: never block the upload response on this. The fuzzy match
    # is pure Python (no LLM, no extra IO) so this is cheap.
    try:
        await _maybe_auto_match_recommendation(
            doc_id=body.doc_id, org_id=org_id
        )
    except Exception as exc:
        log.warning("auto_match_recommendation_failed: %s", exc)

    # V4 #18 / #57: telemetry + team activity. We log on `upload/complete`
    # rather than `upload/init` so half-finished uploads don't pollute the
    # feed. file_size lookup is cheap because the row just landed in cache.
    try:
        from app.services.analytics import track_event
        from app.services.activity import log_activity, resolve_user_privacy

        meta_res = await asyncio.to_thread(
            lambda: svc.table("documents")
            .select("name, file_type, file_size_bytes")
            .eq("id", body.doc_id)
            .maybe_single()
            .execute()
        )
        meta = (meta_res and meta_res.data) or {}
        size_kb = (meta.get("file_size_bytes") or 0) // 1024
        user_id = current_user.get("user_id")
        await track_event(
            org_id=org_id,
            user_id=user_id,
            event_type="doc_uploaded",
            metadata={"file_type": meta.get("file_type"), "file_size_kb": size_kb},
        )
        if user_id:
            is_private = await resolve_user_privacy(user_id)
            await log_activity(
                org_id=org_id,
                user_id=user_id,
                activity_type="uploaded_doc",
                metadata={"doc_name": meta.get("name") or "a document"},
                is_private=is_private,
            )
    except Exception:
        pass

    return {"doc_id": body.doc_id, "status": "pending"}


# ── Chrome Extension: ingest a webpage by URL (V4 Day 3 / #32) ────────────────

# Pre-extracted text path: the browser ran Readability on the user's current
# tab and POSTs the cleaned content here. Skips file Storage entirely (no blob
# exists) and queues the Inngest text-mode pipeline.
@router.post("/from-url", status_code=status.HTTP_201_CREATED)
async def create_document_from_url(
    body: DocumentFromUrlRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id: str | None = current_user["org_id"]
    user_id: str = current_user["user_id"]

    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No organization found. Please sign out and sign back in.",
        )

    svc = get_service_client()

    # Dedupe by (org_id, source_url). The unique index in 023 would error
    # the insert otherwise; doing the lookup first lets us short-circuit with
    # a friendly "already added" instead of a 409.
    existing = await asyncio.to_thread(
        lambda: svc.table("documents")
        .select("id, status")
        .eq("org_id", org_id)
        .eq("source_url", body.url)
        .maybe_single()
        .execute()
    )
    if existing and existing.data:
        return {
            "doc_id": existing.data["id"],
            "status": existing.data.get("status") or "pending",
            "already_existed": True,
        }

    doc_id = str(uuid.uuid4())
    # Trim title aggressively — many pages have ridiculously long titles
    # ("My Notion — Page A — Notion — workspace.notion.so"). Hostname fallback
    # keeps the documents grid readable when title is empty.
    safe_title = (body.title or "").strip()[:255] or (body.hostname or body.url)[:255]
    content_size_bytes = len(body.content.encode("utf-8"))

    try:
        await asyncio.to_thread(
            lambda: svc.table("documents")
            .insert({
                "id": doc_id,
                "org_id": org_id,
                "name": safe_title,
                "file_path": None,
                "file_type": "webpage",
                "file_size_bytes": content_size_bytes,
                "status": "pending",
                "created_by": user_id,
                "source_url": body.url,
                "content": body.content,
                "metadata": {
                    "hostname": body.hostname,
                    "source": "chrome-extension",
                },
            })
            .execute()
        )
    except Exception as exc:
        log.error("[documents/from-url] insert failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to record the page. Please try again.",
        ) from exc

    await _trigger_text_processing(
        doc_id=doc_id,
        org_id=org_id,
        text=body.content,
        title=safe_title,
        source_url=body.url,
    )

    await invalidate_document_caches(org_id)

    try:
        await _maybe_auto_match_recommendation(doc_id=doc_id, org_id=org_id)
    except Exception as exc:
        log.warning("auto_match_recommendation_failed_from_url: %s", exc)

    # Mirror the doc/uploaded telemetry + team activity from `complete_upload`
    # so the activity feed and analytics show extension-sourced docs alongside
    # uploaded ones.
    try:
        from app.services.analytics import track_event
        from app.services.activity import log_activity, resolve_user_privacy

        await track_event(
            org_id=org_id,
            user_id=user_id,
            event_type="doc_uploaded",
            metadata={
                "file_type": "webpage",
                "file_size_kb": content_size_bytes // 1024,
                "source": "chrome-extension",
            },
        )
        is_private = await resolve_user_privacy(user_id)
        await log_activity(
            org_id=org_id,
            user_id=user_id,
            activity_type="uploaded_doc",
            metadata={"doc_name": safe_title, "source": "chrome-extension"},
            is_private=is_private,
        )
    except Exception:
        pass

    return {
        "doc_id": doc_id,
        "status": "pending",
        "already_existed": False,
        "name": safe_title,
    }


# ── List with filters, sort, pagination ───────────────────────────────────────

_SORTABLE_FIELDS: set[str] = {"created_at", "name", "file_size_bytes"}


@router.get("")
async def list_documents(
    status_filter: DocumentStatus | None = Query(None, alias="status"),
    file_type: DocumentFileType | None = Query(None),
    tag: list[str] | None = Query(None, description="Repeat to AND-filter multiple tags."),
    search: str | None = Query(None, max_length=120, description="Case-insensitive substring match on name."),
    sort_by: DocumentSortField = Query("created_at"),
    sort_dir: DocumentSortDir = Query("desc"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Filter, sort, and paginate the caller's documents.

    Tag filter uses the PostgREST `cs.{a,b}` (array-contains) operator so all
    supplied tags must be present on the document. Search runs as ILIKE on
    `name` — small lookup, GIN-on-trgm not worth it at our scale.
    """
    org_id: str | None = current_user["org_id"]
    if not org_id:
        return {"documents": [], "total": 0, "limit": limit, "offset": offset}

    if sort_by not in _SORTABLE_FIELDS:
        raise HTTPException(status_code=400, detail="Invalid sort field.")

    # V3 #80 — cache key folds in every filter so distinct requests hash
    # differently. Tags are sorted lower-cased so {tag=[a,b]} and {tag=[B,A]}
    # hit the same key. Search string normalised the same way.
    cache_signature: tuple[Any, ...] = (
        status_filter,
        file_type,
        tuple(sorted({(t or "").strip().lower() for t in (tag or []) if t})),
        (search or "").strip().lower(),
        sort_by,
        sort_dir,
        limit,
        offset,
    )
    cached = await get_cached_document_list(
        org_id=org_id, filter_signature=cache_signature
    )
    if cached is not None:
        return cached

    client = get_user_client(current_user["token"])

    def _run() -> Any:
        q = client.table("documents").select(
            "id, name, file_type, file_size_bytes, status, chunk_count, tags, metadata, created_at, health_score, health_label, last_accessed_at, review_frequency_days, review_due_at, last_reviewed_at, current_version_id",
            count="exact",
        )
        if status_filter:
            q = q.eq("status", status_filter)
        if file_type:
            q = q.eq("file_type", file_type)
        if tag:
            cleaned = _normalise_tags(tag, cap=TAG_BULK_MAX)
            if cleaned:
                # `cs` on a TEXT[] column → array contains. We pass the literal
                # PG array form; supabase-py forwards it verbatim.
                q = q.contains("tags", cleaned)
        if search:
            # The user typed a substring; we escape PostgREST's special chars
            # so a user search for "a,b" doesn't break the query.
            safe = search.replace("%", r"\%").replace(",", " ")
            q = q.ilike("name", f"%{safe}%")

        q = q.order(sort_by, desc=(sort_dir == "desc"))
        # Stable tie-break — without this, equal-named rows can shuffle on every reload.
        if sort_by != "created_at":
            q = q.order("created_at", desc=True)
        q = q.range(offset, offset + limit - 1)
        return q.execute()

    result = await asyncio.to_thread(_run)
    documents = await _attach_version_summaries(
        client=client,
        documents=list(result.data or []),
    )
    payload = {
        "documents": documents,
        "total": result.count or 0,
        "limit": limit,
        "offset": offset,
    }
    await set_cached_document_list(
        org_id=org_id, filter_signature=cache_signature, payload=payload
    )
    return payload


@router.get("/tags")
async def list_tags(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Return all distinct tags used by the caller's org, with usage counts.

    Powers the tag picker / autocomplete in the bulk-tag dialog and the
    filter dropdown. Uses the service client + an explicit org_id filter
    because PostgREST doesn't expose `unnest()` over the user JWT path.
    """
    org_id: str | None = current_user["org_id"]
    if not org_id:
        return {"tags": []}

    svc = get_service_client()

    def _run() -> list[dict[str, Any]]:
        # supabase-py doesn't model RPCs over array-unnest, so we fall back to
        # selecting the tag arrays and aggregating in Python. The dataset is
        # tiny (one row per doc, ≤ a few hundred docs in the target range).
        res = (
            svc.table("documents")
            .select("tags")
            .eq("org_id", org_id)
            .execute()
        )
        counts: dict[str, int] = {}
        for row in res.data or []:
            for t in row.get("tags") or []:
                counts[t] = counts.get(t, 0) + 1
        return sorted(
            ({"tag": k, "count": v} for k, v in counts.items()),
            key=lambda r: (-r["count"], r["tag"]),
        )

    tags = await asyncio.to_thread(_run)
    return {"tags": tags}


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

    # If we can re-embed just the failed chunks (partial state on a 'ready' doc),
    # do that — much cheaper than re-parsing & re-chunking the whole file, and
    # it preserves the chunks that already embedded successfully.
    failed = await asyncio.to_thread(
        lambda: svc.table("chunks")
        .select("id", count="exact")
        .eq("document_id", doc_id)
        .eq("embedding_status", "failed")
        .limit(1)
        .execute()
    )
    if (failed.count or 0) > 0 and result.data["status"] != "failed":
        await _trigger_chunk_retry(doc_id, org_id)
        return {"doc_id": doc_id, "status": "processing", "mode": "retry-chunks"}

    await asyncio.to_thread(
        lambda: svc.table("documents")
        .update({"status": "pending", "chunk_count": None})
        .eq("id", doc_id)
        .execute()
    )

    import time
    await _trigger_processing(
        doc_id,
        org_id,
        result.data["file_path"],
        result.data["file_type"],
        attempt_key=str(int(time.time() // 60)),
    )
    return {"doc_id": doc_id, "status": "pending", "mode": "full"}


# ── Signed download URL (for citation "Open document" links) ────────────────

@router.get("/{doc_id}/signed-url")
async def get_signed_url(
    doc_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Return a short-lived signed Storage URL for in-browser viewing.

    RLS is enforced via the user-scoped Supabase client: the SELECT below only
    returns the row if the caller's org owns the doc. We then mint a URL with
    the service-role client because Storage signing needs that key, but we've
    already authorized the caller through the RLS check.
    """
    org_id: str | None = current_user["org_id"]
    if not org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No organization found.")

    user_client = get_user_client(current_user["token"])
    owned = await asyncio.to_thread(
        lambda: user_client.table("documents")
        .select("file_path, name")
        .eq("id", doc_id)
        .maybe_single()
        .execute()
    )

    if not owned.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    svc = get_service_client()
    try:
        result = await asyncio.to_thread(
            lambda: svc.storage.from_(STORAGE_BUCKET).create_signed_url(
                owned.data["file_path"], 600
            )
        )
    except Exception as exc:
        log.error("Failed to mint signed URL for %s: %s", doc_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not generate document link.",
        ) from exc

    if isinstance(result, dict):
        payload = result
    else:
        payload = getattr(result, "data", None) or {}

    url = payload.get("signedURL") or payload.get("signed_url") or payload.get("signedUrl")
    if not url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Storage returned an empty signed URL.",
        )

    settings = get_settings()
    if not url.startswith("http"):
        url = f"{settings.supabase_url}{url}"

    return {"url": url, "name": owned.data["name"], "expires_in": 600}


# ── Bulk operations ───────────────────────────────────────────────────────────


class BulkIdsBody(BaseModel):
    document_ids: list[str] = Field(..., min_length=1, max_length=BULK_MAX_IDS)

    @field_validator("document_ids")
    @classmethod
    def _validate_uuids(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in v:
            try:
                uid = str(uuid.UUID(raw))
            except (ValueError, AttributeError) as exc:
                raise ValueError(f"Invalid document id: {raw!r}") from exc
            if uid not in seen:
                seen.add(uid)
                out.append(uid)
        return out


class BulkTagBody(BulkIdsBody):
    tags: list[str] = Field(..., min_length=1, max_length=TAG_BULK_MAX)


@router.delete("/bulk")
async def bulk_delete_documents(
    body: BulkIdsBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Delete many documents in a single round-trip.

    Two-phase: select-then-delete. We need the storage paths for cleanup,
    and the SELECT (RLS-scoped via the user client) doubles as the ownership
    check — any id not visible to the caller is silently dropped from the
    delete set, never leaks through to the service-role delete.
    """
    org_id: str | None = current_user["org_id"]
    if not org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No organization found.")

    user_client = get_user_client(current_user["token"])
    rows = await asyncio.to_thread(
        lambda: user_client.table("documents")
        .select("id, file_path")
        .in_("id", body.document_ids)
        .execute()
    )

    owned: list[dict[str, str]] = rows.data or []
    if not owned:
        return {"deleted": 0, "skipped": len(body.document_ids)}

    owned_ids = [r["id"] for r in owned]
    paths = [r["file_path"] for r in owned if r.get("file_path")]

    svc = get_service_client()
    # Delete DB rows first — that's the user-visible state. Storage cleanup is
    # best-effort; an orphan blob is recoverable, an orphan DB row would haunt
    # the user.
    await asyncio.to_thread(
        lambda: svc.table("documents")
        .delete()
        .in_("id", owned_ids)
        .eq("org_id", org_id)
        .execute()
    )

    if paths:
        try:
            await asyncio.to_thread(
                lambda: svc.storage.from_(STORAGE_BUCKET).remove(paths)
            )
        except Exception as exc:
            log.warning("bulk storage cleanup failed for %d paths: %s", len(paths), exc)

    await invalidate_document_caches(org_id)

    return {
        "deleted": len(owned_ids),
        "skipped": len(body.document_ids) - len(owned_ids),
    }


@router.patch("/bulk/tags")
async def bulk_add_tags(
    body: BulkTagBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Append tags to a set of documents.

    Semantics:
      * Tags are normalised + deduped per the API rules.
      * Existing tags on each row are preserved.
      * Per-doc cap is enforced (TAG_MAX_PER_DOC); overflow tags drop, not error.
      * Returns the count of docs actually updated.
    """
    org_id: str | None = current_user["org_id"]
    if not org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No organization found.")

    new_tags = _normalise_tags(body.tags, cap=TAG_BULK_MAX)
    if not new_tags:
        raise HTTPException(
            status_code=400,
            detail="No valid tags supplied. Use 1-32 chars, lowercase letters / digits / -_. only.",
        )

    user_client = get_user_client(current_user["token"])
    rows = await asyncio.to_thread(
        lambda: user_client.table("documents")
        .select("id, tags")
        .in_("id", body.document_ids)
        .execute()
    )

    owned: list[dict[str, Any]] = rows.data or []
    if not owned:
        return {"updated": 0, "skipped": len(body.document_ids)}

    svc = get_service_client()
    updated = 0

    # Per-row update so we can apply the per-doc cap without truncating
    # other rows' existing tags. The set sizes here are small (≤ a couple
    # hundred rows max per bulk call) so the round-trips are fine.
    async def _update_row(row: dict[str, Any]) -> bool:
        current: list[str] = list(row.get("tags") or [])
        seen = set(current)
        merged = list(current)
        for t in new_tags:
            if t in seen:
                continue
            if len(merged) >= TAG_MAX_PER_DOC:
                break
            merged.append(t)
            seen.add(t)
        if merged == current:
            return False
        await asyncio.to_thread(
            lambda: svc.table("documents")
            .update({"tags": merged})
            .eq("id", row["id"])
            .eq("org_id", org_id)
            .execute()
        )
        return True

    results = await asyncio.gather(
        *[_update_row(r) for r in owned], return_exceptions=True
    )
    for r in results:
        if isinstance(r, BaseException):
            log.warning("bulk tag update failed for one row: %s", r)
        elif r:
            updated += 1

    if updated:
        await invalidate_document_caches(org_id)

    return {
        "updated": updated,
        "skipped": len(body.document_ids) - len(owned),
        "tags_applied": new_tags,
    }


class TagsPatchBody(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=TAG_MAX_PER_DOC)


class ReviewFrequencyBody(BaseModel):
    # None clears the cadence; integer values are clamped to the 7-730 range
    # the DB CHECK constraint expects.
    review_frequency_days: int | None = Field(default=None, ge=7, le=730)


@router.patch("/{doc_id}/review")
async def update_document_review(
    doc_id: str,
    body: ReviewFrequencyBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Set or clear the review cadence on a document (Day 13 / #38).

    When `review_frequency_days` is set, we precompute `review_due_at` from
    `last_reviewed_at` (or `created_at` for never-reviewed docs). This stays
    in sync with `mark_document_reviewed` — both paths set the same field
    rather than relying on a generated column the cron has to evaluate.
    """
    org_id: str | None = current_user["org_id"]
    if not org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No organization found.")
    try:
        uuid.UUID(doc_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid document id.")

    client = get_user_client(current_user["token"])

    if body.review_frequency_days is None:
        update = {
            "review_frequency_days": None,
            "review_due_at": None,
        }
    else:
        # Pull the baseline timestamp first — last_reviewed_at, falling back
        # to created_at — so a fresh cadence on a doc uploaded 100 days ago
        # doesn't mark it instantly overdue (review_due_at = 100d ago + 90d
        # would still be in the past).
        existing = await asyncio.to_thread(
            lambda: client.table("documents")
            .select("last_reviewed_at, created_at")
            .eq("id", doc_id)
            .maybe_single()
            .execute()
        )
        if not existing or not existing.data:
            raise HTTPException(status_code=404, detail="Document not found.")
        # Always anchor to "now" so the FIRST review window starts today.
        # Anchoring to created_at would email the admin on Monday for a doc
        # they uploaded an hour earlier — surprising and annoying.
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        next_due = _dt.now(_tz.utc) + _td(days=body.review_frequency_days)
        update = {
            "review_frequency_days": body.review_frequency_days,
            "review_due_at": next_due.isoformat(),
        }

    result = await asyncio.to_thread(
        lambda: client.table("documents")
        .update(update)
        .eq("id", doc_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Document not found.")
    await invalidate_document_caches(org_id)
    return {"document": result.data[0]}


@router.post("/{doc_id}/mark-reviewed")
async def mark_document_reviewed(
    doc_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Reset the review timer (Day 13 / #38).

    Delegates to the SECURITY DEFINER RPC `mark_document_reviewed` so the
    cadence + last_reviewed_at + review_due_at recompute happens atomically
    against concurrent admin clicks. We still gate access by checking org
    ownership via the user-scoped client before calling the RPC.
    """
    org_id: str | None = current_user["org_id"]
    if not org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No organization found.")
    try:
        uuid.UUID(doc_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid document id.")

    user_client = get_user_client(current_user["token"])
    owned = await asyncio.to_thread(
        lambda: user_client.table("documents")
        .select("id")
        .eq("id", doc_id)
        .maybe_single()
        .execute()
    )
    if not owned or not owned.data:
        raise HTTPException(status_code=404, detail="Document not found.")

    svc = get_service_client()
    try:
        result = await asyncio.to_thread(
            lambda: svc.rpc(
                "mark_document_reviewed", {"target_doc_id": doc_id}
            ).execute()
        )
    except Exception as exc:
        log.error("mark_reviewed_failed: %s", exc)
        raise HTTPException(
            status_code=500, detail="Could not mark document reviewed."
        ) from exc

    next_due = result.data if result else None
    await invalidate_document_caches(org_id)
    return {"status": "ok", "next_due_at": next_due}


class PolicyFlagBody(BaseModel):
    requires_acknowledgement: bool


@router.patch("/{doc_id}/policy-flag")
async def update_policy_flag(
    doc_id: str,
    body: PolicyFlagBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Admin toggle for `documents.requires_acknowledgement` (Day 9).

    Flipping the flag to TRUE on an already-ready doc kicks the propagation
    agent. Flipping to FALSE just stops future versions from propagating —
    we leave existing acknowledgement rows in place because they're an
    audit record people may need.
    """
    org_id: str | None = current_user["org_id"]
    user_id: str | None = current_user["user_id"]
    token: str | None = current_user["token"]
    if not org_id or not user_id or not token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No organization found.")
    try:
        uuid.UUID(doc_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid document id.")

    user_client = get_user_client(token)
    me = await asyncio.to_thread(
        lambda: user_client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required.")

    svc = get_service_client()
    doc_res = await asyncio.to_thread(
        lambda: svc.table("documents")
        .select("id, status, requires_acknowledgement")
        .eq("id", doc_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not doc_res or not doc_res.data:
        raise HTTPException(status_code=404, detail="Document not found.")

    was = bool((doc_res.data or {}).get("requires_acknowledgement"))
    await asyncio.to_thread(
        lambda: svc.table("documents")
        .update({"requires_acknowledgement": body.requires_acknowledgement})
        .eq("id", doc_id)
        .execute()
    )

    # If we just flipped ON and the doc is ready, fire propagation immediately.
    fired = False
    if body.requires_acknowledgement and not was and (doc_res.data or {}).get("status") == "ready":
        try:
            from app.inngest.client import get_inngest_client
            import inngest as _inngest

            client = get_inngest_client()
            await client.send(
                _inngest.Event(
                    name="agent/policy-propagate",
                    data={"document_id": doc_id, "org_id": org_id, "triggered_by": "flag_on"},
                    id=f"policy-flag-on-{doc_id}-{uuid.uuid4().hex[:8]}",
                )
            )
            fired = True
        except Exception:
            pass

    await invalidate_document_caches(org_id)
    return {"requires_acknowledgement": body.requires_acknowledgement, "propagation_queued": fired}


@router.patch("/{doc_id}/tags")
async def update_document_tags(
    doc_id: str,
    body: TagsPatchBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Replace the tag set on a single document.

    PATCH-with-replacement (PUT semantics) is what the per-row tag editor
    needs: the UI shows the full chip set and an edit means "this is now
    the truth", not "add these to whatever's there".
    """
    org_id: str | None = current_user["org_id"]
    if not org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No organization found.")
    try:
        uuid.UUID(doc_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid document id.")

    cleaned = _normalise_tags(body.tags, cap=TAG_MAX_PER_DOC)

    user_client = get_user_client(current_user["token"])
    result = await asyncio.to_thread(
        lambda: user_client.table("documents")
        .update({"tags": cleaned})
        .eq("id", doc_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Document not found.")
    await invalidate_document_caches(org_id)
    return {"document": result.data[0]}


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
        .select("file_path, file_type")
        .eq("id", doc_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    file_path: str = result.data["file_path"]
    file_type: str | None = result.data.get("file_type")

    await asyncio.to_thread(
        lambda: svc.table("documents").delete().eq("id", doc_id).execute()
    )

    try:
        await asyncio.to_thread(
            lambda: svc.storage.from_(STORAGE_BUCKET).remove([file_path])
        )
    except Exception:
        pass

    await invalidate_document_caches(org_id)

    try:
        from app.services.analytics import track_event

        await track_event(
            org_id=org_id,
            user_id=current_user.get("user_id"),
            event_type="doc_deleted",
            metadata={"file_type": file_type},
        )
    except Exception:
        pass


# ── Inngest trigger ───────────────────────────────────────────────────────────

async def _trigger_processing(
    doc_id: str,
    org_id: str,
    file_path: str,
    file_type: str,
    *,
    attempt_key: str | None = None,
    version_id: str | None = None,
) -> None:
    """Emit the doc/uploaded event. Inngest's process-document function picks it up.

    Uses idempotency_key on the event so a duplicate user click doesn't kick off
    a second pipeline run (Inngest deduplicates within a 24h window). Retry
    callers pass `attempt_key` (e.g. a unix-minute) so deliberate re-runs aren't
    silently dropped by that same dedup window.
    """
    event_id = (
        f"doc-uploaded-{doc_id}-{attempt_key}" if attempt_key else f"doc-uploaded-{doc_id}"
    )
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
                    "version_id": version_id,
                },
                id=event_id,
            )
        )
    except Exception as exc:
        log.error("Failed to send doc/uploaded event for %s: %s", doc_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to queue document for processing. Please try again.",
        ) from exc


async def _trigger_text_processing(
    *,
    doc_id: str,
    org_id: str,
    text: str,
    title: str | None = None,
    source_url: str | None = None,
) -> None:
    """Emit the generic doc/process-text Inngest event.

    Shared with the Notion sync + email-forward inbound paths
    (see `inngest/integration_functions.py`). The Inngest function reads the
    text straight from the event payload — 50k chars fits well under
    Inngest's per-event size cap, and skipping a DB round-trip in the
    function keeps the retry path simple.
    """
    client = get_inngest_client()
    try:
        await client.send(
            inngest.Event(
                name="doc/process-text",
                data={
                    "doc_id": doc_id,
                    "org_id": org_id,
                    "text": text,
                    "title": title,
                    "source_url": source_url,
                },
                id=f"doc-process-text-{doc_id}",
            )
        )
    except Exception as exc:
        log.error("Failed to send doc/process-text event for %s: %s", doc_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to queue page for processing. Please try again.",
        ) from exc


async def _maybe_auto_match_recommendation(*, doc_id: str, org_id: str) -> None:
    """V3 #50 — match a freshly-uploaded document against the org's
    recommendation checklist by fuzzy name overlap (Jaccard tokens).

    Read–modify–write on `organizations.recommended_documents`. Concurrent
    uploads of two docs that match different recommendations can race; in
    that case the second write loses one match. Acceptable trade-off — the
    auto-match is a nice-to-have UX touch, not a billing-critical invariant.
    Locking the row via SELECT FOR UPDATE would help but PostgREST doesn't
    expose it cleanly.

    Best-effort throughout: no return value, callers swallow exceptions.
    """
    from app.services.recommendations import (
        best_recommendation_match,
        mark_match,
    )

    svc = get_service_client()
    doc_row = await asyncio.to_thread(
        lambda: svc.table("documents")
        .select("name")
        .eq("id", doc_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not doc_row or not doc_row.data:
        return
    doc_name = (doc_row.data or {}).get("name") or ""
    if not doc_name.strip():
        return

    org_row = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select("recommended_documents")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    recs = (org_row.data or {}).get("recommended_documents") if org_row else None
    if not isinstance(recs, list) or not recs:
        return

    idx = best_recommendation_match(doc_name, recs)
    if idx is None:
        return

    updated = mark_match(recs, idx, document_id=doc_id)
    await asyncio.to_thread(
        lambda: svc.table("organizations")
        .update({"recommended_documents": updated})
        .eq("id", org_id)
        .execute()
    )
    log.info(
        "recommendation_auto_matched",
        extra={
            "doc_id": doc_id,
            "org_id": org_id,
            "matched_key": updated[idx].get("key"),
        },
    )


async def _ensure_initial_version_row(
    *,
    doc_id: str,
    org_id: str,
    user_id: str | None,
    client,
) -> dict[str, Any] | None:
    """Ensure first-time uploads get a v1 row and `documents.current_version_id`.

    Existing docs that already have a current version are left untouched so this
    helper is safe to call on every `/upload/complete`.
    """
    existing = await asyncio.to_thread(
        lambda: client.table("document_versions")
        .select("id, version_number, file_path")
        .eq("document_id", doc_id)
        .eq("is_current", True)
        .limit(1)
        .execute()
    )
    rows = existing.data or []
    if rows:
        return rows[0]

    doc_res = await asyncio.to_thread(
        lambda: client.table("documents")
        .select("file_path, file_size_bytes, created_at")
        .eq("id", doc_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    doc = (doc_res and doc_res.data) or None
    if not doc or not doc.get("file_path"):
        return None

    version_id = str(uuid.uuid4())
    row = {
        "id": version_id,
        "document_id": doc_id,
        "version_number": 1,
        "file_path": doc["file_path"],
        "file_size_bytes": doc.get("file_size_bytes"),
        "uploaded_by": user_id,
        "is_current": True,
        "created_at": doc.get("created_at"),
    }

    try:
        await asyncio.to_thread(
            lambda: client.table("document_versions").insert(row).execute()
        )
    except Exception:
        latest = await asyncio.to_thread(
            lambda: client.table("document_versions")
            .select("id, version_number, file_path")
            .eq("document_id", doc_id)
            .eq("is_current", True)
            .limit(1)
            .execute()
        )
        current = (latest.data or [])
        return current[0] if current else None

    await asyncio.to_thread(
        lambda: client.table("documents")
        .update({"current_version_id": version_id})
        .eq("id", doc_id)
        .eq("org_id", org_id)
        .execute()
    )
    return row


async def _attach_version_summaries(
    *,
    client,
    documents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Hydrate current version number + history count for the documents list."""
    if not documents:
        return documents

    doc_ids = [d["id"] for d in documents if d.get("id")]
    if not doc_ids:
        return documents

    res = await asyncio.to_thread(
        lambda: client.table("document_versions")
        .select("id, document_id, version_number, is_current, created_at")
        .in_("document_id", doc_ids)
        .order("version_number", desc=True)
        .execute()
    )
    versions = res.data or []

    counts: dict[str, int] = {}
    current: dict[str, dict[str, Any]] = {}
    for row in versions:
        doc_id = row.get("document_id")
        if not doc_id:
            continue
        counts[doc_id] = counts.get(doc_id, 0) + 1
        chosen = current.get(doc_id)
        if row.get("is_current") or chosen is None:
            current[doc_id] = row

    enriched: list[dict[str, Any]] = []
    for doc in documents:
        current_row = current.get(doc["id"])
        enriched.append(
            {
                **doc,
                "version_count": counts.get(doc["id"], 0),
                "current_version_number": (
                    int(current_row["version_number"]) if current_row and current_row.get("version_number") is not None else None
                ),
                "current_version_uploaded_at": (
                    current_row.get("created_at") if current_row else None
                ),
            }
        )
    return enriched


async def _trigger_chunk_retry(doc_id: str, org_id: str) -> None:
    """Emit doc/retry-chunks — Inngest re-embeds only `failed` chunks.

    Idempotency keyed by doc + unix-minute so a stuck-pressed retry button
    coalesces within a minute, but a deliberate retry next minute still runs.
    """
    import time
    client = get_inngest_client()
    try:
        await client.send(
            inngest.Event(
                name="doc/retry-chunks",
                data={"doc_id": doc_id, "org_id": org_id},
                id=f"doc-retry-{doc_id}-{int(time.time() // 60)}",
            )
        )
    except Exception as exc:
        log.error("Failed to send doc/retry-chunks event for %s: %s", doc_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to queue chunk retry. Please try again.",
        ) from exc
