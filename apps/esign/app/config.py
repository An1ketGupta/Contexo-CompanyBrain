from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_name: str = "NirnayaIQ E-Sign"
    app_version: str = "0.1.0"
    debug: bool = False
    environment: str = "development"

    # Supabase — shared project with apps/api. Service-role only: this
    # service has no per-request user auth, every write goes through RLS
    # bypass the same way apps/api's Inngest functions already do.
    supabase_url: str
    supabase_service_role_key: str

    # Same bucket apps/api's onboarding_v2/storage.py uses — signed PDFs
    # live alongside the drafts under orgs/{org}/onboarding/{run}/.
    storage_bucket: str = "document"

    # Inngest — send-only. This service never receives Inngest-triggered
    # calls (no /api/inngest serve handler), so only an event key is needed,
    # not a signing key. Events fired here are picked up by apps/api's
    # already-registered Inngest functions regardless of sender app_id.
    inngest_event_key: str = ""

    # Shared secret apps/api sends as X-Esign-Api-Key on every internal call
    # (POST /envelopes, /void). The public /sign/{token} routes are NOT
    # gated by this — the token itself is the credential there.
    esign_api_key: str = ""

    # Public web app base URL — used to build the {app_url}/sign/{token}
    # links emailed to signers. Same value as apps/api's APP_URL.
    app_url: str = "http://localhost:3000"

    # ── Observability ───────────────────────────────────────────────────────
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.1
    log_format: str = "console"
    log_level: str = "INFO"


class ProductionConfigError(RuntimeError):
    """Raised at startup when production env vars are missing."""


_PRODUCTION_REQUIRED: tuple[tuple[str, str], ...] = (
    ("SUPABASE_URL", "supabase_url"),
    ("SUPABASE_SERVICE_ROLE_KEY", "supabase_service_role_key"),
    ("ESIGN_API_KEY", "esign_api_key"),
    ("INNGEST_EVENT_KEY", "inngest_event_key"),
)


def validate_production_config(settings: Settings) -> None:
    """Raise ProductionConfigError if any required env var is empty in prod.

    Mirrors apps/api/app/config.py's validate_production_config — fail loud
    at boot rather than accepting traffic with a half-wired deploy.
    """
    if settings.environment.lower() != "production":
        return
    missing = [
        env_name
        for env_name, attr in _PRODUCTION_REQUIRED
        if not getattr(settings, attr, None)
    ]
    if missing:
        raise ProductionConfigError(
            "Missing required production env vars: "
            + ", ".join(missing)
            + ". Set them in the Render dashboard and redeploy."
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
