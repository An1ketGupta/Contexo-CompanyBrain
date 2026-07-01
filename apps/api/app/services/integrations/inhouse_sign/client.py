"""Client for apps/esign — our own signing service, replacing DocuSeal.

Unlike the old DocuSeal integration, there's no webhook/HMAC verification
step: apps/esign holds the same Supabase service-role key and writes
onboarding_signing_envelopes / onboarding_documents directly, then fires the
completion Inngest event itself. This client only needs to handle the
outbound direction — create an envelope, get signing URLs back.

Deployed on Render (same free tier as DocuSeal was), so it shares that
cold-start retry pattern: a sleeping instance can take 30-90s to wake on
the first request.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import fitz  # PyMuPDF — already a dependency for the induction PDF pipeline
import httpx

from app.config import get_settings
from app.database import get_service_client

log = logging.getLogger(__name__)


class InhouseSignError(RuntimeError):
    """Deterministic apps/esign API failure — 4xx, bad response, missing key."""


class InhouseSignUnavailable(RuntimeError):
    """apps/esign isn't configured. Callers fall back to the legacy
    print/scan flow."""


_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = (3.0, 8.0)
_RETRYABLE_EXC = (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError)


def _config() -> dict[str, str]:
    s = get_settings()
    return {
        "base_url": (getattr(s, "esign_service_url", "") or "").rstrip("/"),
        "api_key": getattr(s, "esign_api_key", "") or "",
    }


def is_configured() -> bool:
    cfg = _config()
    return bool(cfg["base_url"] and cfg["api_key"])


def _require_configured() -> dict[str, str]:
    cfg = _config()
    if not is_configured():
        raise InhouseSignUnavailable(
            "apps/esign not configured. Set ESIGN_SERVICE_URL and ESIGN_API_KEY."
        )
    return cfg


async def _request(method: str, path: str, *, json_body: Any | None = None) -> Any:
    cfg = _require_configured()
    url = f"{cfg['base_url']}{path}"
    headers = {"X-Esign-Api-Key": cfg["api_key"], "Accept": "application/json"}

    resp = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.request(method, url, headers=headers, json=json_body)
            break
        except _RETRYABLE_EXC as exc:
            if attempt == _MAX_ATTEMPTS:
                raise InhouseSignError(
                    f"esign {method} {path} unreachable after {attempt} attempts "
                    f"(Render free-tier cold start?): {exc}"
                ) from exc
            log.warning("inhouse_sign.request_retry method=%s path=%s attempt=%d", method, path, attempt)
            await asyncio.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])

    if resp.status_code >= 400:
        raise InhouseSignError(f"esign {method} {path} -> {resp.status_code}: {(resp.text or '')[:500]}")
    return resp.json() if resp.content else {}


async def create_envelope(
    *,
    org_id: str,
    run_id: str,
    document_kinds: list[str],
    storage_path: str,
    signers: list[dict[str, Any]],
    completion_event: str,
) -> dict[str, Any]:
    """signers: [{role, email, name, routing_order}]. Returns
    {"envelope_id": str, "signers": [{role, email, name, signing_url}]}."""
    return await _request(
        "POST",
        "/envelopes",
        json_body={
            "org_id": org_id,
            "run_id": run_id,
            "document_kinds": document_kinds,
            "storage_path": storage_path,
            "signers": signers,
            "completion_event": completion_event,
        },
    )


async def void_envelopes_for_run(*, org_id: str, run_id: str, reason: str) -> int:  # noqa: ARG001
    if not is_configured():
        return 0
    svc = get_service_client()
    rows = await asyncio.to_thread(
        lambda: svc.table("onboarding_signing_envelopes")
        .select("envelope_id")
        .eq("run_id", run_id)
        .eq("provider", "inhouse")
        .not_.in_("status", ["completed", "declined", "voided", "expired"])
        .execute()
    )
    active = rows.data or []
    voided = 0
    for env in active:
        try:
            await _request("POST", f"/envelopes/{env['envelope_id']}/void")
            voided += 1
        except InhouseSignError as exc:
            log.warning("inhouse_sign.void_failed envelope=%s err=%s", env.get("envelope_id"), exc)
    return voided


def merge_pdfs(pdf_byte_list: list[bytes]) -> bytes:
    """Concatenate multiple rendered PDFs (e.g. appointment_letter + nda)
    into one — apps/esign always signs exactly one PDF per envelope, so any
    multi-document bundle has to be merged before calling create_envelope."""
    if len(pdf_byte_list) == 1:
        return pdf_byte_list[0]
    merged = fitz.open()
    try:
        for b in pdf_byte_list:
            with fitz.open(stream=b, filetype="pdf") as part:
                merged.insert_pdf(part)
        return merged.tobytes()
    finally:
        merged.close()
