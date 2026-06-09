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
    auth as auth_router,
    chat,
    documents,
    health,
    invitations,
    search,
    settings as settings_router,
)


def create_app() -> FastAPI:
    settings = get_settings()

    # Bootstrap logging + Sentry before any other module-level work that might
    # try to log. Idempotent: safe to call from tests' app factories.
    init_observability(settings)

    _app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
    )

    # CORS is added first → executed last; RequestContext is added last → first.
    # Order matters: we want request_id bound BEFORE CORS short-circuits an
    # OPTIONS pre-flight so even pre-flight rejections are traceable.
    _app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
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

# Inngest serve endpoint — webhook the Inngest server hits to invoke our functions.
# Mounts at /api/inngest by default.
inngest_serve(app, get_inngest_client(), INNGEST_FUNCTIONS)
