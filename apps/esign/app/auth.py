"""Internal service-to-service auth for the /envelopes API.

apps/api sends X-Esign-Api-Key on every call. The public /sign/{token}
routes are NOT gated by this — the signing token itself is the credential
there (see routers/public_sign.py), same as onboarding_public.py's BGV/
references pattern.
"""
from __future__ import annotations

from fastapi import Header, HTTPException, status

from app.config import get_settings


async def verify_internal_api_key(x_esign_api_key: str = Header(default="")) -> None:
    settings = get_settings()
    if not settings.esign_api_key or x_esign_api_key != settings.esign_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing X-Esign-Api-Key.")
