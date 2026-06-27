"""FastAPI dependency factory for per-user sliding-window rate limiting.

Reuses the Redis infrastructure already in place for chat rate limiting
(`services/rate_limit._sliding_window_check`). Key by user_id so limits are
per-person, not per-org — mirrors the chat tier-1 pattern.

Fail-open: if Upstash is unreachable the dependency passes through.  That
matches the existing chat limiter contract and keeps the app usable during
Redis blips.

Usage:
    from app.core.rate_limiter import make_rate_limiter

    upload_limiter = make_rate_limiter("upload", limit=20, window_seconds=3600)

    @router.post("/upload/init", dependencies=[Depends(upload_limiter)])
    async def init_upload(...):
        ...
"""
from __future__ import annotations

from fastapi import Depends

from app.auth import verify_jwt
from app.config import get_settings
from app.errors import RateLimited
from app.services.rate_limit import _sliding_window_check


def make_rate_limiter(namespace: str, limit: int, window_seconds: int):
    """Return a FastAPI dependency that enforces a per-user rate limit.

    Args:
        namespace:      Redis key prefix — keep it short and unique per call
                        site (e.g. "upload", "oauth_cb", "admin_agg").
        limit:          Max requests allowed in the window.
        window_seconds: Sliding window size in seconds.
    """
    async def _dep(current_user: dict = Depends(verify_jwt)) -> None:
        if get_settings().environment == "development":
            return
        user_id: str = current_user.get("user_id") or "anon"
        result = await _sliding_window_check(
            namespace=namespace,
            identifier=user_id,
            limit=limit,
            window_seconds=window_seconds,
        )
        if not result.allowed:
            raise RateLimited(
                message=(
                    f"Too many requests. Wait {result.reset_seconds}s and try again."
                ),
                retry_after=result.reset_seconds,
            )

    return _dep


def make_org_rate_limiter(namespace: str, limit: int, window_seconds: int):
    """Per-org sliding-window limiter — used for expensive operations where
    a single recruiter could otherwise burn the whole org's budget (e.g.
    creating real ATS postings, each of which may have a billing impact).
    """
    async def _dep(current_user: dict = Depends(verify_jwt)) -> None:
        if get_settings().environment == "development":
            return
        org_id: str = current_user.get("org_id") or "anon"
        result = await _sliding_window_check(
            namespace=namespace,
            identifier=org_id,
            limit=limit,
            window_seconds=window_seconds,
        )
        if not result.allowed:
            raise RateLimited(
                message=(
                    f"Your team has hit the limit for this action. "
                    f"Wait {result.reset_seconds}s and try again."
                ),
                retry_after=result.reset_seconds,
            )

    return _dep


# Pre-instantiated limiters — import these directly rather than calling
# make_rate_limiter() each time so the closure is created once at module load.

upload_limiter = make_rate_limiter("upload", limit=20, window_seconds=3600)
oauth_callback_limiter = make_rate_limiter("oauth_cb", limit=10, window_seconds=3600)
admin_analytics_limiter = make_rate_limiter("admin_agg", limit=30, window_seconds=3600)
# Recruiting publish: 5/hour/org. ATS postings often have billing impact and
# real customers don't publish more than a handful per hour even at scale.
recruiting_publish_limiter = make_org_rate_limiter(
    "rec_publish", limit=5, window_seconds=3600
)
