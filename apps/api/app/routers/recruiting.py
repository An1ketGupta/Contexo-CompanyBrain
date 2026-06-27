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
    PublishRequisitionRequest,
    RequisitionRead,
    SourcingTemplate,
    UpdateRequisitionRequest,
)
from app.services import recruiting_agent
from app.services.recruiting import audit_log

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
        "linkedin_search_urls": row.get("linkedin_search_urls") or [],
        "hiring_manager_email": row.get("hiring_manager_email"),
        "slack_channel": row.get("slack_channel"),
        "status": row.get("status") or "draft",
        "error_message": row.get("error_message"),
        "created_at": row["created_at"],
        "published_at": row.get("published_at"),
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
            .select("created_by, status")
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
    if row.get("status") == "published":
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
        "context_notes",
    ):
        val = getattr(body, field_name, None)
        if val is not None:
            updates[field_name] = val

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
    if row.get("status") == "published":
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
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _user_id, token = _require_org(current_user)
    client = get_user_client(token)

    def _fetch() -> list[dict[str, Any]]:
        res = (
            client.table("job_requisitions")
            .select("*")
            .eq("org_id", org_id)
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )
        return res.data or []

    rows = await asyncio.to_thread(_fetch)
    return {"requisitions": [_to_read(r) for r in rows]}


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
