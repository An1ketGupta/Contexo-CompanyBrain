"""In-app notifications endpoints (Migration 036).

All endpoints are user-scoped. Inserts come from background functions only,
so this router has no POST/create — just list, mark-read, and unread-count.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import verify_jwt
from app.database import get_user_client
from app.services import notifications as nsvc

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(
    limit: int = Query(default=nsvc.DEFAULT_LIST_LIMIT, ge=1, le=nsvc.MAX_LIST_LIMIT),
    unread_only: bool = Query(default=False),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    client = get_user_client(current_user["token"])
    rows = await nsvc.list_notifications(
        client=client, limit=limit, unread_only=unread_only
    )
    return {"notifications": rows}


@router.get("/unread-count")
async def get_unread_count(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, int]:
    """Cheap query — used to drive the bell badge. Polled on focus."""
    client = get_user_client(current_user["token"])
    count = await nsvc.unread_count(client=client)
    return {"count": count}


@router.post("/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, bool]:
    client = get_user_client(current_user["token"])
    updated = await nsvc.mark_read(client=client, notification_id=notification_id)
    if not updated:
        # Either already read or doesn't belong to the user. Both look like
        # 404 from the caller's perspective — no point distinguishing.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found.",
        )
    return {"ok": True}


@router.post("/mark-all-read")
async def mark_all_notifications_read(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, int]:
    client = get_user_client(current_user["token"])
    updated = await nsvc.mark_all_read(client=client)
    return {"updated": updated}
