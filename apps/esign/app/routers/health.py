from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict[str, Any]:
    """Liveness probe. Cheap, never 5xx — safe for Render's deploy healthcheck."""
    settings = get_settings()
    return {
        "status": "ok",
        "version": settings.app_version,
        "environment": settings.environment,
    }
