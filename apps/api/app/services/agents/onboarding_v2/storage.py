"""Supabase Storage helpers for Onboarding v2 — fetch templates, persist
generated PDFs, mint signed URLs.

We keep these in one place because every step of the agent does roughly the
same dance: read a KB template (DOCX bytes), render it, upload the PDF +
filled DOCX side-by-side under a deterministic per-run prefix, then stamp the
`onboarding_documents` row with a fresh signed URL.

The bucket layout:

    document/orgs/{org_id}/onboarding/{run_id}/{kind}.pdf
    document/orgs/{org_id}/onboarding/{run_id}/{kind}.docx
    document/orgs/{org_id}/onboarding/{run_id}/{kind}_signed.pdf

— same bucket as the rest of the KB, different prefix so the storage UI
groups onboarding artifacts together and so an org-delete cascades them.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.database import get_service_client

log = logging.getLogger(__name__)

STORAGE_BUCKET = "document"

# Signed URLs in the HR dashboard live a week; the agent re-mints them
# whenever it re-stamps an onboarding_documents row, so a stale link auto-
# refreshes on the next dashboard load.
SIGNED_URL_TTL_SECONDS = 7 * 24 * 60 * 60


async def fetch_template_docx(
    *,
    org_id: str,
    template_kind: str,
) -> tuple[bytes, dict[str, Any]] | None:
    """Find the KB document tagged `template_kind=<kind>` for the org and
    return its raw DOCX bytes + the document row.

    Returns None when no template is tagged — the agent uses that signal to
    transition the run into `blocked_missing_template` so HR knows exactly
    what to upload.
    """
    svc = get_service_client()

    def _fetch_doc() -> dict[str, Any] | None:
        res = (
            svc.table("documents")
            .select("id, name, storage_path, file_type, current_version_id, template_kind")
            .eq("org_id", org_id)
            .eq("template_kind", template_kind)
            .order("updated_at", desc=True)
            .limit(1)
            .execute()
        )
        return (res.data or [None])[0]

    doc = await asyncio.to_thread(_fetch_doc)
    if not doc:
        return None

    storage_path = doc.get("storage_path")
    if not storage_path:
        log.warning(
            "onboarding_v2.template_missing_storage_path org=%s kind=%s doc=%s",
            org_id,
            template_kind,
            doc.get("id"),
        )
        return None

    def _download() -> bytes:
        return svc.storage.from_(STORAGE_BUCKET).download(storage_path)

    raw = await asyncio.to_thread(_download)
    return raw, doc


async def upload_onboarding_artifact(
    *,
    org_id: str,
    run_id: str,
    kind: str,
    pdf_bytes: bytes,
    docx_bytes: bytes | None = None,
) -> dict[str, Any]:
    """Upload a generated artifact (and optional .docx companion) to Storage
    and return the storage paths + a signed URL for the PDF.

    Upserts: re-rendering after a tweak overwrites the previous bytes at
    the same path so we don't accumulate orphan blobs. The Storage API's
    upload-with-upsert is async-unfriendly, so we wrap in `to_thread` like
    everything else in this codebase.
    """
    svc = get_service_client()
    prefix = f"orgs/{org_id}/onboarding/{run_id}"
    pdf_path = f"{prefix}/{kind}.pdf"
    docx_path = f"{prefix}/{kind}.docx" if docx_bytes else None

    def _upload(path: str, body: bytes, mime: str) -> None:
        svc.storage.from_(STORAGE_BUCKET).upload(
            path=path,
            file=body,
            file_options={
                "content-type": mime,
                "upsert": "true",
            },
        )

    await asyncio.to_thread(_upload, pdf_path, pdf_bytes, "application/pdf")
    if docx_bytes and docx_path:
        await asyncio.to_thread(
            _upload,
            docx_path,
            docx_bytes,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    def _signed_url() -> str | None:
        try:
            res = svc.storage.from_(STORAGE_BUCKET).create_signed_url(
                path=pdf_path,
                expires_in=SIGNED_URL_TTL_SECONDS,
            )
            return res.get("signedURL") or res.get("signed_url")
        except Exception as exc:  # noqa: BLE001
            log.warning("onboarding_v2.signed_url_failed path=%s err=%s", pdf_path, exc)
            return None

    signed_url = await asyncio.to_thread(_signed_url)
    expires_at = (
        datetime.now(UTC) + timedelta(seconds=SIGNED_URL_TTL_SECONDS)
    ).isoformat()
    return {
        "pdf_path": pdf_path,
        "docx_path": docx_path,
        "signed_url": signed_url,
        "signed_url_expires_at": expires_at,
        "file_bytes": len(pdf_bytes),
    }


async def upsert_onboarding_document(
    *,
    org_id: str,
    run_id: str,
    kind: str,
    storage: dict[str, Any],
    source_template_id: str | None,
    render_context: dict[str, Any] | None,
    sign_status: str = "draft",
) -> str:
    """Insert or update the onboarding_documents row for this (run, kind).
    Returns the row id."""
    svc = get_service_client()

    def _existing() -> dict[str, Any] | None:
        res = (
            svc.table("onboarding_documents")
            .select("id")
            .eq("run_id", run_id)
            .eq("kind", kind)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    existing = await asyncio.to_thread(_existing)

    payload = {
        "org_id": org_id,
        "run_id": run_id,
        "kind": kind,
        "source_template_id": source_template_id,
        "storage_path": storage["pdf_path"],
        "signed_url": storage.get("signed_url"),
        "signed_url_expires_at": storage.get("signed_url_expires_at"),
        "file_bytes": storage.get("file_bytes"),
        "render_context": render_context,
        "sign_status": sign_status,
    }

    if existing:
        await asyncio.to_thread(
            lambda: svc.table("onboarding_documents")
            .update(payload)
            .eq("id", existing["id"])
            .execute()
        )
        return existing["id"]

    def _insert() -> str:
        res = svc.table("onboarding_documents").insert(payload).execute()
        return (res.data or [{}])[0].get("id")

    return await asyncio.to_thread(_insert)


async def log_onboarding_event(
    *,
    org_id: str,
    run_id: str,
    actor_kind: str,
    event_type: str,
    message: str | None = None,
    actor_user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append to onboarding_events for the dashboard timeline. Best-effort —
    a failure to log shouldn't fail the agent step."""
    svc = get_service_client()
    try:
        await asyncio.to_thread(
            lambda: svc.table("onboarding_events")
            .insert(
                {
                    "org_id": org_id,
                    "run_id": run_id,
                    "actor_kind": actor_kind,
                    "event_type": event_type,
                    "message": message,
                    "actor_user_id": actor_user_id,
                    "metadata": metadata,
                }
            )
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "onboarding_v2.event_log_failed run=%s type=%s err=%s",
            run_id,
            event_type,
            exc,
        )
