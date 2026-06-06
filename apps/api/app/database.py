from supabase import create_client, Client
from app.config import get_settings

_service_client: Client | None = None


def get_service_client() -> Client:
    """Service role client — bypasses RLS. Use only in background jobs (Inngest)."""
    global _service_client
    if _service_client is None:
        settings = get_settings()
        _service_client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    return _service_client


def get_user_client(jwt_token: str) -> Client:
    """User-scoped client that respects RLS policies. Use in all request handlers."""
    settings = get_settings()
    client = create_client(settings.supabase_url, settings.supabase_anon_key)
    client.postgrest.auth(jwt_token)
    return client
