from supabase import Client, create_client

from app.config import get_settings

_service_client: Client | None = None


def get_service_client() -> Client:
    """Service role client — bypasses RLS. This service has no per-request
    user auth, so every table access goes through this client, same as
    apps/api's Inngest functions."""
    global _service_client
    if _service_client is None:
        settings = get_settings()
        _service_client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    return _service_client
