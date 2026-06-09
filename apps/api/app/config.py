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

    # ── Rate limits ─────────────────────────────────────────────────────────
    # Per-user/minute on the chat endpoint. The previous per-org cap was lenient
    # because we trusted orgs; per-user catches runaway scripts inside a tenant.
    rate_limit_chat_per_user_per_minute: int = 20
    # Monthly task budgets per plan. "business" / "free" handled by a None check
    # in the limiter (unlimited / blocked). Drives both pricing enforcement and
    # the friendly "you've used X / Y" messaging we'll surface later.
    rate_limit_chat_monthly_starter: int = 500
    rate_limit_chat_monthly_growth: int = 2_500


@lru_cache
def get_settings() -> Settings:
    return Settings()
