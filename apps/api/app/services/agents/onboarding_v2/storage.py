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

# Stored signed URL TTL on `onboarding_documents.signed_url` — long enough
# that a webhook callback minutes/hours later can still resolve it.
SIGNED_URL_TTL_SECONDS = 7 * 24 * 60 * 60

# Per-request refresh TTL — every GET on a run re-mints with this shorter
# window so a URL leaked in a screenshot or browser-history dump becomes
# unusable quickly. 1h is long enough to survive a HR tab refresh + a
# download click but short enough to be cheap to invalidate.
REFRESH_SIGNED_URL_TTL_SECONDS = 60 * 60


async def mint_signed_url(storage_path: str, *, ttl_seconds: int | None = None) -> str | None:
    """Mint a fresh signed URL for an arbitrary storage path. Returns None
    on any error — callers fall back to "Download unavailable" UI rather
    than blocking the whole page load. This is the canonical refresh entry
    point used by the GET handlers."""
    svc = get_service_client()
    ttl = ttl_seconds or REFRESH_SIGNED_URL_TTL_SECONDS

    def _mint() -> str | None:
        try:
            res = svc.storage.from_(STORAGE_BUCKET).create_signed_url(
                path=storage_path, expires_in=ttl
            )
            return res.get("signedURL") or res.get("signed_url")
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "onboarding_v2.signed_url_refresh_failed path=%s err=%s",
                storage_path,
                exc,
            )
            return None

    return await asyncio.to_thread(_mint)


async def refresh_signed_urls_for_run(run_id: str) -> dict[str, str]:
    """Mint fresh signed URLs for every onboarding_documents row in a run.
    Returns a {storage_path: signed_url} mapping the caller can splice into
    its response. Doesn't write back to the DB — the URL is per-request."""
    svc = get_service_client()

    def _fetch() -> list[dict[str, Any]]:
        res = (
            svc.table("onboarding_documents")
            .select(
                "storage_path, signed_pdf_path, "
                "hr_edited_storage_path, hr_edited_pdf_path"
            )
            .eq("run_id", run_id)
            .execute()
        )
        return res.data or []

    rows = await asyncio.to_thread(_fetch)
    paths: list[str] = []
    for r in rows:
        if r.get("storage_path"):
            paths.append(r["storage_path"])
        if r.get("signed_pdf_path"):
            paths.append(r["signed_pdf_path"])
        if r.get("hr_edited_storage_path"):
            paths.append(r["hr_edited_storage_path"])
        if r.get("hr_edited_pdf_path"):
            paths.append(r["hr_edited_pdf_path"])

    # Fan out — bound parallelism to avoid hammering Supabase if a run has
    # many artifacts. asyncio.gather is fine here; mint_signed_url already
    # threads internally.
    urls = await asyncio.gather(*(mint_signed_url(p) for p in paths))
    return {p: u for p, u in zip(paths, urls) if u}


async def fetch_template_docx(
    *,
    org_id: str,
    template_kind: str,
) -> tuple[bytes, dict[str, Any]] | None:
    """Find the KB document tagged `template_kind=<kind>` for the org and
    return its raw DOCX bytes + the document row.

    The actual storage path lives on `document_versions.file_path` keyed by
    `documents.current_version_id`; we fall back to `documents.file_path`
    (legacy single-version uploads) when there's no current version row.

    Returns None if the org has not uploaded and tagged a DOCX for this kind.
    The agent treats that as a clean blocked state and prompts HR to upload
    the missing template before proceeding.
    """
    svc = get_service_client()

    def _fetch_doc() -> dict[str, Any] | None:
        # Only consider templates HR has explicitly saved (template_status =
        # 'active'). Drafts are mid-iteration and must not be picked up by
        # in-flight onboarding runs — they could be half-edited or broken.
        res = (
            svc.table("documents")
            .select(
                "id, name, file_path, file_type, current_version_id, "
                "template_kind, template_status"
            )
            .eq("org_id", org_id)
            .eq("template_kind", template_kind)
            .eq("template_status", "active")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return (res.data or [None])[0]

    doc = await asyncio.to_thread(_fetch_doc)
    if not doc:
        return None

    storage_path: str | None = None
    current_version_id = doc.get("current_version_id")
    if current_version_id:
        def _fetch_version() -> dict[str, Any] | None:
            res = (
                svc.table("document_versions")
                .select("id, file_path")
                .eq("id", current_version_id)
                .maybe_single()
                .execute()
            )
            return res.data if res else None

        version = await asyncio.to_thread(_fetch_version)
        if version:
            storage_path = version.get("file_path")

    if not storage_path:
        storage_path = doc.get("file_path")

    if not storage_path:
        log.warning(
            "onboarding_v2.template_missing_storage_path org=%s kind=%s doc=%s",
            org_id, template_kind, doc.get("id"),
        )
        return None

    def _download() -> bytes:
        return svc.storage.from_(STORAGE_BUCKET).download(storage_path)

    raw = await asyncio.to_thread(_download)
    return raw, doc


async def fetch_docuseal_template(
    *,
    org_id: str,
    template_key: str,
) -> dict[str, Any] | None:
    """Resolve the org's DocuSeal e-sign template binding for an envelope.

    `template_key` is 'loi' (routed HR → candidate) or 'offer_bundle' (the
    candidate-only Appointment Letter + NDA). Returns the row from
    onboarding_docuseal_templates — {docuseal_template_id, role_names,
    field_map, …} — or None when the org hasn't configured e-sign for this
    envelope yet (the caller then falls back to the print/scan or email flow).

    Free-tier DocuSeal can't ingest a rendered PDF over the API, so signing
    requires a template built once in the DocuSeal UI. See migration 081.
    """
    svc = get_service_client()

    def _fetch() -> dict[str, Any] | None:
        res = (
            svc.table("onboarding_docuseal_templates")
            .select("docuseal_template_id, role_names, field_map, template_key, note")
            .eq("org_id", org_id)
            .eq("template_key", template_key)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    row = await asyncio.to_thread(_fetch)
    if not row or not row.get("docuseal_template_id"):
        return None
    return row


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
