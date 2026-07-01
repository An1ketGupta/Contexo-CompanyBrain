"""Outbound-only Inngest client.

This service never hosts an /api/inngest serve handler (it doesn't receive
Inngest-triggered calls), so it only needs an event key to send() — no
signing key, no app_id collision risk with apps/api's "company-brain" app.
Events fired here (onboarding_v2/loi_signed_uploaded, esign/signer_turn,
etc.) are picked up by apps/api's already-registered functions purely by
event name — Inngest routes on the event name across the whole account, not
by which app_id sent it.
"""
from __future__ import annotations

from functools import lru_cache

import inngest

from app.config import get_settings

APP_ID = "nirnayaiq-esign"


@lru_cache(maxsize=1)
def get_inngest_client() -> inngest.Inngest:
    settings = get_settings()
    kwargs: dict = {
        "app_id": APP_ID,
        "is_production": settings.environment.lower() == "production",
    }
    if settings.inngest_event_key:
        kwargs["event_key"] = settings.inngest_event_key
    return inngest.Inngest(**kwargs)


async def send_event(name: str, data: dict) -> None:
    client = get_inngest_client()
    await client.send(inngest.Event(name=name, data=data))
