from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from inngest.fast_api import serve as inngest_serve

from app.config import get_settings
from app.inngest import FUNCTIONS as INNGEST_FUNCTIONS
from app.inngest import get_inngest_client
from app.routers import chat, documents, health


def create_app() -> FastAPI:
    settings = get_settings()

    _app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
    )

    _app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    return _app


app = create_app()

app.include_router(health.router)
app.include_router(documents.router)
app.include_router(chat.router)

# Inngest serve endpoint — webhook the Inngest server hits to invoke our functions.
# Mounts at /api/inngest by default.
inngest_serve(app, get_inngest_client(), INNGEST_FUNCTIONS)
