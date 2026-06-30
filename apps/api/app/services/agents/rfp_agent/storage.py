"""Supabase Storage I/O for the RFP Agent.

Bucket: `rfp-files` (created in migration 077). We use a SEPARATE bucket
from the KB ingestion bucket because RFP buyer files are typically under
NDA and shouldn't ever be at risk of leaking into the company KB.

Layout:
    orgs/{org_id}/rfp/{rfp_id}/source.{ext}             — raw buyer upload
    orgs/{org_id}/rfp/{rfp_id}/filled-rev{N}.{ext}      — fill-original output
    orgs/{org_id}/rfp/{rfp_id}/summary-rev{N}.docx      — clean DOCX summary
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.database import get_service_client

log = logging.getLogger(__name__)

STORAGE_BUCKET = "rfp-files"

# Signed URLs expire after 24h — enough time to download, short enough that
# a leaked URL has limited blast radius. Frontend re-mints on each download.
SIGNED_URL_TTL_SECONDS = 24 * 60 * 60


def _ext_for_format(fmt: str) -> str:
    return {"xlsx": "xlsx", "docx": "docx", "pdf": "pdf", "csv": "csv", "md": "md", "txt": "txt"}.get(fmt, fmt)


async def upload_source(
    *,
    org_id: str,
    rfp_id: str,
    file_bytes: bytes,
    source_format: str,
) -> str:
    """Persist the buyer's original file. Returns the storage path.

    We MUST keep the source bytes around — the exporter's "fill original"
    pass loads them to write answers back into the buyer's exact template.
    """
    svc = get_service_client()
    ext = _ext_for_format(source_format)
    path = f"orgs/{org_id}/rfp/{rfp_id}/source.{ext}"
    mime = _mime_for_format(source_format)

    def _upload() -> None:
        svc.storage.from_(STORAGE_BUCKET).upload(
            path=path,
            file=file_bytes,
            file_options={"content-type": mime, "upsert": "true"},
        )

    await asyncio.to_thread(_upload)
    return path


async def download_source(*, storage_path: str) -> bytes:
    svc = get_service_client()

    def _download() -> bytes:
        return svc.storage.from_(STORAGE_BUCKET).download(storage_path)

    return await asyncio.to_thread(_download)


async def upload_output(
    *,
    org_id: str,
    rfp_id: str,
    revision: int,
    file_bytes: bytes,
    kind: str,        # "filled" | "summary"
    source_format: str | None = None,
) -> str:
    """Upload a generated artifact. `kind=filled` keeps the source format
    (xlsx/docx). `kind=summary` is always DOCX. Returns storage path."""
    svc = get_service_client()
    if kind == "summary":
        ext, mime = "docx", _mime_for_format("docx")
    else:
        ext = _ext_for_format(source_format or "docx")
        mime = _mime_for_format(source_format or "docx")
    path = f"orgs/{org_id}/rfp/{rfp_id}/{kind}-rev{revision}.{ext}"

    def _upload() -> None:
        svc.storage.from_(STORAGE_BUCKET).upload(
            path=path,
            file=file_bytes,
            file_options={"content-type": mime, "upsert": "true"},
        )

    await asyncio.to_thread(_upload)
    return path


async def mint_signed_url(storage_path: str, *, ttl_seconds: int | None = None) -> str | None:
    svc = get_service_client()
    ttl = ttl_seconds or SIGNED_URL_TTL_SECONDS

    def _mint() -> str | None:
        try:
            res = svc.storage.from_(STORAGE_BUCKET).create_signed_url(
                path=storage_path, expires_in=ttl
            )
            return res.get("signedURL") or res.get("signed_url")
        except Exception as exc:
            log.warning("rfp.signed_url_failed path=%s err=%s", storage_path, exc)
            return None

    return await asyncio.to_thread(_mint)


def _mime_for_format(fmt: str) -> str:
    return {
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pdf": "application/pdf",
        "csv": "text/csv",
        "md": "text/markdown",
        "txt": "text/plain",
    }.get(fmt, "application/octet-stream")


# ── Approval helpers ────────────────────────────────────────────────────────


async def create_rfp_approval(
    *,
    org_id: str,
    rfp_id: str,
    gate_type: str,            # 'rep' | 'legal'
    revision: int = 1,
) -> str:
    svc = get_service_client()

    def _insert() -> dict[str, Any]:
        res = (
            svc.table("rfp_approvals")
            .insert(
                {
                    "org_id": org_id,
                    "rfp_id": rfp_id,
                    "gate_type": gate_type,
                    "status": "pending",
                    "revision": revision,
                }
            )
            .execute()
        )
        rows = res.data or []
        if not rows:
            raise RuntimeError("rfp_approval_insert_failed")
        return rows[0]

    row = await asyncio.to_thread(_insert)
    return row["id"]


async def fetch_org_rfp_collection(org_id: str) -> str | None:
    """Return the org's configured RFP-approved collection_id (if any)."""
    svc = get_service_client()

    def _q() -> str | None:
        res = (
            svc.table("organizations")
            .select("rfp_approved_collection_id")
            .eq("id", org_id)
            .maybe_single()
            .execute()
        )
        return (res.data or {}).get("rfp_approved_collection_id") if res else None

    return await asyncio.to_thread(_q)
