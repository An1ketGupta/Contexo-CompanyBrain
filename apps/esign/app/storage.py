"""Supabase Storage helpers — shares the same bucket/prefix convention as
apps/api's onboarding_v2/storage.py (document/orgs/{org}/onboarding/{run}/...),
since this service reads the PDFs that pipeline already rendered.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import get_settings
from app.database import get_service_client

log = logging.getLogger(__name__)

SIGNED_URL_TTL_SECONDS = 7 * 24 * 60 * 60
PREVIEW_URL_TTL_SECONDS = 60 * 60


def _bucket():
    settings = get_settings()
    svc = get_service_client()
    return svc.storage.from_(settings.storage_bucket)


async def download_pdf(storage_path: str) -> bytes:
    def _download() -> bytes:
        return _bucket().download(storage_path)

    return await asyncio.to_thread(_download)


async def upload_pdf(storage_path: str, pdf_bytes: bytes) -> None:
    def _upload() -> None:
        _bucket().upload(
            path=storage_path,
            file=pdf_bytes,
            file_options={"content-type": "application/pdf", "upsert": "true"},
        )

    await asyncio.to_thread(_upload)


async def mint_signed_url(storage_path: str, *, ttl_seconds: int | None = None) -> str | None:
    ttl = ttl_seconds or PREVIEW_URL_TTL_SECONDS

    def _mint() -> str | None:
        try:
            res = _bucket().create_signed_url(path=storage_path, expires_in=ttl)
            return res.get("signedURL") or res.get("signed_url")
        except Exception as exc:  # noqa: BLE001
            log.warning("esign.signed_url_failed path=%s err=%s", storage_path, exc)
            return None

    return await asyncio.to_thread(_mint)


def expires_at_iso(days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


async def get_envelope_by_token(token: str) -> dict[str, Any] | None:
    """Find the envelope whose `signers` array contains an entry with this
    public_token. Uses Postgres JSONB containment (@>), same trick
    onboarding_v2.py already uses for `document_kinds @> ['loi']` — backed
    by the GIN index added in migration 082."""
    svc = get_service_client()

    def _fetch() -> dict[str, Any] | None:
        res = (
            svc.table("onboarding_signing_envelopes")
            .select("*")
            .contains("signers", json.dumps([{"public_token": token}]))
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    return await asyncio.to_thread(_fetch)
