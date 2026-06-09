import asyncio
from functools import lru_cache

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from supabase import Client, create_client

from app.config import get_settings
from app.observability import bind_user_context, get_logger

log = get_logger(__name__)
bearer_scheme = HTTPBearer()


@lru_cache(maxsize=1)
def _auth_client() -> Client:
    """Anon-keyed client used only to validate access tokens against /auth/v1/user."""
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_anon_key)


async def verify_jwt(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """Validates the Supabase access token by calling `auth.get_user(token)`.

    Supabase does the signature/expiry check server-side, so this works with any
    JWT signing algorithm (HS256, ES256, RS256) and survives Supabase's ongoing
    migration to asymmetric keys without us having to manage JWT_SECRET or JWKS.
    """
    token = credentials.credentials
    client = _auth_client()

    try:
        response = await asyncio.to_thread(lambda: client.auth.get_user(token))
    except Exception as e:
        log.warning("token_validation_failed", exc_type=type(e).__name__, error=str(e))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = getattr(response, "user", None)
    user_id = getattr(user, "id", None) if user else None
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    app_metadata = getattr(user, "app_metadata", None) or {}
    org_id: str | None = app_metadata.get("org_id")

    # Bind to log context + Sentry scope so every subsequent log line and any
    # error captured during this request carries identity.
    bind_user_context(user_id=user_id, org_id=org_id)

    return {"user_id": user_id, "org_id": org_id, "token": token}
