"""Sales Enablement endpoints — Pre-call brief only.

RFP endpoints moved to routers/rfp.py (RFP Agent, migration 077). This file
now owns only the pre-call brief surface, which is in-memory and not
persisted, so the move kept it cleanly separable.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import verify_jwt
from app.errors import NoOrganization
from app.models.sales_enablement import (
    PrecallBriefRequest,
    PrecallBriefResponse,
)
from app.services import precall_brief

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sales", tags=["sales"])


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


# RFP endpoints live in routers/rfp.py — see RFP Agent (migration 077).
