"""Storage + DB helpers for the Sales Agent.

Mirrors onboarding_v2/storage.py in spirit: small set of helpers that
encapsulate the Supabase Storage uploads and the deal_events / deal_approvals /
deal_documents writes that every stage of the agent does.

Bucket layout:
    document/orgs/{org_id}/sales/{deal_run_id}/{kind}-rev{N}.docx
    document/orgs/{org_id}/sales/{deal_run_id}/{kind}-rev{N}.pdf
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.database import get_service_client

log = logging.getLogger(__name__)

STORAGE_BUCKET = "document"
SIGNED_URL_TTL_SECONDS = 7 * 24 * 60 * 60
REFRESH_SIGNED_URL_TTL_SECONDS = 60 * 60


# ── Signed URL helpers ───────────────────────────────────────────────────────


async def mint_signed_url(storage_path: str, *, ttl_seconds: int | None = None) -> str | None:
    svc = get_service_client()
    ttl = ttl_seconds or REFRESH_SIGNED_URL_TTL_SECONDS

    def _mint() -> str | None:
        try:
            res = svc.storage.from_(STORAGE_BUCKET).create_signed_url(
                path=storage_path, expires_in=ttl
            )
            return res.get("signedURL") or res.get("signed_url")
        except Exception as exc:  # noqa: BLE001
            log.warning("sales.signed_url_failed path=%s err=%s", storage_path, exc)
            return None

    return await asyncio.to_thread(_mint)


# ── KB template lookups (mirrors onboarding_v2.fetch_template_docx) ─────────


async def fetch_template_docx(
    *,
    org_id: str,
    template_kind: str,
) -> tuple[bytes, dict[str, Any]] | None:
    """Find the org's KB document tagged template_kind=<kind> and return its
    raw DOCX bytes + the document row. Returns None if the template isn't
    uploaded — caller (agent) treats that as a blocked state.

    Only considers template_status='active' to avoid picking up draft templates
    HR is mid-iteration on, matching the onboarding_v2 convention.
    """
    svc = get_service_client()

    def _fetch_doc() -> dict[str, Any] | None:
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
            "sales.template_missing_storage_path org=%s kind=%s doc=%s",
            org_id, template_kind, doc.get("id"),
        )
        return None

    def _download() -> bytes:
        return svc.storage.from_(STORAGE_BUCKET).download(storage_path)

    raw = await asyncio.to_thread(_download)
    return raw, doc


async def fetch_kb_doc_by_kind(
    *,
    org_id: str,
    template_kind: str,
) -> dict[str, Any] | None:
    """Return the doc row (no bytes) for an active template/reference of the
    given kind. Used for the ICP doc lookup (we feed its content via
    hybrid_search to the LLM, not by parsing the file)."""
    svc = get_service_client()

    def _q() -> dict[str, Any] | None:
        res = (
            svc.table("documents")
            .select("id, name, template_kind, current_version_id, file_path")
            .eq("org_id", org_id)
            .eq("template_kind", template_kind)
            .eq("template_status", "active")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return (res.data or [None])[0]

    return await asyncio.to_thread(_q)


# ── Artifact uploads (proposal/sow/pricing) ─────────────────────────────────


async def upload_deal_artifact(
    *,
    org_id: str,
    deal_run_id: str,
    kind: str,
    revision: int,
    pdf_bytes: bytes | None = None,
    docx_bytes: bytes | None = None,
) -> dict[str, Any]:
    """Upload a generated proposal/sow/pricing artifact. Always uses a
    revision-suffixed path so we don't overwrite older drafts (audit + ability
    to compare HR-edits to the original agent version)."""
    svc = get_service_client()
    prefix = f"orgs/{org_id}/sales/{deal_run_id}"
    docx_path = f"{prefix}/{kind}-rev{revision}.docx" if docx_bytes else None
    pdf_path = f"{prefix}/{kind}-rev{revision}.pdf" if pdf_bytes else None

    def _upload(path: str, body: bytes, mime: str) -> None:
        svc.storage.from_(STORAGE_BUCKET).upload(
            path=path,
            file=body,
            file_options={
                "content-type": mime,
                "upsert": "true",
            },
        )

    if docx_bytes and docx_path:
        await asyncio.to_thread(
            _upload, docx_path, docx_bytes,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    if pdf_bytes and pdf_path:
        await asyncio.to_thread(_upload, pdf_path, pdf_bytes, "application/pdf")

    signed_url = None
    if pdf_path:
        signed_url = await mint_signed_url(pdf_path, ttl_seconds=SIGNED_URL_TTL_SECONDS)

    return {
        "docx_path": docx_path,
        "pdf_path": pdf_path,
        "signed_url": signed_url,
        "file_bytes": (len(pdf_bytes) if pdf_bytes else 0)
        + (len(docx_bytes) if docx_bytes else 0),
    }


async def insert_deal_document(
    *,
    org_id: str,
    deal_run_id: str,
    kind: str,
    revision: int,
    source: str,
    storage_path: str,
    pdf_storage_path: str | None = None,
    source_template_id: str | None = None,
    render_context: dict[str, Any] | None = None,
    file_bytes: int | None = None,
) -> str:
    svc = get_service_client()
    payload = {
        "org_id": org_id,
        "deal_run_id": deal_run_id,
        "kind": kind,
        "revision": revision,
        "source": source,
        "storage_path": storage_path,
        "pdf_storage_path": pdf_storage_path,
        "source_template_id": source_template_id,
        "render_context": render_context,
        "file_bytes": file_bytes,
    }

    def _insert() -> dict[str, Any]:
        res = svc.table("deal_documents").insert(payload).execute()
        return (res.data or [{}])[0]

    inserted = await asyncio.to_thread(_insert)
    return inserted["id"]


# ── deal_events ──────────────────────────────────────────────────────────────


async def log_deal_event(
    *,
    org_id: str,
    deal_run_id: str,
    actor_kind: str,
    event_type: str,
    message: str | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    payload: dict[str, Any] | None = None,
    actor_user_id: str | None = None,
) -> None:
    """Append a row to deal_events. Best-effort — a failure here must not
    abort the agent (we already mutated the deal_run row)."""
    svc = get_service_client()
    row = {
        "org_id": org_id,
        "deal_run_id": deal_run_id,
        "actor_kind": actor_kind,
        "actor_user_id": actor_user_id,
        "event_type": event_type,
        "message": message,
        "from_status": from_status,
        "to_status": to_status,
        "payload": payload or {},
    }
    try:
        await asyncio.to_thread(lambda: svc.table("deal_events").insert(row).execute())
    except Exception as exc:  # noqa: BLE001
        log.warning("sales.event_log_failed deal=%s err=%s", deal_run_id, exc)


# ── deal_approvals ───────────────────────────────────────────────────────────


async def create_deal_approval(
    *,
    org_id: str,
    deal_run_id: str,
    gate_type: str,
    gate_variant: str | None = None,
    draft_subject: str | None = None,
    draft_content: str | None = None,
    draft_document_id: str | None = None,
) -> str:
    """Insert a new pending approval row. The revision is auto-incremented
    against the same (deal_run_id, gate_type) bucket so retries / regens
    show up as v2, v3, ... rather than colliding."""
    svc = get_service_client()

    def _max_rev() -> int:
        res = (
            svc.table("deal_approvals")
            .select("revision")
            .eq("deal_run_id", deal_run_id)
            .eq("gate_type", gate_type)
            .order("revision", desc=True)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return rows[0]["revision"] if rows else 0

    cur = await asyncio.to_thread(_max_rev)
    payload = {
        "org_id": org_id,
        "deal_run_id": deal_run_id,
        "gate_type": gate_type,
        "gate_variant": gate_variant,
        "draft_subject": draft_subject,
        "draft_content": draft_content,
        "draft_document_id": draft_document_id,
        "revision": cur + 1,
        "status": "pending",
    }

    def _insert() -> dict[str, Any]:
        res = svc.table("deal_approvals").insert(payload).execute()
        return (res.data or [{}])[0]

    inserted = await asyncio.to_thread(_insert)
    return inserted["id"]


async def get_latest_pending_approval(
    *, deal_run_id: str, gate_type: str | None = None
) -> dict[str, Any] | None:
    svc = get_service_client()

    def _q() -> dict[str, Any] | None:
        q = (
            svc.table("deal_approvals")
            .select("*")
            .eq("deal_run_id", deal_run_id)
            .eq("status", "pending")
            .order("created_at", desc=True)
            .limit(1)
        )
        if gate_type:
            q = q.eq("gate_type", gate_type)
        res = q.execute()
        return (res.data or [None])[0]

    return await asyncio.to_thread(_q)


# ── deal_research ────────────────────────────────────────────────────────────


async def insert_deal_research(
    *,
    org_id: str,
    deal_run_id: str,
    source: str,
    payload: dict[str, Any],
) -> str:
    svc = get_service_client()
    row = {
        "org_id": org_id,
        "deal_run_id": deal_run_id,
        "source": source,
        "company_summary": payload.get("company_summary"),
        "headcount_estimate": payload.get("headcount_estimate"),
        "industry": payload.get("industry"),
        "tech_stack_hints": payload.get("tech_stack_hints"),
        "pain_point_hypothesis": payload.get("pain_point_hypothesis"),
        "competitors_detected": payload.get("competitors_detected"),
        "similar_past_deals": payload.get("similar_past_deals"),
        "raw_sources": payload.get("raw_sources"),
        "has_usable_signal": bool(payload.get("has_usable_signal", False)),
    }

    def _ins() -> dict[str, Any]:
        res = svc.table("deal_research").insert(row).execute()
        return (res.data or [{}])[0]

    inserted = await asyncio.to_thread(_ins)
    return inserted["id"]


async def fetch_latest_research(deal_run_id: str) -> dict[str, Any] | None:
    svc = get_service_client()

    def _q() -> dict[str, Any] | None:
        res = (
            svc.table("deal_research")
            .select("*")
            .eq("deal_run_id", deal_run_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return (res.data or [None])[0]

    return await asyncio.to_thread(_q)


# ── Refresh signed URLs for a deal (for the detail GET endpoint) ────────────


async def refresh_signed_urls_for_deal(deal_run_id: str) -> dict[str, str]:
    svc = get_service_client()

    def _fetch() -> list[dict[str, Any]]:
        res = (
            svc.table("deal_documents")
            .select("storage_path, pdf_storage_path, signed_pdf_path")
            .eq("deal_run_id", deal_run_id)
            .execute()
        )
        return res.data or []

    rows = await asyncio.to_thread(_fetch)
    paths: list[str] = []
    for r in rows:
        for k in ("storage_path", "pdf_storage_path", "signed_pdf_path"):
            v = r.get(k)
            if v:
                paths.append(v)
    if not paths:
        return {}
    urls = await asyncio.gather(*(mint_signed_url(p) for p in paths))
    return {p: u for p, u in zip(paths, urls) if u}
