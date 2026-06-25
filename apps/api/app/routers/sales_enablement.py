"""Sales Enablement endpoints (#22, Agent2 Day 5).

Two distinct surfaces sharing only the auth scaffolding:

  Pre-call brief:
    POST /sales/precall-brief                            in-memory, not persisted

  RFP:
    POST /sales/rfp/upload                               multipart upload + extract
    PATCH /sales/rfp/{id}/requirements                   rep edits the parsed list
    POST /sales/rfp/{id}/generate                        run matching + build docx
    GET  /sales/rfp/{id}                                 read
    GET  /sales/rfp/{id}/download                        signed URL or stream
    GET  /sales/rfp                                      list
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.models.sales_enablement import (
    PrecallBriefRequest,
    PrecallBriefResponse,
    RfpResponseRead,
    RfpRequirement,
    UpdateRequirementsRequest,
)
from app.services import precall_brief, rfp_response

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sales", tags=["sales"])

# 25 MB upload cap. RFPs are usually <5 MB but enterprise security
# questionnaires can balloon with embedded screenshots.
_MAX_RFP_BYTES = 25 * 1024 * 1024


def _require_org(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return org_id, user_id, token


# ── Pre-call brief ──────────────────────────────────────────────────────────


@router.post("/precall-brief", response_model=PrecallBriefResponse)
async def post_precall_brief(
    body: PrecallBriefRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _user_id, _ = _require_org(current_user)
    try:
        return await precall_brief.generate_precall_brief(
            org_id=org_id,
            prospect_name=body.prospect_name,
            company=body.company,
            notes=body.notes,
        )
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


# ── RFP ─────────────────────────────────────────────────────────────────────


@router.post("/rfp/upload", response_model=RfpResponseRead)
async def upload_rfp(
    file: UploadFile = File(...),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, _ = _require_org(current_user)

    # Read with bounded budget — defends against multi-GB streamed uploads
    # that would otherwise exhaust worker memory before FastAPI gets to size-
    # check downstream.
    content = await file.read(_MAX_RFP_BYTES + 1)
    if len(content) > _MAX_RFP_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"RFP file exceeds {_MAX_RFP_BYTES // (1024 * 1024)} MB.",
        )

    try:
        row = await rfp_response.extract_requirements(
            org_id=org_id,
            user_id=user_id,
            file_bytes=content,
            filename=file.filename or "rfp",
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return _to_rfp_read(row)


@router.patch("/rfp/{rfp_id}/requirements", response_model=RfpResponseRead)
async def update_rfp_requirements(
    rfp_id: str,
    body: UpdateRequirementsRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, _ = _require_org(current_user)
    svc = get_service_client()

    def _update() -> dict[str, Any] | None:
        # Ownership guard via the SQL `eq` — RLS would catch this too but
        # the explicit org_id filter keeps the contract obvious.
        res = (
            svc.table("rfp_responses")
            .update(
                {
                    "requirements": [r.model_dump() for r in body.requirements],
                    "status": "reviewed",
                }
            )
            .eq("id", rfp_id)
            .eq("org_id", org_id)
            .eq("created_by", user_id)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None

    row = await asyncio.to_thread(_update)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="RFP not found or not yours.")
    return _to_rfp_read(row)


@router.post("/rfp/{rfp_id}/generate", response_model=RfpResponseRead)
async def generate_rfp(
    rfp_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _user_id, _ = _require_org(current_user)
    try:
        row = await rfp_response.generate_response(org_id=org_id, rfp_id=rfp_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return _to_rfp_read(row)


@router.get("/rfp/{rfp_id}", response_model=RfpResponseRead)
async def get_rfp(
    rfp_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _user_id, token = _require_org(current_user)
    client = get_user_client(token)

    def _fetch() -> dict[str, Any] | None:
        res = (
            client.table("rfp_responses")
            .select("*")
            .eq("id", rfp_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    row = await asyncio.to_thread(_fetch)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "RFP not found.")
    return _to_rfp_read(row)


@router.get("/rfp/{rfp_id}/download")
async def download_rfp(
    rfp_id: str,
    current_user: dict = Depends(verify_jwt),
):
    org_id, _user_id, _ = _require_org(current_user)
    svc = get_service_client()

    def _fetch() -> dict[str, Any] | None:
        res = (
            svc.table("rfp_responses")
            .select("output_file_path, source_filename, status")
            .eq("id", rfp_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    row = await asyncio.to_thread(_fetch)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "RFP not found.")
    if row.get("status") != "ready" or not row.get("output_file_path"):
        raise HTTPException(status.HTTP_409_CONFLICT, "RFP response not ready.")
    path = row["output_file_path"]
    if not os.path.exists(path):
        # Tempfiles can be GC'd on a worker restart. We'd rather a clear
        # 410 than a 500 so the UI can prompt "regenerate".
        raise HTTPException(status.HTTP_410_GONE, "Generated file no longer available.")

    base = (row.get("source_filename") or "rfp").rsplit(".", 1)[0]
    return FileResponse(
        path,
        filename=f"{base}_response.docx",
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
    )


@router.get("/rfp")
async def list_rfps(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _user_id, token = _require_org(current_user)
    client = get_user_client(token)

    def _fetch() -> list[dict[str, Any]]:
        res = (
            client.table("rfp_responses")
            .select("*")
            .eq("org_id", org_id)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        return res.data or []

    rows = await asyncio.to_thread(_fetch)
    return {"rfps": [_to_rfp_read(r) for r in rows]}


def _to_rfp_read(row: dict[str, Any]) -> dict[str, Any]:
    """Coerce DB row to RfpResponseRead shape (with safe defaults)."""
    return {
        "id": row["id"],
        "org_id": row["org_id"],
        "created_by": row["created_by"],
        "source_filename": row["source_filename"],
        "requirements": [RfpRequirement(**r).model_dump() for r in row.get("requirements") or []],
        "matches": row.get("matches") or [],
        "status": row.get("status") or "extracted",
        "output_file_path": row.get("output_file_path"),
        "gap_count": int(row.get("gap_count") or 0),
        "error_message": row.get("error_message"),
        "created_at": row["created_at"],
        "generated_at": row.get("generated_at"),
    }
