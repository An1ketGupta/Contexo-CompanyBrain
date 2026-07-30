"""Recruiting Agent endpoints (#20, Agent2 Day 5).

Two POSTs, one GET listing, one GET fetch:

    POST /recruiting/requisitions/generate     create draft + 5 JD variants
    POST /recruiting/requisitions/{id}/publish publish to ATS + tracker + Slack
    GET  /recruiting/requisitions              list all in org
    GET  /recruiting/requisitions/{id}         single read

Auth:
    * Creators or admins can read everything in their org.
    * Only creator (or an admin) can publish a draft they own.
    * Generate is open to any member; the org's hiring spend gate lives in
      `usage.py` already and trips for raw chat queries — we leave it there
      and don't double-gate here.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth import verify_jwt
from app.core import idempotency
from app.core.rate_limiter import recruiting_publish_limiter
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.models.recruiting import (
    AtsPosting,
    GenerateRequisitionRequest,
    GenerateRequisitionResponse,
    JdVariant,
    LinkedinSearch,
    NaukriSearch,
    NaukriTaxonomy,
    PublishRequisitionRequest,
    RequisitionRead,
    SourcingTemplate,
    SyncCandidatesResponse,
    UpdateRequisitionRequest,
)
from app.services import recruiting_agent
from app.services.recruiting import audit_log
from app.services.recruiting import candidates as candidate_sync

log = logging.getLogger(__name__)

router = APIRouter(prefix="/recruiting", tags=["recruiting"])


def _require_org(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return org_id, user_id, token


async def _user_role(token: str, user_id: str) -> str | None:
    client = get_user_client(token)
    me = await asyncio.to_thread(
        lambda: client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    return (me.data or {}).get("role") if me and me.data else None


def _coerce_linkedin_searches(raw: Any) -> list[dict[str, Any]]:
    """Bridge legacy rows (list[str]) and current rows (list[dict]) to the
    LinkedinSearch shape the API now returns. Pre-rewrite rows lack labels,
    so we synthesise a stable placeholder rather than reverse-engineering
    the URL — the legacy variant is just "Variant 1/2/3" anyway."""
    if not raw:
        return []
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if isinstance(item, str):
            out.append(
                LinkedinSearch(
                    label=f"Search variant {i + 1}",
                    url=item,
                    description="Legacy search — re-publish this requisition to get the updated, labelled variants.",
                ).model_dump()
            )
        elif isinstance(item, dict) and item.get("url"):
            out.append(LinkedinSearch(**item).model_dump())
    return out


def _coerce_naukri_searches(raw: Any) -> list[dict[str, Any]]:
    """Naukri search URLs were introduced in migration 068; there are no
    legacy rows to migrate. We still defensively handle missing keys so a
    stale draft persisted before the migration doesn't 500 on read."""
    if not raw:
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict) and item.get("url"):
            out.append(NaukriSearch(**item).model_dump())
    return out


def _coerce_naukri_taxonomy(raw: Any) -> dict[str, Any] | None:
    """Naukri taxonomy is nullable; return None when the requisition didn't
    publish to Naukri so the API consumer can branch on its presence."""
    if not raw or not isinstance(raw, dict):
        return None
    try:
        return NaukriTaxonomy(**raw).model_dump()
    except Exception:
        # Schema drift between DB and Pydantic — return None rather than 500
        # so the rest of the requisition still loads. The recruiter can
        # re-publish to regenerate the taxonomy.
        return None


def _normalise_status(raw: str | None) -> str:
    # Status was originally stored as 'Published' (capital P) before
    # migration 072 folded the enum to lowercase. Coerce both casings on
    # read so a DB that hasn't been migrated yet still validates.
    if not raw:
        return "draft"
    if raw.lower() == "published":
        return "published"
    return raw


def _to_read(row: dict[str, Any]) -> dict[str, Any]:
    """Coerce a DB row into the shape RequisitionRead expects."""
    return {
        "id": row["id"],
        "org_id": row["org_id"],
        "created_by": row["created_by"],
        "role_request": row["role_request"],
        "seniority_level": row.get("seniority_level"),
        "disclosed_compensation": row.get("disclosed_compensation"),
        "interview_details": row.get("interview_details"),
        "stack": row.get("stack"),
        "working_hours": row.get("working_hours"),
        "context_notes": row.get("context_notes"),
        "location": row.get("location"),
        "department": row.get("department"),
        # Pre-migration rows default to True (matches column default).
        "grounded": row.get("grounded") if row.get("grounded") is not None else True,
        "jd_variants": [JdVariant(**v).model_dump() for v in row.get("jd_variants") or []],
        "selected_variant_index": row.get("selected_variant_index"),
        "ats_platform": row.get("ats_platform"),
        "ats_job_id": row.get("ats_job_id"),
        "ats_url": row.get("ats_url"),
        "ats_postings": [
            AtsPosting(**p).model_dump() for p in row.get("ats_postings") or []
        ],
        "notion_tracker_url": row.get("notion_tracker_url"),
        "sourcing_templates": [
            SourcingTemplate(**t).model_dump() for t in row.get("sourcing_templates") or []
        ],
        "linkedin_search_urls": _coerce_linkedin_searches(
            row.get("linkedin_search_urls")
        ),
        "naukri_search_urls": _coerce_naukri_searches(row.get("naukri_search_urls")),
        "naukri_taxonomy": _coerce_naukri_taxonomy(row.get("naukri_taxonomy")),
        "hiring_manager_email": row.get("hiring_manager_email"),
        "slack_channel": row.get("slack_channel"),
        "slack_post_error": row.get("slack_post_error"),
        "status": _normalise_status(row.get("status")),
        "error_message": row.get("error_message"),
        "created_at": row["created_at"],
        "published_at": row.get("published_at"),
        "notion_candidates_db_id": row.get("notion_candidates_db_id"),
        "candidates_last_synced_at": row.get("candidates_last_synced_at"),
        "candidates_last_sync_error": row.get("candidates_last_sync_error"),
        "hiring_completed_at": row.get("hiring_completed_at"),
        "archived_at": row.get("archived_at"),
        "archived_by": row.get("archived_by"),
    }


# ── Generate ────────────────────────────────────────────────────────────────


@router.post(
    "/requisitions/generate",
    response_model=GenerateRequisitionResponse,
)
async def generate_requisition(
    body: GenerateRequisitionRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, _ = _require_org(current_user)

    try:
        row = await recruiting_agent.generate_job_requisition(
            org_id=org_id,
            user_id=user_id,
            role_request=body.role_request,
            location=body.location,
            department=body.department,
            seniority_level=body.seniority_level,
            disclosed_compensation=body.disclosed_compensation,
            interview_details=body.interview_details,
            stack=body.stack,
            working_hours=body.working_hours,
            context_notes=body.context_notes,
        )
    except RuntimeError as exc:
        # JD synthesis problems are user-facing — we want a 502 rather than
        # a generic 500 so the UI can render "AI couldn't write a JD; try
        # rephrasing".
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return {
        "id": row["id"],
        "role_request": row["role_request"],
        "jd_variants": [JdVariant(**v).model_dump() for v in row.get("jd_variants") or []],
        "sources": row.get("sources") or [],
        "grounded": bool(row.get("grounded")),
        "created_at": row["created_at"],
    }


# ── Publish ─────────────────────────────────────────────────────────────────


@router.post(
    "/requisitions/{requisition_id}/publish",
    response_model=RequisitionRead,
    dependencies=[Depends(recruiting_publish_limiter)],
)
async def publish_requisition(
    requisition_id: str,
    body: PublishRequisitionRequest,
    request: Request,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)

    # Idempotency: same Idempotency-Key + same body → return cached response
    # instead of re-publishing. Prevents double job creation in the ATS on
    # double-click / proxy retry.
    hit = await idempotency.check_and_reserve(
        org_id=org_id,
        request=request,
        endpoint=f"recruiting.publish:{requisition_id}",
        body=body.model_dump(),
    )
    if hit is not None:
        return hit.response_body

    # Ownership check: creator or admin only.
    svc = get_service_client()

    def _fetch_owner() -> dict[str, Any] | None:
        res = (
            svc.table("job_requisitions")
            .select("created_by, status, archived_at")
            .eq("id", requisition_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    own_row = await asyncio.to_thread(_fetch_owner)
    if not own_row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Requisition not found.")
    if own_row["created_by"] != user_id:
        role = await _user_role(token, user_id)
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your requisition.")
    if own_row.get("archived_at"):
        # An archived draft is retired; pushing it to the ATS would create a
        # live job posting nothing in the UI points at.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This requisition is archived. Restore it before publishing.",
        )

    try:
        updated = await recruiting_agent.publish_requisition(
            org_id=org_id,
            user_id=user_id,
            requisition_id=requisition_id,
            selected_variant_index=body.selected_variant_index,
            ats_platforms=body.ats_platforms,
            hiring_manager_email=body.hiring_manager_email,
            slack_channel=body.slack_channel,
            notion_parent_page_id=body.notion_parent_page_id,
            location_override=body.location_override,
            department_override=body.department_override,
            mapping_overrides=body.mapping_overrides,
            naukri_taxonomy=body.naukri_taxonomy,
        )
    except PermissionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    response = _to_read(updated)
    await idempotency.record_response(
        org_id=org_id, request=request, response_status=200, response_body=response
    )
    return response


# ── Candidate sync (published requisitions only) ────────────────────────────


@router.post(
    "/requisitions/{requisition_id}/sync-candidates",
    response_model=SyncCandidatesResponse,
)
async def sync_candidates(
    requisition_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Pull candidates from every ATS the requisition was posted to and
    mirror them into the Notion tracker's candidate database.

    Available to any member of the requisition's org (read+write to a
    shared Notion DB is intentionally team-scoped, not just creator-scoped
    — recruiters often share the workload across the team).
    """
    org_id, user_id, _token = _require_org(current_user)
    svc = get_service_client()

    def _fetch() -> dict[str, Any] | None:
        res = (
            svc.table("job_requisitions")
            .select("status, notion_candidates_db_id")
            .eq("id", requisition_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    row = await asyncio.to_thread(_fetch)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Requisition not found.")
    if _normalise_status(row.get("status")) != "published":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Publish the requisition before syncing candidates.",
        )

    try:
        result = await candidate_sync.sync_candidates(
            org_id=org_id,
            user_id=user_id,
            requisition_id=requisition_id,
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        # "notion_candidates_db_missing" — surface as a 409 so the UI can
        # translate it into "Re-publish this requisition to set up the
        # candidate tracker" rather than a generic 500.
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return result


@router.post(
    "/requisitions/{requisition_id}/mark-hired",
    response_model=RequisitionRead,
)
async def mark_hiring_completed(
    requisition_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """HR clicks "Hiring completed" on the requisition. Stamps
    hiring_completed_at once; replaces the old flow that collected candidate
    identity + offer details inline and drove the Onboarding v2 agent."""
    org_id, _user_id, _token = _require_org(current_user)
    svc = get_service_client()

    def _fetch() -> dict[str, Any] | None:
        res = (
            svc.table("job_requisitions")
            .select("*")
            .eq("id", requisition_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    row = await asyncio.to_thread(_fetch)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Requisition not found.")

    if row.get("hiring_completed_at"):
        return _to_read(row)

    def _update() -> dict[str, Any]:
        res = (
            svc.table("job_requisitions")
            .update({"hiring_completed_at": datetime.now(UTC).isoformat()})
            .eq("id", requisition_id)
            .eq("org_id", org_id)
            .execute()
        )
        return (res.data or [{}])[0]

    updated = await asyncio.to_thread(_update)
    return _to_read({**row, **updated})


# ── Archive / Unarchive ─────────────────────────────────────────────────────


async def _set_archived(
    requisition_id: str,
    current_user: dict,
    *,
    archived: bool,
) -> dict[str, Any]:
    """Shared body for archive/unarchive. Same permission rule as delete:
    the creator, or an org admin."""
    org_id, user_id, token = _require_org(current_user)
    svc = get_service_client()

    def _fetch() -> dict[str, Any] | None:
        res = (
            svc.table("job_requisitions")
            .select("*")
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

    already = bool(row.get("archived_at"))
    if already == archived:
        return _to_read(row)

    updates: dict[str, Any] = (
        {"archived_at": datetime.now(UTC).isoformat(), "archived_by": user_id}
        if archived
        else {"archived_at": None, "archived_by": None}
    )

    def _update() -> dict[str, Any]:
        res = (
            svc.table("job_requisitions")
            .update(updates)
            .eq("id", requisition_id)
            .eq("org_id", org_id)
            .execute()
        )
        return (res.data or [{}])[0]

    updated = await asyncio.to_thread(_update)
    await audit_log.write(
        org_id=org_id,
        requisition_id=requisition_id,
        actor_user_id=user_id,
        action="archive" if archived else "unarchive",
        status="success",
        request_summary={"status": _normalise_status(row.get("status"))},
    )
    return _to_read({**row, **updated})


@router.post("/requisitions/{requisition_id}/archive", response_model=RequisitionRead)
async def archive_requisition(
    requisition_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Hide a requisition from the default listing without destroying it.
    This is the retire path for published rows, which delete refuses."""
    return await _set_archived(requisition_id, current_user, archived=True)


@router.post("/requisitions/{requisition_id}/unarchive", response_model=RequisitionRead)
async def unarchive_requisition(
    requisition_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    return await _set_archived(requisition_id, current_user, archived=False)


# ── Edit / Delete (drafts only) ─────────────────────────────────────────────


@router.patch("/requisitions/{requisition_id}", response_model=RequisitionRead)
async def update_requisition(
    requisition_id: str,
    body: UpdateRequisitionRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Edit a draft (or failed) requisition. Published rows are immutable —
    if you need to fix a published JD, edit it in the ATS directly."""
    org_id, user_id, token = _require_org(current_user)
    svc = get_service_client()

    def _fetch() -> dict[str, Any] | None:
        res = (
            svc.table("job_requisitions")
            .select("*")
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
    if _normalise_status(row.get("status")) == "published":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Published requisitions can't be edited. Update the JD in your ATS instead.",
        )

    updates: dict[str, Any] = {}
    for field_name in (
        "role_request",
        "location",
        "department",
        "seniority_level",
        "disclosed_compensation",
        "interview_details",
        "stack",
        "working_hours",
        "context_notes",
    ):
        val = getattr(body, field_name, None)
        if val is not None:
            updates[field_name] = val

    # Manual JD edit: replace one variant's text in place. Rejected alongside
    # regenerate_variants — the regenerated set would silently discard the edit.
    if body.jd_variant_edit is not None:
        if body.regenerate_variants:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "jd_variant_edit and regenerate_variants are mutually exclusive.",
            )
        variants = list(row.get("jd_variants") or [])
        idx = body.jd_variant_edit.variant_index
        if idx >= len(variants):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"variant_index {idx} out of range (requisition has {len(variants)} variants).",
            )
        variants[idx] = {**variants[idx], "text": body.jd_variant_edit.text}
        updates["jd_variants"] = variants

    # Regenerate path: rerun the agent with the merged fields and overwrite
    # the variants. We don't change `created_at` so the requisition stays in
    # its original timeline position.
    if body.regenerate_variants:
        merged = {**row, **updates}
        try:
            new_row = await recruiting_agent.generate_job_requisition(
                org_id=org_id,
                user_id=user_id,
                role_request=merged.get("role_request") or "",
                location=merged.get("location") or "",
                department=merged.get("department") or "",
                seniority_level=merged.get("seniority_level") or "mid",
                disclosed_compensation=merged.get("disclosed_compensation"),
                interview_details=merged.get("interview_details"),
                stack=merged.get("stack"),
                working_hours=merged.get("working_hours"),
                context_notes=merged.get("context_notes"),
            )
        except RuntimeError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

        # generate_job_requisition inserts a fresh row — we want to point the
        # new variants at the EXISTING requisition_id. Move them over and
        # delete the throwaway row.
        updates["jd_variants"] = new_row.get("jd_variants") or []
        updates["grounded"] = bool(new_row.get("grounded"))
        updates["status"] = "draft"
        updates["error_message"] = None
        await asyncio.to_thread(
            lambda: svc.table("job_requisitions")
            .delete()
            .eq("id", new_row["id"])
            .execute()
        )

    if not updates:
        # No-op PATCH; just return the current row.
        return _to_read(row)

    def _update() -> dict[str, Any]:
        res = (
            svc.table("job_requisitions")
            .update(updates)
            .eq("id", requisition_id)
            .eq("org_id", org_id)
            .execute()
        )
        return (res.data or [{}])[0]

    updated = await asyncio.to_thread(_update)
    await audit_log.write(
        org_id=org_id,
        requisition_id=requisition_id,
        actor_user_id=user_id,
        action="edit",
        status="success",
        request_summary={
            "fields": list(updates.keys()),
            "regenerate": body.regenerate_variants,
        },
    )
    return _to_read({**row, **updated})


@router.delete("/requisitions/{requisition_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_requisition(
    requisition_id: str,
    current_user: dict = Depends(verify_jwt),
) -> None:
    """Delete a draft or failed requisition. Published rows are immutable —
    they're our audit-trail anchor for what was posted to the ATS."""
    org_id, user_id, token = _require_org(current_user)
    svc = get_service_client()

    def _fetch() -> dict[str, Any] | None:
        res = (
            svc.table("job_requisitions")
            .select("created_by, status")
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
    if _normalise_status(row.get("status")) == "published":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Published requisitions can't be deleted (audit trail).",
        )

    await asyncio.to_thread(
        lambda: svc.table("job_requisitions")
        .delete()
        .eq("id", requisition_id)
        .eq("org_id", org_id)
        .execute()
    )
    # Audit log row carries a soft FK with no cascade, so the delete history
    # persists even though the requisition is gone.
    await audit_log.write(
        org_id=org_id,
        requisition_id=requisition_id,
        actor_user_id=user_id,
        action="delete",
        status="success",
    )


# ── Read ─────────────────────────────────────────────────────────────────────


@router.get("/requisitions")
async def list_requisitions(
    archived: bool = False,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Active requisitions by default; `?archived=true` returns the archived
    ones instead. The two sets are fetched separately so archived rows don't
    eat into the 100-row cap on the main listing.

    `archived_count` ships with both responses so the UI can label its
    Archived tab without a second round trip.
    """
    org_id, _user_id, token = _require_org(current_user)
    client = get_user_client(token)

    def _fetch() -> list[dict[str, Any]]:
        q = client.table("job_requisitions").select("*").eq("org_id", org_id)
        if archived:
            # Most-recently-archived first — that's the row you're most likely
            # to want back.
            q = q.not_.is_("archived_at", "null").order("archived_at", desc=True)
        else:
            q = q.is_("archived_at", "null").order("created_at", desc=True)
        res = q.limit(100).execute()
        return res.data or []

    def _fetch_archived_count() -> int:
        res = (
            client.table("job_requisitions")
            .select("id", count="exact", head=True)
            .eq("org_id", org_id)
            .not_.is_("archived_at", "null")
            .execute()
        )
        return res.count or 0

    rows, archived_count = await asyncio.gather(
        asyncio.to_thread(_fetch),
        asyncio.to_thread(_fetch_archived_count),
    )
    return {
        "requisitions": [_to_read(r) for r in rows],
        "archived_count": archived_count,
    }


@router.get("/requisitions/{requisition_id}", response_model=RequisitionRead)
async def get_requisition(
    requisition_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _user_id, token = _require_org(current_user)
    client = get_user_client(token)

    def _fetch() -> dict[str, Any] | None:
        res = (
            client.table("job_requisitions")
            .select("*")
            .eq("id", requisition_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    row = await asyncio.to_thread(_fetch)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Requisition not found.")
    return _to_read(row)


# ── Notion parent (org-level default for hiring trackers) ───────────────────


from pydantic import BaseModel as _BaseModel  # noqa: E402
from pydantic import Field as _Field  # noqa: E402

from app.services.integrations import notion as _notion_svc  # noqa: E402


class SetNotionParentRequest(_BaseModel):
    parent_page_id: str
    # Optional title hint — if omitted, we pull it from Notion during validation.
    parent_page_title: str | None = None


def _normalise_page_id(raw: str) -> str:
    """Accept any Notion-shaped input — URL, slug-with-ID, dashed UUID, or bare
    hex — and return the bare 32-char hex form Notion's API requires.

    Returns the input unchanged when no 32-hex run is found; the caller's
    subsequent retrieve_page will reject it cleanly with a 400.
    """
    import re

    cleaned = raw.strip().replace("-", "")
    m = re.search(r"[0-9a-fA-F]{32}", cleaned)
    return m.group(0).lower() if m else raw.strip()


@router.get("/notion-parent")
async def get_notion_parent(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Return the org's default Notion parent page for hiring trackers.

    Shape:
      {
        connected: bool,        # Notion OAuth done
        parent_id: str | null,  # configured default (may be null)
        parent_title: str | null,
        accessible: bool,       # bot can still see the parent (live check)
        accessibility_error: str | null
      }
    """
    org_id, _user_id, _token = _require_org(current_user)
    svc = get_service_client()

    def _fetch() -> dict[str, Any] | None:
        res = (
            svc.table("notion_integrations")
            .select(
                "id, default_recruiting_tracker_parent_id, "
                "default_recruiting_tracker_parent_title"
            )
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    row = await asyncio.to_thread(_fetch)
    if not row:
        return {
            "connected": False,
            "parent_id": None,
            "parent_title": None,
            "accessible": False,
            "accessibility_error": None,
        }

    parent_id = row.get("default_recruiting_tracker_parent_id")
    parent_title = row.get("default_recruiting_tracker_parent_title")
    accessible = False
    accessibility_error: str | None = None
    if parent_id:
        # Live check — catches the case where the user un-shared the page
        # in Notion after configuring it here.
        try:
            page = await _notion_svc.retrieve_page(org_id=org_id, page_id=parent_id)
            accessible = True
            # Refresh the cached title opportunistically so the chip stays
            # in sync with renames on Notion's side.
            if page.get("title") and page["title"] != parent_title:
                parent_title = page["title"]
                await asyncio.to_thread(
                    lambda: svc.table("notion_integrations")
                    .update(
                        {"default_recruiting_tracker_parent_title": parent_title}
                    )
                    .eq("org_id", org_id)
                    .execute()
                )
        except PermissionError as exc:
            accessibility_error = str(exc)
        except Exception as exc:
            accessibility_error = f"{type(exc).__name__}: {exc}"

    return {
        "connected": True,
        "parent_id": parent_id,
        "parent_title": parent_title,
        "accessible": accessible,
        "accessibility_error": accessibility_error,
    }


@router.post("/notion-parent")
async def set_notion_parent(
    body: SetNotionParentRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Set the org-level default parent. Validates the bot can write to the
    page before persisting; rejects with a 400 / 404 otherwise so the UI
    can render an actionable message."""
    org_id, _user_id, _token = _require_org(current_user)
    page_id = _normalise_page_id(body.parent_page_id)

    try:
        page = await _notion_svc.retrieve_page(org_id=org_id, page_id=page_id)
    except PermissionError as exc:
        # "notion_not_connected" or "notion_parent_not_shared" — both are
        # user-actionable, so a 400 with the code is more useful than a 500.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    title = body.parent_page_title or page.get("title") or "Untitled"

    svc = get_service_client()

    def _save() -> None:
        existing = (
            svc.table("notion_integrations")
            .select("id")
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        if not existing or not existing.data:
            # Notion isn't connected for this org but we got past retrieve_page
            # — only possible if the token was deleted mid-request. Treat as
            # not-connected; the UI re-routes to OAuth.
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail="notion_not_connected"
            )
        svc.table("notion_integrations").update(
            {
                "default_recruiting_tracker_parent_id": page_id,
                "default_recruiting_tracker_parent_title": title,
            }
        ).eq("id", existing.data["id"]).execute()

    await asyncio.to_thread(_save)
    return {
        "parent_id": page_id,
        "parent_title": title,
        "url": page.get("url"),
    }


@router.delete("/notion-parent", status_code=status.HTTP_204_NO_CONTENT)
async def clear_notion_parent(
    current_user: dict = Depends(verify_jwt),
) -> None:
    org_id, _user_id, _token = _require_org(current_user)
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("notion_integrations")
        .update(
            {
                "default_recruiting_tracker_parent_id": None,
                "default_recruiting_tracker_parent_title": None,
            }
        )
        .eq("org_id", org_id)
        .execute()
    )


# ── Slack channel (org-level default for requisition announcements) ─────────


from app.services.integrations import slack as _slack_svc  # noqa: E402


class SetSlackChannelRequest(_BaseModel):
    channel_id: str = _Field(..., min_length=1, max_length=40)
    channel_name: str | None = _Field(default=None, max_length=200)


@router.get("/slack-channel")
async def get_slack_channel(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Return the org's default Slack channel for recruiting announcements.

    Mirrors GET /recruiting/notion-parent. Shape:
      {
        connected: bool,           # Slack OAuth done
        channel_id: str | null,    # configured default (may be null)
        channel_name: str | null,
        accessible: bool,          # bot still sees the channel (live check)
        accessibility_error: str | null
      }
    """
    org_id, _user_id, _token = _require_org(current_user)
    svc = get_service_client()

    def _fetch() -> dict[str, Any] | None:
        res = (
            svc.table("slack_integrations")
            .select(
                "id, default_recruiting_slack_channel_id, "
                "default_recruiting_slack_channel_name"
            )
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    row = await asyncio.to_thread(_fetch)
    if not row:
        return {
            "connected": False,
            "channel_id": None,
            "channel_name": None,
            "accessible": False,
            "accessibility_error": None,
        }

    channel_id = row.get("default_recruiting_slack_channel_id")
    channel_name = row.get("default_recruiting_slack_channel_name")
    accessible = False
    accessibility_error: str | None = None
    if channel_id:
        # Live check via the cached channel list — picks up un-invites and
        # archived channels without a dedicated conversations.info call.
        try:
            channels = await _slack_svc.list_channels(org_id=org_id)
            match = next((c for c in channels if c.get("id") == channel_id), None)
            if match is None:
                accessibility_error = (
                    "Bot was removed from this channel or it was archived. "
                    "Pick a different channel."
                )
            else:
                accessible = True
                fresh_name = match.get("name")
                if fresh_name and fresh_name != channel_name:
                    channel_name = fresh_name
                    await asyncio.to_thread(
                        lambda: svc.table("slack_integrations")
                        .update(
                            {"default_recruiting_slack_channel_name": channel_name}
                        )
                        .eq("org_id", org_id)
                        .execute()
                    )
        except Exception as exc:  # noqa: BLE001 — surface to UI verbatim
            accessibility_error = f"{type(exc).__name__}: {exc}"

    return {
        "connected": True,
        "channel_id": channel_id,
        "channel_name": channel_name,
        "accessible": accessible,
        "accessibility_error": accessibility_error,
    }


@router.post("/slack-channel")
async def set_slack_channel(
    body: SetSlackChannelRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Set the org-level default channel. Validates the bot can still see the
    channel (i.e. it's in the channel list returned by Slack) before saving.
    """
    org_id, _user_id, _token = _require_org(current_user)
    svc = get_service_client()

    def _existing() -> dict[str, Any] | None:
        res = (
            svc.table("slack_integrations")
            .select("id")
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    existing = await asyncio.to_thread(_existing)
    if not existing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="slack_not_connected"
        )

    channels = await _slack_svc.list_channels(org_id=org_id)
    match = next((c for c in channels if c.get("id") == body.channel_id), None)
    if match is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                "slack_channel_not_visible: invite the bot to the channel and "
                "try again."
            ),
        )
    # chat.postMessage requires bot membership. For public channels the bot
    # can self-join via conversations.join (no user action needed). For
    # private channels Slack has no self-join path — the wall stays.
    if not match.get("is_member"):
        if match.get("is_private"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="slack_bot_not_in_private_channel",
            )
        try:
            await _slack_svc.join_channel(org_id=org_id, channel_id=body.channel_id)
        except PermissionError as exc:
            # "slack_scope_upgrade_required" if the install pre-dates
            # channels:join; the UI reads this and prompts a re-auth.
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, detail=str(exc)
            ) from exc

    channel_name = body.channel_name or match.get("name") or "channel"

    await asyncio.to_thread(
        lambda: svc.table("slack_integrations")
        .update(
            {
                "default_recruiting_slack_channel_id": body.channel_id,
                "default_recruiting_slack_channel_name": channel_name,
            }
        )
        .eq("id", existing["id"])
        .execute()
    )
    return {"channel_id": body.channel_id, "channel_name": channel_name}


@router.delete("/slack-channel", status_code=status.HTTP_204_NO_CONTENT)
async def clear_slack_channel(
    current_user: dict = Depends(verify_jwt),
) -> None:
    org_id, _user_id, _token = _require_org(current_user)
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("slack_integrations")
        .update(
            {
                "default_recruiting_slack_channel_id": None,
                "default_recruiting_slack_channel_name": None,
            }
        )
        .eq("org_id", org_id)
        .execute()
    )
