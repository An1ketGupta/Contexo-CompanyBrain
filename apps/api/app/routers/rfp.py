"""RFP endpoints (Agent-driven, replaces legacy sales_enablement RFP routes).

Surface (all under /sales/rfp/* to preserve the frontend's existing calls):

  POST   /sales/rfp/upload                       — multipart upload, triggers extract
  GET    /sales/rfp                              — list
  GET    /sales/rfp/{id}                         — minimal read (status, gap_count, etc.)
  GET    /sales/rfp/{id}/full                    — read with requirements + answers
  PATCH  /sales/rfp/{id}                         — meta edits (title, deal_run_id, collection_id, legal_required)

  PATCH  /sales/rfp/{id}/requirements            — legacy bulk edit (kept for old UI)
  POST   /sales/rfp/{id}/requirements            — add a new requirement
  PATCH  /sales/rfp/{id}/requirements/{req_id}   — edit one
  DELETE /sales/rfp/{id}/requirements/{req_id}   — remove one
  POST   /sales/rfp/{id}/requirements/approve    — approve list → kick drafting

  PATCH  /sales/rfp/{id}/answers/{answer_id}     — rep edits an answer
  POST   /sales/rfp/{id}/answers/{req_id}/regenerate  — regen one answer

  POST   /sales/rfp/{id}/approve-rep             — rep approves package → legal/finalize
  POST   /sales/rfp/{id}/approve-legal           — legal approves → finalize
  POST   /sales/rfp/{id}/reject-legal            — legal rejects → redraft

  POST   /sales/rfp/{id}/generate                — legacy alias: kick drafting
  GET    /sales/rfp/{id}/download                — signed-URL download (kind=filled|summary)
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.inngest.client import get_inngest_client
from app.services.agents.rfp_agent import storage as rfp_storage

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sales", tags=["rfp"])

# Match sales_enablement.py's 25MB cap. Enterprise RFPs with embedded
# screenshots can balloon; if we see complaints we'll bump or switch to
# direct-to-Supabase Storage uploads like the doc upload path does.
_MAX_RFP_BYTES = 25 * 1024 * 1024


# ── Helpers ──────────────────────────────────────────────────────────────


def _require_org(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return org_id, user_id, token


def _detect_format(filename: str) -> str:
    ext = (filename.rsplit(".", 1)[-1] or "").lower()
    return {
        "xlsx": "xlsx",
        "xls": "xlsx",
        "docx": "docx",
        "doc": "docx",
        "pdf": "pdf",
        "csv": "csv",
        "md": "md",
        "txt": "txt",
    }.get(ext, "pdf")


def _to_rfp_summary(row: dict[str, Any]) -> dict[str, Any]:
    """Compact shape for the list view."""
    return {
        "id": row["id"],
        "title": row.get("title") or row.get("source_filename"),
        "source_filename": row.get("source_filename"),
        "status": row.get("status") or "extracting",
        "gap_count": int(row.get("gap_count") or 0),
        "deal_run_id": row.get("deal_run_id"),
        "collection_id": row.get("collection_id"),
        "legal_required": bool(row.get("legal_required", True)),
        "error_message": row.get("error_message"),
        "created_at": row.get("created_at"),
        "generated_at": row.get("generated_at"),
    }


def _to_legacy_read(row: dict[str, Any]) -> dict[str, Any]:
    """Backwards-compat shape for the original /sales/rfp/{id} GET (the old
    UI reads this). Pulls requirements/matches from the JSONB columns we
    keep populated on extract for backward compat."""
    return {
        "id": row["id"],
        "org_id": row["org_id"],
        "created_by": row["created_by"],
        "source_filename": row.get("source_filename") or "",
        "requirements": row.get("requirements") or [],
        "matches": row.get("matches") or [],
        "status": row.get("status") or "extracting",
        "output_file_path": row.get("output_file_path"),
        "gap_count": int(row.get("gap_count") or 0),
        "error_message": row.get("error_message"),
        "created_at": row.get("created_at"),
        "generated_at": row.get("generated_at"),
    }


async def _send_inngest(event: str, data: dict[str, Any]) -> None:
    client = get_inngest_client()
    import inngest as inngest_lib

    await client.send(inngest_lib.Event(name=event, data=data))


async def _fetch_rfp_row(rfp_id: str, org_id: str) -> dict[str, Any] | None:
    svc = get_service_client()

    def _q() -> dict[str, Any] | None:
        res = (
            svc.table("rfp_responses")
            .select("*")
            .eq("id", rfp_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    return await asyncio.to_thread(_q)


# ── Request models ──────────────────────────────────────────────────────


class UpdateMetaRequest(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    deal_run_id: str | None = None
    collection_id: str | None = None
    legal_required: bool | None = None


class RequirementEditRequest(BaseModel):
    requirement_text: str | None = Field(default=None, max_length=4000)
    category: str | None = Field(default=None, max_length=120)
    external_ref: str | None = Field(default=None, max_length=64)


class RequirementAddRequest(BaseModel):
    requirement_text: str = Field(..., min_length=5, max_length=4000)
    category: str | None = Field(default=None, max_length=120)


class AnswerEditRequest(BaseModel):
    answer_text: str | None = Field(default=None, max_length=10_000)
    status: Literal["draft", "edited", "approved", "gap", "rejected"] | None = None


class LegalRejectRequest(BaseModel):
    notes: str = Field(..., min_length=1, max_length=4000)
    rejected_requirement_ids: list[str] = Field(default_factory=list)


class LegacyUpdateRequirementsRequest(BaseModel):
    """Old UI shape: posts back the entire requirements array.
    Each item: {id, requirement_text, category}. We sync the rfp_requirements
    table to match."""

    requirements: list[dict[str, Any]]


# ── Upload (replaces sales_enablement.upload_rfp) ────────────────────────


@router.post("/rfp/upload")
async def upload_rfp(
    file: UploadFile = File(...),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, _ = _require_org(current_user)

    content = await file.read(_MAX_RFP_BYTES + 1)
    if len(content) > _MAX_RFP_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"RFP file exceeds {_MAX_RFP_BYTES // (1024 * 1024)} MB.",
        )
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Empty file.")

    filename = file.filename or "rfp"
    source_format = _detect_format(filename)
    title = filename.rsplit(".", 1)[0]

    rfp_id = str(uuid.uuid4())
    svc = get_service_client()

    # Persist row first so we have an id for the storage path.
    def _insert() -> dict[str, Any]:
        res = (
            svc.table("rfp_responses")
            .insert(
                {
                    "id": rfp_id,
                    "org_id": org_id,
                    "created_by": user_id,
                    "source_filename": filename,
                    "title": title,
                    "source_format": source_format,
                    "status": "extracting",
                    "requirements": [],
                    "matches": [],
                }
            )
            .execute()
        )
        rows = res.data or []
        if not rows:
            raise RuntimeError("rfp_insert_failed")
        return rows[0]

    row = await asyncio.to_thread(_insert)

    # Upload source bytes to private storage.
    try:
        source_path = await rfp_storage.upload_source(
            org_id=org_id,
            rfp_id=rfp_id,
            file_bytes=content,
            source_format=source_format,
        )
    except Exception as exc:
        # Roll back the row insert — caller can retry cleanly.
        log.exception("rfp.upload.storage_failed rfp=%s", rfp_id)
        await asyncio.to_thread(
            lambda: svc.table("rfp_responses").delete().eq("id", rfp_id).execute()
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"Storage upload failed: {exc}",
        ) from exc

    await asyncio.to_thread(
        lambda: svc.table("rfp_responses")
        .update({"source_storage_path": source_path})
        .eq("id", rfp_id)
        .execute()
    )

    # Trigger the agent.
    await _send_inngest(
        "rfp/uploaded",
        {
            "rfp_id": rfp_id,
            "org_id": org_id,
            "triggered_by_user_id": user_id,
        },
    )

    row["source_storage_path"] = source_path
    return _to_legacy_read(row)


# ── Read / list ──────────────────────────────────────────────────────────


@router.get("/rfp")
async def list_rfps(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _user_id, token = _require_org(current_user)
    client = get_user_client(token)

    def _fetch() -> list[dict[str, Any]]:
        res = (
            client.table("rfp_responses")
            .select(
                "id, title, source_filename, status, gap_count, deal_run_id, "
                "collection_id, legal_required, error_message, created_at, generated_at"
            )
            .eq("org_id", org_id)
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )
        return res.data or []

    rows = await asyncio.to_thread(_fetch)
    return {"rfps": [_to_rfp_summary(r) for r in rows]}


@router.get("/rfp/{rfp_id}")
async def get_rfp(
    rfp_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _user_id, _ = _require_org(current_user)
    row = await _fetch_rfp_row(rfp_id, org_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "RFP not found.")
    return _to_legacy_read(row)


@router.get("/rfp/{rfp_id}/full")
async def get_rfp_full(
    rfp_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Detail view: top-level row + per-requirement rows + per-answer rows."""
    org_id, _user_id, _ = _require_org(current_user)
    row = await _fetch_rfp_row(rfp_id, org_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "RFP not found.")

    svc = get_service_client()

    def _fetch_reqs() -> list[dict[str, Any]]:
        res = (
            svc.table("rfp_requirements")
            .select("*")
            .eq("rfp_id", rfp_id)
            .order("ordinal")
            .execute()
        )
        return res.data or []

    def _fetch_answers() -> list[dict[str, Any]]:
        res = (
            svc.table("rfp_answers")
            .select("*")
            .eq("rfp_id", rfp_id)
            .execute()
        )
        return res.data or []

    def _fetch_approvals() -> list[dict[str, Any]]:
        res = (
            svc.table("rfp_approvals")
            .select("*")
            .eq("rfp_id", rfp_id)
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
        return res.data or []

    reqs, answers, approvals = await asyncio.gather(
        asyncio.to_thread(_fetch_reqs),
        asyncio.to_thread(_fetch_answers),
        asyncio.to_thread(_fetch_approvals),
    )

    return {
        "rfp": _to_rfp_summary(row),
        "requirements": reqs,
        "answers": answers,
        "approvals": approvals,
        # Compatible fields for the legacy UI bits
        "source_filename": row.get("source_filename"),
        "status": row.get("status"),
        "gap_count": int(row.get("gap_count") or 0),
        "error_message": row.get("error_message"),
    }


@router.patch("/rfp/{rfp_id}")
async def update_rfp_meta(
    rfp_id: str,
    body: UpdateMetaRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _user_id, _ = _require_org(current_user)
    svc = get_service_client()
    payload = {k: v for k, v in body.model_dump(exclude_unset=True).items()}
    if not payload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields to update.")

    def _update() -> dict[str, Any] | None:
        res = (
            svc.table("rfp_responses")
            .update(payload)
            .eq("id", rfp_id)
            .eq("org_id", org_id)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None

    row = await asyncio.to_thread(_update)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "RFP not found.")
    return _to_legacy_read(row)


# ── Requirements ─────────────────────────────────────────────────────────


@router.patch("/rfp/{rfp_id}/requirements")
async def legacy_bulk_update_requirements(
    rfp_id: str,
    body: LegacyUpdateRequirementsRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Legacy endpoint hit by the original UI which posts back the whole list.
    We replace rows in rfp_requirements to match, then advance the state
    machine to drafting on the next /generate call (the UI does both in sequence)."""
    org_id, _user_id, _ = _require_org(current_user)
    svc = get_service_client()

    new_reqs = body.requirements or []
    # Sync rfp_requirements: delete existing rows then insert the new set. The
    # ON DELETE CASCADE from rfp_answers will clear stale answers too, which
    # is the right behavior — if the rep removes a row mid-flow the prior
    # answer is no longer relevant.
    def _replace() -> None:
        svc.table("rfp_requirements").delete().eq("rfp_id", rfp_id).execute()
        if not new_reqs:
            return
        rows = []
        for i, r in enumerate(new_reqs):
            text = (r.get("requirement_text") or "").strip()
            if not text:
                continue
            rows.append(
                {
                    "rfp_id": rfp_id,
                    "org_id": org_id,
                    "ordinal": i,
                    "external_ref": r.get("id"),
                    "requirement_text": text[:4000],
                    "category": (r.get("category") or "")[:120] or None,
                    "is_cluster_lead": True,
                }
            )
        if rows:
            svc.table("rfp_requirements").insert(rows).execute()

    await asyncio.to_thread(_replace)

    # Also mirror to JSONB column for backwards compat.
    def _update_top() -> dict[str, Any] | None:
        res = (
            svc.table("rfp_responses")
            .update(
                {
                    "requirements": new_reqs,
                    "status": "awaiting_requirements_review",
                }
            )
            .eq("id", rfp_id)
            .eq("org_id", org_id)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None

    row = await asyncio.to_thread(_update_top)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "RFP not found.")
    return _to_legacy_read(row)


@router.post("/rfp/{rfp_id}/requirements")
async def add_requirement(
    rfp_id: str,
    body: RequirementAddRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _user_id, _ = _require_org(current_user)
    svc = get_service_client()

    def _q_max() -> int:
        res = (
            svc.table("rfp_requirements")
            .select("ordinal")
            .eq("rfp_id", rfp_id)
            .order("ordinal", desc=True)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return int(rows[0]["ordinal"]) + 1 if rows else 0

    def _insert(ordinal: int) -> dict[str, Any]:
        res = (
            svc.table("rfp_requirements")
            .insert(
                {
                    "rfp_id": rfp_id,
                    "org_id": org_id,
                    "ordinal": ordinal,
                    "requirement_text": body.requirement_text.strip()[:4000],
                    "category": (body.category or "")[:120] or None,
                    "is_cluster_lead": True,
                }
            )
            .execute()
        )
        rows = res.data or []
        if not rows:
            raise RuntimeError("rfp_requirement_insert_failed")
        return rows[0]

    ordinal = await asyncio.to_thread(_q_max)
    row = await asyncio.to_thread(_insert, ordinal)
    return row


@router.patch("/rfp/{rfp_id}/requirements/{req_id}")
async def update_requirement(
    rfp_id: str,
    req_id: str,
    body: RequirementEditRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _user_id, _ = _require_org(current_user)
    svc = get_service_client()
    payload = {k: v for k, v in body.model_dump(exclude_unset=True).items()}
    if not payload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields to update.")

    def _update() -> dict[str, Any] | None:
        res = (
            svc.table("rfp_requirements")
            .update(payload)
            .eq("id", req_id)
            .eq("rfp_id", rfp_id)
            .eq("org_id", org_id)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None

    row = await asyncio.to_thread(_update)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Requirement not found.")
    return row


@router.delete("/rfp/{rfp_id}/requirements/{req_id}")
async def delete_requirement(
    rfp_id: str,
    req_id: str,
    current_user: dict = Depends(verify_jwt),
) -> Response:
    org_id, _user_id, _ = _require_org(current_user)
    svc = get_service_client()

    def _delete() -> None:
        svc.table("rfp_requirements").delete().eq("id", req_id).eq("rfp_id", rfp_id).eq("org_id", org_id).execute()

    await asyncio.to_thread(_delete)
    return Response(status_code=204)


@router.post("/rfp/{rfp_id}/requirements/approve")
async def approve_requirements(
    rfp_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Rep approved the requirements list. Trigger answer drafting."""
    org_id, user_id, _ = _require_org(current_user)
    row = await _fetch_rfp_row(rfp_id, org_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "RFP not found.")
    if row.get("status") in ("ready", "drafting", "finalizing"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Cannot approve while status is {row.get('status')}.",
        )

    await _send_inngest(
        "rfp/requirements-approved",
        {"rfp_id": rfp_id, "org_id": org_id, "triggered_by_user_id": user_id},
    )
    return {"status": "drafting", "rfp_id": rfp_id}


# Legacy alias used by existing UI's "Generate response" button.
@router.post("/rfp/{rfp_id}/generate")
async def legacy_generate(
    rfp_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    return await approve_requirements(rfp_id, current_user)


# ── Answers ──────────────────────────────────────────────────────────────


@router.patch("/rfp/{rfp_id}/answers/{answer_id}")
async def edit_answer(
    rfp_id: str,
    answer_id: str,
    body: AnswerEditRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, _ = _require_org(current_user)
    svc = get_service_client()

    payload: dict[str, Any] = {}
    if body.answer_text is not None:
        payload["answer_text"] = body.answer_text[:8000]
        payload["status"] = "edited"
        payload["edited_by"] = user_id
        payload["edited_at"] = datetime.now(UTC).isoformat()
    if body.status is not None:
        payload["status"] = body.status
    if not payload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields to update.")

    def _update() -> dict[str, Any] | None:
        res = (
            svc.table("rfp_answers")
            .update(payload)
            .eq("id", answer_id)
            .eq("rfp_id", rfp_id)
            .eq("org_id", org_id)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None

    row = await asyncio.to_thread(_update)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Answer not found.")
    return row


@router.post("/rfp/{rfp_id}/answers/{req_id}/regenerate")
async def regenerate_answer(
    rfp_id: str,
    req_id: str,
    legal_feedback: str | None = Body(default=None, embed=True),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _user_id, _ = _require_org(current_user)
    await _send_inngest(
        "rfp/regenerate-answer",
        {
            "rfp_id": rfp_id,
            "org_id": org_id,
            "requirement_id": req_id,
            "legal_feedback": legal_feedback,
        },
    )
    return {"status": "queued", "requirement_id": req_id}


# ── Approval gates ───────────────────────────────────────────────────────


@router.post("/rfp/{rfp_id}/approve-rep")
async def approve_rep(
    rfp_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Rep approved the answer package. If legal_required=true, transition to
    awaiting_legal_review; else jump to finalizing."""
    org_id, user_id, _ = _require_org(current_user)
    svc = get_service_client()

    row = await _fetch_rfp_row(rfp_id, org_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "RFP not found.")
    legal_required = bool(row.get("legal_required", True))

    next_status = "awaiting_legal_review" if legal_required else "finalizing"

    def _update() -> None:
        # Stamp the row + close any open rep approval gate.
        svc.table("rfp_responses").update(
            {
                "status": next_status,
                "approved_by_rep_at": datetime.now(UTC).isoformat(),
                "approved_by_rep_user_id": user_id,
            }
        ).eq("id", rfp_id).execute()
        svc.table("rfp_approvals").update(
            {
                "status": "approved",
                "reviewer_user_id": user_id,
                "decided_at": datetime.now(UTC).isoformat(),
            }
        ).eq("rfp_id", rfp_id).eq("gate_type", "rep").eq("status", "pending").execute()
        # Mass-mark any draft answers as approved (rep edits stay 'edited').
        svc.table("rfp_answers").update({"status": "approved"}).eq("rfp_id", rfp_id).eq(
            "status", "draft"
        ).execute()

    await asyncio.to_thread(_update)

    await _send_inngest(
        "rfp/rep-approved" if legal_required else "rfp/finalize",
        {"rfp_id": rfp_id, "org_id": org_id, "triggered_by_user_id": user_id},
    )
    return {"status": next_status}


@router.post("/rfp/{rfp_id}/approve-legal")
async def approve_legal(
    rfp_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, _ = _require_org(current_user)
    svc = get_service_client()

    def _update() -> None:
        svc.table("rfp_responses").update(
            {
                "status": "finalizing",
                "approved_by_legal_at": datetime.now(UTC).isoformat(),
                "approved_by_legal_user_id": user_id,
                "legal_rejection_notes": None,
            }
        ).eq("id", rfp_id).eq("org_id", org_id).execute()
        svc.table("rfp_approvals").update(
            {
                "status": "approved",
                "reviewer_user_id": user_id,
                "decided_at": datetime.now(UTC).isoformat(),
            }
        ).eq("rfp_id", rfp_id).eq("gate_type", "legal").eq("status", "pending").execute()

    await asyncio.to_thread(_update)
    await _send_inngest(
        "rfp/legal-approved",
        {"rfp_id": rfp_id, "org_id": org_id, "triggered_by_user_id": user_id},
    )
    return {"status": "finalizing"}


@router.post("/rfp/{rfp_id}/reject-legal")
async def reject_legal(
    rfp_id: str,
    body: LegalRejectRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, _ = _require_org(current_user)
    svc = get_service_client()

    def _update() -> None:
        svc.table("rfp_responses").update(
            {
                "status": "legal_rejected",
                "legal_rejection_notes": body.notes[:4000],
            }
        ).eq("id", rfp_id).eq("org_id", org_id).execute()
        # Mark the rejected answer rows so the agent re-drafts them.
        if body.rejected_requirement_ids:
            svc.table("rfp_answers").update({"status": "rejected"}).in_(
                "requirement_id", body.rejected_requirement_ids
            ).eq("rfp_id", rfp_id).execute()
        svc.table("rfp_approvals").insert(
            {
                "org_id": org_id,
                "rfp_id": rfp_id,
                "gate_type": "legal",
                "status": "rejected",
                "reviewer_user_id": user_id,
                "decided_at": datetime.now(UTC).isoformat(),
                "notes": body.notes[:4000],
                "rejected_requirement_ids": body.rejected_requirement_ids,
            }
        ).execute()

    await asyncio.to_thread(_update)
    await _send_inngest(
        "rfp/legal-rejected",
        {"rfp_id": rfp_id, "org_id": org_id, "triggered_by_user_id": user_id},
    )
    return {"status": "legal_rejected"}


# ── Download ─────────────────────────────────────────────────────────────


@router.get("/rfp/{rfp_id}/download")
async def download_rfp(
    rfp_id: str,
    kind: Literal["filled", "summary"] = Query(default="summary"),
    current_user: dict = Depends(verify_jwt),
):
    org_id, _user_id, _ = _require_org(current_user)
    row = await _fetch_rfp_row(rfp_id, org_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "RFP not found.")
    if row.get("status") != "ready":
        raise HTTPException(status.HTTP_409_CONFLICT, "RFP response not ready.")

    path: str | None = None
    if kind == "filled":
        path = row.get("filled_output_storage_path")
    if not path:
        # Fallback to summary (xlsx-only RFPs always also produce summary)
        path = row.get("summary_output_storage_path") or row.get("output_file_path")
    if not path:
        raise HTTPException(status.HTTP_410_GONE, "Generated file no longer available.")

    signed = await rfp_storage.mint_signed_url(path)
    if not signed:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Failed to mint download URL.")
    return RedirectResponse(url=signed, status_code=307)
