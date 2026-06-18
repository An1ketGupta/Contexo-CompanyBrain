from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from inngest.fast_api import serve as inngest_serve

from app.config import get_settings
from app.errors import install_exception_handlers
from app.inngest import FUNCTIONS as INNGEST_FUNCTIONS
from app.inngest import get_inngest_client
from app.middleware.request_context import RequestContextMiddleware
from app.observability import init_observability
from app.routers import (
    admin as admin_router,
    approvals as approvals_router,
    auth as auth_router,
    chat,
    collections as collections_router,
    compliance as compliance_router,
    document_versions as document_versions_router,
    documents,
    gmail_router,
    health,
    integrations as integrations_router,
    invitations,
    meeting_prep as meeting_prep_router,
    notifications as notifications_router,
    organizations as organizations_router,
    public_api,
    search,
    settings as settings_router,
    sharing as sharing_router,
    slack_router,
    support as support_router,
    team as team_router,
    templates as templates_router,
    time_savings as time_savings_router,
    usage as usage_router,
    webhooks as webhooks_router,
)


def create_app() -> FastAPI:
    settings = get_settings()

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
    _app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=r"^chrome-extension://[a-z0-9]+$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
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

# Inngest serve endpoint — webhook the Inngest server hits to invoke our functions.
# Mounts at /api/inngest by default.
inngest_serve(app, get_inngest_client(), INNGEST_FUNCTIONS)
