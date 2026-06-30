"""Interview Kit endpoints (extends Recruiting Agent #20).

Endpoints:
    POST   /recruiting/requisitions/{rid}/interview-kits/generate  fire agent
    GET    /recruiting/requisitions/{rid}/interview-kits           list
    GET    /recruiting/interview-kits/{kid}                        single
    PATCH  /recruiting/interview-kits/{kid}                        edit panels/rubric/refs
    POST   /recruiting/interview-kits/{kid}/publish                lock the kit
    DELETE /recruiting/interview-kits/{kid}                        delete (admin)

Auth:
    Reads — any member of the org (interviewers need to see the kit).
    Generate / edit / publish — requisition creator or admin.
    Delete — admin only.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import inngest as _inngest_pkg
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.inngest.client import get_inngest_client
from app.services.agents.interview_kit import storage as kit_storage
from app.services.agents.interview_kit.schemas import (
    GenerateInterviewKitRequest,
    InterviewKitRead,
    UpdateInterviewKitRequest,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/recruiting", tags=["recruiting", "interview-kits"])


def _require_org(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return org_id, user_id, token


async def _user_role(token: str, user_id: str) -> str | None:
    client = get_user_client(token)
    res = await asyncio.to_thread(
        lambda: client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    return (res.data or {}).get("role") if res and res.data else None


async def _assert_can_edit_requisition(
    *, org_id: str, user_id: str, token: str, requisition_id: str
) -> dict[str, Any]:
    svc = get_service_client()

    def _fetch() -> dict[str, Any] | None:
        res = (
            svc.table("job_requisitions")
            .select("created_by, status, jd_variants, selected_variant_index")
            .eq("id", requisition_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    row = await asyncio.to_thread(_fetch)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Requisition not found.")
    if row["created_by"] != user_id:
        role = await _user_role(token, user_id)
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your requisition.")
    return row


def _row_to_read(row: dict[str, Any]) -> dict[str, Any]:
    """Coerce DB row into the InterviewKitRead shape. The JSONB columns are
    already lists/dicts; we just stamp defaults for older rows missing keys."""
    return {
        "id": row["id"],
        "org_id": row["org_id"],
        "requisition_id": row["requisition_id"],
        "created_by": row["created_by"],
        "run_id": row.get("run_id"),
        "jd_variant_index": row.get("jd_variant_index"),
        "role_title": row.get("role_title"),
        "seniority_level": row.get("seniority_level"),
        "competencies": row.get("competencies") or [],
        "panels": row.get("panels") or [],
        "scorecard": row.get("scorecard") or [],
        "reference_questions": row.get("reference_questions") or [],
        "sources": row.get("sources") or [],
        "status": row.get("status") or "draft",
        "error_message": row.get("error_message"),
        "generated_at": row.get("generated_at"),
        "published_at": row.get("published_at"),
        "created_at": row["created_at"],
        "updated_at": row.get("updated_at") or row["created_at"],
    }


# ── Generate ────────────────────────────────────────────────────────────────


@router.post(
    "/requisitions/{requisition_id}/interview-kits/generate",
    response_model=InterviewKitRead,
)
async def generate_interview_kit(
    requisition_id: str,
    body: GenerateInterviewKitRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    req = await _assert_can_edit_requisition(
        org_id=org_id, user_id=user_id, token=token, requisition_id=requisition_id
    )
    variants = req.get("jd_variants") or []
    if not variants:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Requisition has no JD variants. Generate the JD first.",
        )

    # Default to the published variant; fall back to 0.
    variant_idx = body.jd_variant_index
    if variant_idx is None:
        variant_idx = req.get("selected_variant_index") or 0
    if variant_idx < 0 or variant_idx >= len(variants):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"jd_variant_index out of range (have {len(variants)} variants).",
        )

    # We pre-create the agent_runs row id ourselves so the kit row can link
    # to it before the Inngest worker fires. The InterviewKitAgent constructor
    # accepts a run_id and uses it on create_run_record().
    import uuid

    run_id = str(uuid.uuid4())

    kit_id = await kit_storage.create_kit_row(
        org_id=org_id,
        requisition_id=requisition_id,
        created_by=user_id,
        run_id=run_id,
        jd_variant_index=variant_idx,
    )

    inngest_client = get_inngest_client()
    await inngest_client.send(
        _inngest_pkg.Event(
            name="recruiting/interview-kit-generate",
            data={
                "kit_id": kit_id,
                "org_id": org_id,
                "requisition_id": requisition_id,
                "jd_variant_index": variant_idx,
                "triggered_by_user_id": user_id,
                "run_id": run_id,
            },
        )
    )

    row = await kit_storage.fetch_kit(org_id=org_id, kit_id=kit_id)
    if not row:
        # Defensive — we just inserted it; an immediate 404 means an org-id mismatch.
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Kit row vanished after insert.")
    return _row_to_read(row)


# ── List by requisition ────────────────────────────────────────────────────


@router.get(
    "/requisitions/{requisition_id}/interview-kits",
    response_model=list[InterviewKitRead],
)
async def list_kits(
    requisition_id: str,
    current_user: dict = Depends(verify_jwt),
) -> list[dict[str, Any]]:
    org_id, _user_id, _token = _require_org(current_user)
    rows = await kit_storage.list_kits_for_requisition(
        org_id=org_id, requisition_id=requisition_id
    )
    return [_row_to_read(r) for r in rows]


# ── Single ─────────────────────────────────────────────────────────────────


@router.get(
    "/interview-kits/{kit_id}",
    response_model=InterviewKitRead,
)
async def get_kit(
    kit_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _user_id, _token = _require_org(current_user)
    row = await kit_storage.fetch_kit(org_id=org_id, kit_id=kit_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interview kit not found.")
    return _row_to_read(row)


# ── Edit ───────────────────────────────────────────────────────────────────


@router.patch(
    "/interview-kits/{kit_id}",
    response_model=InterviewKitRead,
)
async def update_kit(
    kit_id: str,
    body: UpdateInterviewKitRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    row = await kit_storage.fetch_kit(org_id=org_id, kit_id=kit_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interview kit not found.")

    if row["created_by"] != user_id:
        role = await _user_role(token, user_id)
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your kit.")

    if row["status"] == "generating":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Kit is still generating. Wait until it's ready before editing.",
        )

    patch: dict[str, Any] = {}
    if body.competencies is not None:
        patch["competencies"] = [c.model_dump() for c in body.competencies]
    if body.panels is not None:
        patch["panels"] = [p.model_dump() for p in body.panels]
    if body.scorecard is not None:
        patch["scorecard"] = [s.model_dump() for s in body.scorecard]
    if body.reference_questions is not None:
        patch["reference_questions"] = [r.model_dump() for r in body.reference_questions]
    if body.role_title is not None:
        patch["role_title"] = body.role_title
    if body.seniority_level is not None:
        patch["seniority_level"] = body.seniority_level

    if not patch:
        return _row_to_read(row)

    # Editing a published kit flips it back to 'ready' so panelists see the
    # "edited after publish" state. Recruiter re-publishes when satisfied.
    if row["status"] == "published":
        patch["status"] = "ready"
        patch["published_at"] = None

    await kit_storage.update_kit_row(kit_id=kit_id, patch=patch)
    refreshed = await kit_storage.fetch_kit(org_id=org_id, kit_id=kit_id)
    return _row_to_read(refreshed or row)


# ── Publish ────────────────────────────────────────────────────────────────


@router.post(
    "/interview-kits/{kit_id}/publish",
    response_model=InterviewKitRead,
)
async def publish_kit(
    kit_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    row = await kit_storage.fetch_kit(org_id=org_id, kit_id=kit_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interview kit not found.")
    if row["created_by"] != user_id:
        role = await _user_role(token, user_id)
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your kit.")
    if row["status"] not in ("ready", "published"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot publish kit with status '{row['status']}'.",
        )

    await kit_storage.update_kit_row(
        kit_id=kit_id,
        patch={
            "status": "published",
            "published_at": datetime.now(UTC).isoformat(),
        },
    )
    refreshed = await kit_storage.fetch_kit(org_id=org_id, kit_id=kit_id)
    return _row_to_read(refreshed or row)


# ── Delete ─────────────────────────────────────────────────────────────────


@router.delete("/interview-kits/{kit_id}")
async def delete_kit(
    kit_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, str]:
    org_id, user_id, token = _require_org(current_user)
    role = await _user_role(token, user_id)
    if role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only.")
    row = await kit_storage.fetch_kit(org_id=org_id, kit_id=kit_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interview kit not found.")
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("interview_kits").delete().eq("id", kit_id).execute()
    )
    return {"status": "deleted"}
