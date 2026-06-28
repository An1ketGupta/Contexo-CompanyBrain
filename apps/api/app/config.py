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
    # Per-API-key/day cap layered on top of per-minute + per-org-monthly. A
    # leaked key with a polite-cadence script wouldn't trip the minute limit
    # but could still burn the org's whole month in a day; this catches that.
    # Set to 0 to disable. Default budget is generous (~3500 calls/day, well
    # above any honest automation) — the goal is to cap abuse, not throttle.
    rate_limit_api_per_key_per_day: int = 3_500

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

    # ── Post-Slack integrations wave (OneDrive/SharePoint, Confluence,
    #    GitHub App, Dropbox). All four are org-scoped admin installs.
    #    Empty values disable the corresponding UI card + skip the polling
    #    cron — the deploy boots cleanly even if you haven't provisioned the
    #    OAuth apps yet.

    # Microsoft Graph (Azure AD app, "Microsoft 365 / personal" multi-tenant).
    # Scopes requested: Files.Read.All, Sites.Read.All, offline_access.
    # Use the v2 OAuth endpoint with the "common" tenant for multi-tenant
    # consent; admin consent is required for Sites.Read.All on most tenants.
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_tenant: str = "common"
    microsoft_oauth_redirect_uri: str = (
        "http://localhost:8000/integrations/onedrive/callback"
    )

    # Atlassian (Confluence Cloud OAuth 3LO). Scopes: read:confluence-content.all,
    # read:confluence-space.summary, read:confluence-content.summary, offline_access.
    atlassian_client_id: str = ""
    atlassian_client_secret: str = ""
    atlassian_oauth_redirect_uri: str = (
        "http://localhost:8000/integrations/confluence/callback"
    )

    # GitHub App. Unlike a classic OAuth app, the install flow gives us an
    # installation_id; we mint short-lived installation access tokens server-
    # side via a JWT signed with the app's private key.
    github_app_id: str = ""
    github_app_slug: str = ""
    github_app_client_id: str = ""
    github_app_client_secret: str = ""
    # PEM-formatted private key. Multi-line; in .env set as a single line with
    # \n escapes OR mount as a file and use a separate _path var (skipped here
    # to keep config flat — the deploy decodes \n on read).
    github_app_private_key: str = ""
    github_app_webhook_secret: str = ""
    github_oauth_redirect_uri: str = (
        "http://localhost:8000/integrations/github/callback"
    )

    # Dropbox Business. Scopes: files.content.read, files.metadata.read,
    # team_data.member, members.read. Team admin consent required.
    dropbox_client_id: str = ""
    dropbox_client_secret: str = ""
    dropbox_oauth_redirect_uri: str = (
        "http://localhost:8000/integrations/dropbox/callback"
    )

    # ── Stripe billing (Production Roadmap Day 6+) ──────────────────────────
    # `stripe_mode` decides which set of pricing_tiers rows we read at
    # runtime: 'test' for staging + local, 'live' for production. Keeping
    # this explicit (rather than inferring from the key prefix) protects
    # against a half-completed Test→Live cutover where the secret key was
    # rotated but the deploy still reads the test-mode tier table.
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_mode: str = "test"  # 'test' | 'live' — must match pricing_tiers.stripe_mode
    # Pin the Stripe API version so a Stripe-side rollout doesn't change
    # response shapes (e.g., subscription.items[].price.id structure)
    # without us opting in. Bump deliberately when we test against a newer
    # API version.
    stripe_api_version: str = "2024-12-18.acacia"
    # When true, the seed script + checkout flow include the
    # `billing_address_collection='required'` option so Stripe collects the
    # full address (needed for invoicing and EU VAT). Leave false until you
    # add Tax-ID / VAT support post-launch.
    stripe_collect_billing_address: bool = False

    # ── V5 Day 3 — Founder-only internal dashboards ──────────────────────────
    # Comma-separated Supabase auth user UUIDs that may hit /internal/* routes
    # (LLM cost dashboard, etc.). Empty in dev = nobody passes the gate.
    founder_user_ids: str = ""

    @property
    def founder_user_id_set(self) -> set[str]:
        return {u.strip() for u in self.founder_user_ids.split(",") if u.strip()}

    # ── V5 Day 4 — Embedding fine-tuning backend (Modal.com serverless GPU) ──
    # Modal hosts the sentence-transformers training + eval + serving stack.
    # Empty values disable the admin fine-tune button + skip the Inngest cron.
    # We POST to MODAL_FINETUNE_ENDPOINT with a JSONL of training pairs and a
    # bearer token; the endpoint returns a job_id we poll on
    # MODAL_FINETUNE_STATUS_ENDPOINT/{job_id}.
    modal_finetune_endpoint: str = ""
    modal_finetune_status_endpoint: str = ""
    modal_finetune_token: str = ""
    # Minimum training pairs before the UI even shows the fine-tune CTA.
    embedding_finetune_min_pairs: int = 50
    embedding_finetune_recommended_pairs: int = 200

    # ── Agent2 Day 5: ATS integrations (#20) ──────────────────────────────
    # All three ATS providers authenticate via an org-supplied API key, not
    # OAuth — there's no provider-side OAuth app to provision here. Persisted
    # to `integrations.access_token` and validated at connect time. These
    # config keys are reserved for future webhook signing if/when needed.
    greenhouse_webhook_secret: str = ""
    lever_webhook_secret: str = ""
    ashby_webhook_secret: str = ""

    # ATS API base URLs. Production defaults point at the real provider hosts.
    # For local dev set USE_MOCK_ATS=true and start tools/mock_ats_server.py —
    # one flag flips all three adapters to the mock at MOCK_ATS_URL (default
    # http://localhost:8001), no need to set each *_API_URL individually.
    #
    # For granular overrides (e.g. mock Greenhouse but real Lever), set the
    # per-provider *_API_URL env vars directly and leave USE_MOCK_ATS unset.
    use_mock_ats: bool = False
    mock_ats_url: str = "http://localhost:8001"
    greenhouse_api_url: str = "https://harvest.greenhouse.io/v1"
    lever_api_url: str = "https://api.lever.co/v1"
    ashby_api_url: str = "https://api.ashbyhq.com"

    # Naukri (Info Edge) HotVacancy API. Naukri is a job board (not an ATS) —
    # we group it under the same "posting destinations" model so the publish
    # form is one checkbox group. Real Naukri requires a signed enterprise
    # contract; no self-serve dev tier exists. USE_MOCK_ATS=true points every
    # adapter at the mock; for granular Naukri-only mocking set NAUKRI_API_URL
    # directly. Auth: HTTP "Auth-Key" header carrying the recruiter's account
    # API key (not Basic — Naukri diverges from the ATS providers here).
    naukri_api_url: str = "https://api.naukri.com/v1"
    naukri_webhook_secret: str = ""

    # ── Agent2 Day 6: Asana + Linear OAuth (#44) ──────────────────────────
    asana_client_id: str = ""
    asana_client_secret: str = ""
    asana_oauth_redirect_uri: str = (
        "http://localhost:8000/integrations/asana/callback"
    )

    linear_client_id: str = ""
    linear_client_secret: str = ""
    linear_oauth_redirect_uri: str = (
        "http://localhost:8000/integrations/linear/callback"
    )

    # Jira Cloud — Atlassian OAuth 3LO. Separate from atlassian_* (which is
    # the Confluence-scoped app) so each integration card can be enabled or
    # disabled independently.
    jira_client_id: str = ""
    jira_client_secret: str = ""
    jira_oauth_redirect_uri: str = (
        "http://localhost:8000/integrations/jira/callback"
    )


class ProductionConfigError(RuntimeError):
    """Raised at startup when production env vars are missing.

    Inherits RuntimeError so uvicorn surfaces it as a fatal boot error
    rather than the worker silently restarting on every request.
    """


# Env vars that MUST be present in production. Everything else either
# defaults safely (e.g., disabling an integration card) or is checked
# inline by the code path that needs it (e.g., a Stripe call fails
# closed if STRIPE_SECRET_KEY is empty). Listed by env-var name so the
# error message tells the operator exactly which dashboard secret to set.
_PRODUCTION_REQUIRED: tuple[tuple[str, str], ...] = (
    ("GEMINI_API_KEY", "gemini_api_key"),
    ("OAUTH_STATE_SECRET", "oauth_state_secret"),
    ("INNGEST_SIGNING_KEY", "inngest_signing_key"),
    ("SUPABASE_SERVICE_ROLE_KEY", "supabase_service_role_key"),
    ("UPSTASH_REDIS_REST_URL", "upstash_redis_rest_url"),
    ("UPSTASH_REDIS_REST_TOKEN", "upstash_redis_rest_token"),
)


def validate_production_config(settings: Settings) -> None:
    """Raise ProductionConfigError if any required env var is empty in prod.

    Called from app.main.create_app() before the FastAPI app is constructed
    so a misconfigured deploy fails the Railway healthcheck immediately
    instead of accepting traffic and 500-ing on the first request.
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
            + ". Set them in the Railway dashboard and redeploy."
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
