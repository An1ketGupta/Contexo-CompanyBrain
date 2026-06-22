"""Single shared Inngest client used for both event-sending and function-serving.

In development (no event_key configured), Inngest runs against the local Dev
Server (default http://localhost:8288). In production, it uses the hosted
service with signing-key verification.
"""
from __future__ import annotations

from functools import lru_cache

import inngest

from app.config import get_settings

APP_ID = "company-brain"


@lru_cache(maxsize=1)
def get_inngest_client() -> inngest.Inngest:
    settings = get_settings()
    is_production = settings.environment.lower() == "production"

    # In production an unsigned client would accept any HTTP POST to the
    # /api/inngest serve handler, letting anyone with the URL trigger
    # ingestion, agent dispatch, or webhook delivery. The startup
    # validation in app.config also catches this; this is defence-in-depth
    # at construction time so even a stray cache reuse can't bypass it.
    if is_production and not settings.inngest_signing_key:
        raise RuntimeError(
            "INNGEST_SIGNING_KEY is required in production. "
            "Without it the Inngest serve handler accepts unsigned requests."
        )

    kwargs: dict = {
        "app_id": APP_ID,
        "is_production": is_production,
    }
    if settings.inngest_event_key:
        kwargs["event_key"] = settings.inngest_event_key
    if settings.inngest_signing_key:
        kwargs["signing_key"] = settings.inngest_signing_key

    return inngest.Inngest(**kwargs)
