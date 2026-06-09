"""Internal auth hooks called by the Next.js layer after Supabase signup.

These endpoints are called server-to-server from the Next.js complete-signup
flow; they are HMAC-signed with INTERNAL_EMAIL_SECRET so they can't be
invoked by a random client. We keep the email dispatch on the FastAPI side
so all Inngest event emission lives in one process.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.config import get_settings
from app.database import get_service_client
from app.observability import get_logger

log = get_logger(__name__)

router = APIRouter(tags=["auth-hooks"])


class WelcomeHook(BaseModel):
    user_id: str = Field(..., min_length=8, max_length=64)
    org_id: str = Field(..., min_length=8, max_length=64)


def _verify_internal_signature(raw_body: bytes, signature: str | None) -> None:
    settings = get_settings()
    if not settings.internal_email_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal hook secret not configured.",
        )
    if not signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing signature.",
        )
    expected = hmac.new(
        settings.internal_email_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bad signature.",
        )


@router.post("/auth/internal/welcome")
async def welcome_hook(
    request: Request,
    x_internal_signature: str | None = Header(default=None, alias="X-Internal-Signature"),
) -> dict[str, bool]:
    """Fire a welcome email for a freshly-signed-up user.

    Idempotent: the email worker checks email_events before sending, and the
    table's unique partial index on (user_id, event_type) WHERE dedupe_key IS
    NULL prevents a duplicate row.
    """
    raw = await request.body()
    _verify_internal_signature(raw, x_internal_signature)

    try:
        payload = WelcomeHook.model_validate_json(raw)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bad payload: {exc}",
        ) from exc

    svc = get_service_client()
    user_row = await asyncio.to_thread(
        lambda: svc.table("users")
        .select("display_name, org_id")
        .eq("id", payload.user_id)
        .maybe_single()
        .execute()
    )
    if not user_row or not user_row.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    org_row = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select("name")
        .eq("id", payload.org_id)
        .maybe_single()
        .execute()
    )
    if not org_row or not org_row.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found.")

    try:
        au = await asyncio.to_thread(
            lambda: svc.auth.admin.get_user_by_id(payload.user_id)
        )
        email = getattr(getattr(au, "user", None), "email", None)
    except Exception as exc:
        log.warning("welcome_auth_lookup_failed", user_id=payload.user_id, error=str(exc))
        email = None

    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Couldn't resolve email for user.",
        )

    settings = get_settings()
    display_name = (user_row.data or {}).get("display_name") or ""
    first_name = display_name.split(" ")[0] if display_name else None

    from app.services.email import send_email_event

    await send_email_event(
        event_type="welcome",
        to=email,
        user_id=payload.user_id,
        org_id=payload.org_id,
        data={
            "first_name": first_name,
            "org_name": org_row.data["name"],
            "app_url": settings.app_url,
        },
    )
    return {"queued": True}
