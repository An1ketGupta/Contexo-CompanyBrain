"""Usage snapshot endpoint — powers the sidebar quota meter."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends

from app.auth import verify_jwt
from app.errors import NoOrganization
from app.services.rate_limit import get_usage_snapshot

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("/me")
async def get_my_usage(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id = current_user.get("org_id")
    if not org_id:
        raise NoOrganization("No organization found. Please sign out and sign back in.")

    snap = await get_usage_snapshot(org_id)
    reset_at = datetime.now(UTC) + timedelta(seconds=snap.seconds_until_reset)
    return {
        "plan": snap.plan,
        "used": snap.used,
        "limit": snap.limit,
        "reset_at": reset_at.isoformat(),
        "seconds_until_reset": snap.seconds_until_reset,
        # `unlimited` is a UX hint — `limit=null` already implies it but the
        # meter renders differently (no bar) and switching on `null` reads worse.
        "unlimited": snap.limit is None,
        "source": snap.source,
    }
