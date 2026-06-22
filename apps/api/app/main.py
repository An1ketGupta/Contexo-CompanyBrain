from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from inngest.fast_api import serve as inngest_serve

from app.config import get_settings, validate_production_config
from app.errors import install_exception_handlers
from app.inngest import FUNCTIONS as INNGEST_FUNCTIONS
from app.inngest import get_inngest_client
from app.middleware.request_context import RequestContextMiddleware
from app.observability import init_observability
from app.routers import (
    admin as admin_router,
)
from app.routers import (
    approvals as approvals_router,
)
from app.routers import (
    auth as auth_router,
)
from app.routers import (
    billing as billing_router,
)
from app.routers import (
    chat,
    documents,
    gmail_router,
    health,
    invitations,
    public_api,
    search,
    slack_router,
)
from app.routers import (
    webhooks_stripe as webhooks_stripe_router,
)
from app.routers import (
    collections as collections_router,
)
from app.routers import (
    compliance as compliance_router,
)
from app.routers import (
    document_versions as document_versions_router,
)
from app.routers import (
    integrations as integrations_router,
)
from app.routers import (
    integrations_v2 as integrations_v2_router,
)
from app.routers import (
    internal as internal_router,
)
from app.routers import (
    meeting_prep as meeting_prep_router,
)
from app.routers import (
    notifications as notifications_router,
)
from app.routers import (
    organizations as organizations_router,
)
from app.routers import (
    scheduled_reports as scheduled_reports_router,
)
from app.routers import (
    settings as settings_router,
)
from app.routers import (
    sharing as sharing_router,
)
from app.routers import (
    support as support_router,
)
from app.routers import (
    team as team_router,
)
from app.routers import (
    templates as templates_router,
)
from app.routers import (
    time_savings as time_savings_router,
)
from app.routers import (
    usage as usage_router,
)
from app.routers import (
    webhooks as webhooks_router,
)


def create_app() -> FastAPI:
    settings = get_settings()

    # Fail loud at boot if a production deploy is missing critical secrets.
    # Doing this before init_observability means a misconfigured prod box
    # never reports itself healthy to Railway, so a bad deploy gets rolled
    # back automatically instead of accepting traffic and 500-ing.
    validate_production_config(settings)

    # Bootstrap logging + Sentry before any other module-level work that might
    # try to log. Idempotent: safe to call from tests' app factories.
    init_observability(settings)

    # Touch the Langfuse module so it initializes the singleton at app boot
    # rather than on the first chat request (lazy init would block the request
    # by a few hundred ms while resolving DNS to ingest.langfuse.com).
    from app.services import langfuse as _lf  # noqa: F401

    _app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
    )

    @_app.on_event("shutdown")
    async def _flush_langfuse() -> None:
        # Drain any queued spans before the worker exits. Safe no-op when
        # tracing is disabled.
        from app.services.langfuse import flush

        flush()

    # CORS is added first → executed last; RequestContext is added last → first.
    # Order matters: we want request_id bound BEFORE CORS short-circuits an
    # OPTIONS pre-flight so even pre-flight rejections are traceable.
    # `allow_origin_regex` lets the Chrome extension (V4 #32) call this API
    # without us pinning a specific extension id. Chrome extension origins
    # are `chrome-extension://<32-char-id>`; the id is stable per build but
    # differs between dev (load-unpacked) and Web Store builds, so a regex
    # is the only sane allow-list.
    # Explicit method + header allowlists. `allow_credentials=True` paired with
    # `*` is treated by browsers as null-origin and silently breaks credentialed
    # requests, but more importantly it's a defence-in-depth gap if a future
    # endpoint accepts a non-standard header carrying a sensitive token.
    # Methods covers every verb our routers actually use; headers covers what
    # the Next.js proxy + Supabase client + RequestContext middleware send.
    _app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=r"^chrome-extension://[a-z0-9]+$",
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Accept",
            "X-Request-ID",
            "X-Org-Id",
            "Idempotency-Key",
        ],
        expose_headers=["X-Request-ID", "Retry-After"],
    )
    _app.add_middleware(RequestContextMiddleware)

    install_exception_handlers(_app)

    return _app


app = create_app()

app.include_router(health.router)
app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(search.router)
app.include_router(settings_router.router)
app.include_router(invitations.router)
app.include_router(auth_router.router)
app.include_router(usage_router.router)
app.include_router(webhooks_router.router)
app.include_router(integrations_router.router)
app.include_router(integrations_v2_router.router)
app.include_router(public_api.router)
app.include_router(slack_router.router)
app.include_router(gmail_router.router)
app.include_router(organizations_router.router)
app.include_router(templates_router.router)
app.include_router(sharing_router.router)
app.include_router(admin_router.router)
app.include_router(team_router.router)
app.include_router(document_versions_router.router)
app.include_router(meeting_prep_router.router)
app.include_router(collections_router.router)
app.include_router(time_savings_router.router)
app.include_router(approvals_router.router)
app.include_router(compliance_router.router)
app.include_router(support_router.router)
app.include_router(notifications_router.router)
app.include_router(internal_router.router)
app.include_router(scheduled_reports_router.router)
app.include_router(billing_router.router)
app.include_router(webhooks_stripe_router.router)

# Inngest serve endpoint — webhook the Inngest server hits to invoke our functions.
# Mounts at /api/inngest by default.
inngest_serve(app, get_inngest_client(), INNGEST_FUNCTIONS)
