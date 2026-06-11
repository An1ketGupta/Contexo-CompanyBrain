"""V3 Day 4 #62 — shareable output links.

Three surfaces:

  * POST   /chat/messages/{id}/share   — create (or return existing) public token. Auth.
  * DELETE /chat/messages/{id}/share   — revoke the active token. Auth.
  * GET    /share/{token}              — public, no auth. Renders the shared output.

Why a dedicated router (not bolted onto chat.py):
  * The public GET runs through the service-role client because RLS would
    block anon — keeping it in its own file makes the trust boundary obvious.
  * The two prefixes (/chat and /share) are mounted from a single APIRouter
    to keep route grouping consistent in OpenAPI.

The org admin can flip `organizations.allow_output_sharing` to false to
disable share creation org-wide. Existing tokens keep working until revoked
individually — the toggle gates new shares, not historical ones.
"""
from __future__ import annotations

import asyncio
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth import verify_jwt
from app.config import get_settings
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization, RateLimited
from app.observability import get_logger
from app.services.rate_limit import _sliding_window_check  # internal helper, reused intentionally

log = get_logger(__name__)


router = APIRouter(tags=["sharing"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_org(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return org_id, user_id, token


def _public_share_url(token: str) -> str:
    base = get_settings().app_url.rstrip("/")
    return f"{base}/share/{token}"


# ── Authenticated surface ─────────────────────────────────────────────────────

@router.post("/chat/messages/{message_id}/share", status_code=status.HTTP_201_CREATED)
async def create_share(
    message_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Mint (or return existing) public token for an assistant message.

    Idempotent: if the message already has an active share, we return the
    existing token instead of creating a duplicate. This matches user intent
    ("Share" is a single, persistent state) and keeps `shared_outputs.token`
    stable so cached links keep working.
    """
    org_id, user_id, token = _require_org(current_user)
    client = get_user_client(token)

    # Verify org-level sharing is enabled. We re-read the row instead of
    # caching because admins flipping the toggle should take effect on the
    # very next click — the lookup is one row by primary key.
    org_row = await asyncio.to_thread(
        lambda: client.table("organizations")
        .select("allow_output_sharing")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    if not org_row or not org_row.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found.")
    if org_row.data.get("allow_output_sharing") is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sharing is disabled for your workspace. Ask an admin to enable it.",
        )

    msg = await asyncio.to_thread(
        lambda: client.table("messages")
        .select("id, role, conversation_id")
        .eq("id", message_id)
        .maybe_single()
        .execute()
    )
    if not msg or not msg.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found.")
    if msg.data.get("role") != "assistant":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only assistant messages can be shared.",
        )

    existing = await asyncio.to_thread(
        lambda: client.table("shared_outputs")
        .select("token, view_count, created_at")
        .eq("message_id", message_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    rows = existing.data or []
    if rows:
        return {
            "url": _public_share_url(rows[0]["token"]),
            "token": rows[0]["token"],
            "view_count": rows[0].get("view_count", 0),
            "created_at": rows[0].get("created_at"),
            "reused": True,
        }

    token_value = secrets.token_urlsafe(32)
    inserted = await asyncio.to_thread(
        lambda: client.table("shared_outputs")
        .insert(
            {
                "message_id": message_id,
                "conversation_id": msg.data["conversation_id"],
                "org_id": org_id,
                "created_by": user_id,
                "token": token_value,
                "is_active": True,
            }
        )
        .execute()
    )
    if not inserted.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create share link.",
        )

    try:
        from app.services.analytics import track_event
        from app.services.activity import log_activity, resolve_user_privacy

        await track_event(
            org_id=org_id,
            user_id=user_id,
            event_type="share_created",
            metadata={"message_id": message_id},
        )
        is_private = await resolve_user_privacy(user_id)
        await log_activity(
            org_id=org_id,
            user_id=user_id,
            activity_type="shared_output",
            metadata={"message_id": message_id},
            is_private=is_private,
        )
    except Exception:
        pass

    return {
        "url": _public_share_url(token_value),
        "token": token_value,
        "view_count": 0,
        "created_at": inserted.data[0].get("created_at"),
        "reused": False,
    }


@router.delete(
    "/chat/messages/{message_id}/share",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_share(
    message_id: str,
    current_user: dict = Depends(verify_jwt),
) -> None:
    """Deactivate every active share row for this message. Subsequent shares
    mint a fresh token (we preserve the revoked row for audit)."""
    _, _, token = _require_org(current_user)
    client = get_user_client(token)

    result = await asyncio.to_thread(
        lambda: client.table("shared_outputs")
        .update({"is_active": False, "revoked_at": "now()"})
        .eq("message_id", message_id)
        .eq("is_active", True)
        .execute()
    )
    # We don't 404 when nothing was active — "revoke" is idempotent.
    log.info("share_revoked", message_id=message_id, rows=len(result.data or []))


@router.get("/chat/messages/{message_id}/share")
async def get_share_state(
    message_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Return the current share state for the UI (button label, view count)."""
    _, _, token = _require_org(current_user)
    client = get_user_client(token)

    rows = await asyncio.to_thread(
        lambda: client.table("shared_outputs")
        .select("token, view_count, created_at, last_viewed_at")
        .eq("message_id", message_id)
        .eq("is_active", True)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    data = rows.data or []
    if not data:
        return {"is_shared": False}
    return {
        "is_shared": True,
        "url": _public_share_url(data[0]["token"]),
        "token": data[0]["token"],
        "view_count": data[0].get("view_count", 0),
        "created_at": data[0].get("created_at"),
        "last_viewed_at": data[0].get("last_viewed_at"),
    }


# ── Public surface ────────────────────────────────────────────────────────────

# Per-IP rate limit on /share/{token}. Generous enough to allow a normal
# refresh while shutting down scrapers; matches the 30 req/min we use on
# other lightly-protected public surfaces.
_PUBLIC_SHARE_RATE = 30
_PUBLIC_SHARE_WINDOW = 60


def _client_ip(request: Request) -> str:
    # The Railway / Vercel edge sets X-Forwarded-For; fall back to socket peer.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",", 1)[0].strip()
    return request.client.host if request.client else "anon"


@router.get("/share/{token}")
async def get_public_share(token: str, request: Request) -> dict[str, Any]:
    """Public, unauthenticated read of a shared assistant message.

    The service-role client is used because anon RLS doesn't include
    shared_outputs (intentionally — orgs decide what's public, not the policy).
    We manually filter `is_active=true` before returning, then bump view_count.
    """
    # Rate limit by IP first — cheap rejection of bots before any DB work.
    ip = _client_ip(request)
    rl = await _sliding_window_check(
        namespace="share_public",
        identifier=ip,
        limit=_PUBLIC_SHARE_RATE,
        window_seconds=_PUBLIC_SHARE_WINDOW,
    )
    if not rl.allowed:
        raise RateLimited(
            "Too many requests to this share link. Try again in a minute.",
            retry_after=rl.reset_seconds,
        )

    # Token validation — short-circuit before talking to the DB.
    if not (16 <= len(token) <= 80) or not all(
        c.isalnum() or c in "-_" for c in token
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link not found.")

    svc = get_service_client()

    share_row = await asyncio.to_thread(
        lambda: svc.table("shared_outputs")
        .select(
            "id, message_id, conversation_id, org_id, view_count, "
            "is_active, created_at"
        )
        .eq("token", token)
        .maybe_single()
        .execute()
    )
    if not share_row or not share_row.data or not share_row.data.get("is_active"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This link no longer works. The author may have revoked it.",
        )

    share = share_row.data
    msg = await asyncio.to_thread(
        lambda: svc.table("messages")
        .select("id, content, sources, created_at")
        .eq("id", share["message_id"])
        .maybe_single()
        .execute()
    )
    if not msg or not msg.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link not found.")

    org = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select("name, allow_output_sharing")
        .eq("id", share["org_id"])
        .maybe_single()
        .execute()
    )
    if not org or not org.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link not found.")
    # If the org has since disabled sharing, hide existing shares too —
    # otherwise the admin toggle is incomplete.
    if org.data.get("allow_output_sharing") is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The author's workspace has disabled shared links.",
        )

    # Fire-and-forget view bump — failures shouldn't block the read.
    try:
        await asyncio.to_thread(
            lambda: svc.table("shared_outputs")
            .update(
                {
                    "view_count": (share.get("view_count") or 0) + 1,
                    "last_viewed_at": "now()",
                }
            )
            .eq("id", share["id"])
            .execute()
        )
    except Exception as exc:
        log.warning("share_view_count_bump_failed", error=str(exc))

    # Strip anything we don't want public from sources — chunk content snippets
    # may include internal context the org didn't intend to publish at link
    # creation. Names and page numbers are safe.
    public_sources = []
    for s in (msg.data.get("sources") or []):
        public_sources.append(
            {
                "document_name": s.get("document_name") or s.get("name"),
                "page_number": s.get("page_number"),
            }
        )

    return {
        "content": msg.data.get("content") or "",
        "sources": public_sources,
        "org_name": org.data.get("name"),
        "created_at": msg.data.get("created_at"),
        "shared_at": share.get("created_at"),
    }
