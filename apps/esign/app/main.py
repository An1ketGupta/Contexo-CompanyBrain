from fastapi import FastAPI

from app.config import get_settings, validate_production_config
from app.routers import envelopes, health, public_sign


def create_app() -> FastAPI:
    settings = get_settings()

    # Fail loud at boot if a production deploy is missing critical secrets —
    # mirrors apps/api/app/main.py's create_app().
    validate_production_config(settings)

    _app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
    )
    return _app


app = create_app()

app.include_router(health.router)
app.include_router(envelopes.router)
app.include_router(public_sign.router)
