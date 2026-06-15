from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_name: str = "Company Brain API"
    app_version: str = "0.1.0"
    debug: bool = False
    environment: str = "development"

    # CORS — comma-separated in .env: ALLOWED_ORIGINS=http://localhost:3000,https://app.vercel.app
    allowed_origins: str = "http://localhost:3000"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    # Supabase
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str

    # AI Providers
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    huggingface_api_key: str = ""         # used as a dev fallback when Gemini quota is exhausted

    # Provider selection
    llm_provider: str = "gemini"          # gemini | claude | openai
    embedding_provider: str = "google"    # google | huggingface | openai
    embedding_dimensions: int = 768       # 768 for google/HF mpnet-base, 1536 for openai

    # LLM behavior
    llm_model: str = "gemini-3.1-flash-lite"
    llm_temperature: float = 0.3
    llm_timeout_seconds: float = 30.0

    # Tool-use guardrails (defense in depth — caps the worst case)
    chat_max_tool_rounds: int = 4         # max round-trips through the LLM in one task
    chat_max_searches: int = 8            # max distinct search tool calls per task
    chat_max_context_chunks: int = 20     # cap chunks passed to LLM after dedup
    chat_history_turns: int = 6           # how many prior messages we include
    chat_max_message_chars: int = 16_000  # input length guard
    chat_search_k: int = 8                # k per individual search tool call

    # Storage
    storage_provider: str = "supabase"   # supabase | r2
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = ""

    # Inngest
    inngest_signing_key: str = ""
    inngest_event_key: str = ""

    # Upstash Redis
    upstash_redis_rest_url: str = ""
    upstash_redis_rest_token: str = ""

    # ── Email (Resend + React Email via internal Next.js render route) ──────
    resend_api_key: str = ""
    email_from: str = "NirnayaIQ <onboarding@resend.dev>"
    # The Next.js app exposes POST /api/internal/email/render. In dev this is
    # http://localhost:3000; in prod, set EMAIL_RENDER_URL to the deployed URL.
    email_render_url: str = "http://localhost:3000/api/internal/email/render"
    # Shared HMAC secret with the Next.js render route. Same value as
    # NEXT_PUBLIC NOTHING — env var is INTERNAL_EMAIL_SECRET on both sides.
    internal_email_secret: str = ""
    # Public web URL used when building absolute links inside emails.
    app_url: str = "http://localhost:3000"
    # If false, the worker pretends Resend succeeded (logs the payload) — used
    # in local dev when you haven't put a Resend key in your .env yet.
    email_enabled: bool = False

    # ── Observability ───────────────────────────────────────────────────────
    # Sentry — leave SENTRY_DSN empty in dev to disable. Sampling defaults keep
    # the free tier comfortable; bump traces_sample_rate before a launch event.
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.1
    sentry_profiles_sample_rate: float = 0.0
    # Release tag — set by CI from the git SHA. Empty in dev means "no release"
    # which Sentry handles gracefully (no source-map matching, just plain stack).
    release_version: str = ""

    # Logging — "json" for prod (Railway log shippers parse it), "console" in dev.
    log_format: str = "console"
    log_level: str = "INFO"

    # ── Langfuse (LLM observability) ────────────────────────────────────────
    # Leave keys empty in dev to disable — the @observe wrappers become no-ops
    # via `enabled=False`. In prod we'd set all three.
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    # Sample 1.0 (everything) until we have traffic — drop to 0.1 if cost matters.
    langfuse_sample_rate: float = 1.0

    # ── Rate limits ─────────────────────────────────────────────────────────
    # Per-user/minute on the chat endpoint. The previous per-org cap was lenient
    # because we trusted orgs; per-user catches runaway scripts inside a tenant.
    rate_limit_chat_per_user_per_minute: int = 20
    # Monthly task budgets per plan. "business" / "free" handled by a None check
    # in the limiter (unlimited / blocked). Drives both pricing enforcement and
    # the friendly "you've used X / Y" messaging we'll surface later.
    rate_limit_chat_monthly_starter: int = 500
    rate_limit_chat_monthly_growth: int = 2_500

    # Per-API-key/minute on the public /v1 endpoints. Tighter than the per-user
    # cap because automation can burn through quotas in seconds.
    rate_limit_api_per_key_per_minute: int = 60

    # ── Day-14 integrations: OAuth credentials ─────────────────────────────
    # Google Drive — see https://console.cloud.google.com/apis/credentials.
    # Empty in dev disables the Drive UI card + skips the polling cron.
    google_client_id: str = ""
    google_client_secret: str = ""
    google_oauth_redirect_uri: str = "http://localhost:8000/integrations/drive/callback"
    # Gmail OAuth lives on a distinct redirect URI so the same Google client
    # can issue both Drive (org-level) and Gmail (per-user) tokens without the
    # callback handler having to disambiguate provider from state alone.
    gmail_oauth_redirect_uri: str = "http://localhost:8000/integrations/gmail/callback"

    # Notion — create an integration at https://notion.so/my-integrations
    # and a public OAuth app under "Settings → OAuth Domain & URIs".
    notion_client_id: str = ""
    notion_client_secret: str = ""
    notion_oauth_redirect_uri: str = "http://localhost:8000/integrations/notion/callback"

    # Email-forward inbound (Resend Inbound or Mailgun routes). The
    # signing-secret is used to verify webhook authenticity. The base domain
    # is the suffix orgs see (brain-<slug>@inbound.<domain>).
    inbound_email_domain: str = "inbound.nirnayaiq.com"
    inbound_email_webhook_secret: str = ""

    # ── Day-15 Slack bot ─────────────────────────────────────────────────────
    slack_client_id: str = ""
    slack_client_secret: str = ""
    slack_signing_secret: str = ""
    slack_oauth_redirect_uri: str = "http://localhost:8000/integrations/slack/callback"

    # JWT signing secret for OAuth state round-trips (any OAuth that needs to
    # round-trip a user id through a third party — Slack, Drive, Notion).
    # Distinct from internal_email_secret to limit blast radius if either leaks.
    oauth_state_secret: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
