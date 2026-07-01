"""Public, no-API-key routes — the signing token in the URL is the
credential. Mirrors apps/api/app/routers/onboarding_public.py's pattern:
UUID-format check -> row lookup via service client -> expiry check ->
idempotency short-circuit on already-signed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from app.config import get_settings
from app.database import get_service_client
from app.inngest_events import send_event
from app.models import SignPrefill, SubmitSignatureRequest, SubmitSignatureResponse
from app.pdf_sign import AuditEntry, SignatureImageError, append_audit_page, stamp_signature
from app.storage import download_pdf, mint_signed_url, upload_pdf

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sign", tags=["public-sign"])


def _validate_token(token: str) -> None:
    try:
        uuid.UUID(token)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Malformed token.") from exc


def _find_signer(signers: list[dict[str, Any]], token: str) -> dict[str, Any] | None:
    return next((s for s in signers if s.get("public_token") == token), None)


def _current_pdf_path(events: list[dict[str, Any]]) -> str:
    """Most recent event carrying a pdf_path — the working document as of
    right now. Always present since envelope_created always sets one."""
    for e in reversed(events):
        if isinstance(e, dict) and e.get("pdf_path"):
            return e["pdf_path"]
    raise RuntimeError("envelope has no pdf_path in its event log")


def _source_metadata(events: list[dict[str, Any]]) -> dict[str, Any]:
    for e in events:
        if isinstance(e, dict) and e.get("event") == "envelope_created":
            return e
    raise RuntimeError("envelope missing envelope_created event")


@router.get("/{token}", response_model=SignPrefill)
async def get_sign_prefill(token: str) -> SignPrefill:
    _validate_token(token)
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

    env = await asyncio.to_thread(_fetch)
    if not env:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not recognised.")

    signers = env.get("signers") or []
    signer = _find_signer(signers, token)
    if not signer:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not recognised.")

    expires_at = signer.get("public_token_expires_at")
    if expires_at and datetime.fromisoformat(expires_at.replace("Z", "+00:00")) < datetime.now(UTC):
        raise HTTPException(status.HTTP_410_GONE, "This signing link has expired.")

    if env.get("status") in ("voided", "declined", "expired"):
        raise HTTPException(status.HTTP_410_GONE, f"This envelope is {env['status']}.")

    pdf_path = _current_pdf_path(env.get("events") or [])
    preview_url = await mint_signed_url(pdf_path)

    return SignPrefill(
        envelope_id=env["envelope_id"],
        role=signer["role"],
        signer_name=signer["name"],
        document_kinds=env.get("document_kinds") or [],
        preview_url=preview_url,
        already_signed=signer.get("status") == "completed",
    )


@router.post("/{token}", response_model=SubmitSignatureResponse)
async def submit_signature(
    token: str, body: SubmitSignatureRequest, request: Request
) -> SubmitSignatureResponse:
    _validate_token(token)
    if not body.consent_accepted:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Consent is required to sign.")
    if not body.signature_image_base64 and not body.typed_name:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Provide either a drawn signature or a typed name."
        )

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

    env = await asyncio.to_thread(_fetch)
    if not env:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not recognised.")

    signers: list[dict[str, Any]] = env.get("signers") or []
    signer_idx = next((i for i, s in enumerate(signers) if s.get("public_token") == token), None)
    if signer_idx is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not recognised.")
    signer = signers[signer_idx]

    expires_at = signer.get("public_token_expires_at")
    if expires_at and datetime.fromisoformat(expires_at.replace("Z", "+00:00")) < datetime.now(UTC):
        raise HTTPException(status.HTTP_410_GONE, "This signing link has expired.")
    if signer.get("status") == "completed":
        # Idempotent re-POST — friendly no-op, mirrors onboarding_public.py.
        return SubmitSignatureResponse(
            status="signed",
            next="completed" if env.get("status") == "completed" else "awaiting_next_signer",
        )

    events: list[dict[str, Any]] = env.get("events") or []
    source_meta = _source_metadata(events)
    pdf_path = _current_pdf_path(events)

    try:
        pdf_bytes = await download_pdf(pdf_path)
    except Exception as exc:  # noqa: BLE001
        log.warning("esign.sign_download_failed path=%s err=%s", pdf_path, exc)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Couldn't load the document.") from exc

    now = datetime.now(UTC)
    try:
        stamped = stamp_signature(
            pdf_bytes,
            role=signer["role"],
            signer_name=signer["name"],
            signature_image_base64=body.signature_image_base64,
            typed_name=body.typed_name,
            signed_at=now,
        )
    except SignatureImageError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    now_iso = now.isoformat()
    ip_address = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")

    signers[signer_idx] = {
        **signer,
        "status": "completed",
        "completed_at": now_iso,
        "consent_accepted_at": now_iso,
        "ip_address": ip_address,
        "user_agent": user_agent,
    }

    remaining = [s for s in signers if s.get("status") != "completed"]
    prefix = source_meta["pdf_path"].rsplit(".pdf", 1)[0]

    if remaining:
        working_path = f"{prefix}_esign_wip.pdf"
        await upload_pdf(working_path, stamped)
        new_events = events + [
            {
                "at": now_iso,
                "event": "signer_completed",
                "signer_role": signer["role"],
                "pdf_path": working_path,
            }
        ]
        await asyncio.to_thread(
            lambda: svc.table("onboarding_signing_envelopes")
            .update({"signers": signers, "events": new_events})
            .eq("envelope_id", env["envelope_id"])
            .execute()
        )

        next_signer = min(remaining, key=lambda s: s.get("routing_order", 1))
        settings = get_settings()
        await send_event(
            "esign/signer_turn",
            {
                "org_id": env["org_id"],
                "run_id": env["run_id"],
                "envelope_id": env["envelope_id"],
                "document_kinds": env.get("document_kinds") or [],
                "signer_email": next_signer["email"],
                "signer_name": next_signer["name"],
                "signer_role": next_signer["role"],
                "signing_url": f"{settings.app_url.rstrip('/')}/sign/{next_signer['public_token']}",
            },
        )
        return SubmitSignatureResponse(status="signed", next="awaiting_next_signer")

    # Last signer — append the audit page and finalize.
    entries = [
        AuditEntry(
            role=s["role"],
            name=s["name"],
            email=s["email"],
            ip_address=s.get("ip_address", "unknown"),
            user_agent=s.get("user_agent", "unknown"),
            consent_accepted_at=datetime.fromisoformat(
                (s.get("consent_accepted_at") or now_iso).replace("Z", "+00:00")
            ),
            completed_at=datetime.fromisoformat((s.get("completed_at") or now_iso).replace("Z", "+00:00")),
        )
        for s in signers
    ]
    final_bytes = append_audit_page(
        stamped, document_sha256=source_meta["document_sha256"], entries=entries
    )
    final_path = f"{prefix}_signed.pdf"
    await upload_pdf(final_path, final_bytes)

    new_events = events + [
        {"at": now_iso, "event": "envelope_completed", "pdf_path": final_path}
    ]
    await asyncio.to_thread(
        lambda: svc.table("onboarding_signing_envelopes")
        .update(
            {
                "signers": signers,
                "events": new_events,
                "status": "completed",
                "completed_at": now_iso,
                "signed_pdf_path": final_path,
            }
        )
        .eq("envelope_id", env["envelope_id"])
        .execute()
    )
    await asyncio.to_thread(
        lambda: svc.table("onboarding_documents")
        .update(
            {
                "sign_status": "signed_by_candidate",
                "esign_status": "completed",
                "esign_completed_at": now_iso,
                "signed_pdf_path": final_path,
            }
        )
        .eq("run_id", env["run_id"])
        .in_("kind", env.get("document_kinds") or [])
        .execute()
    )
    await asyncio.to_thread(
        lambda: svc.table("onboarding_events")
        .insert(
            {
                "org_id": env["org_id"],
                "run_id": env["run_id"],
                "actor_kind": "candidate" if signer["role"] == "candidate" else "hr",
                "event_type": "esign_envelope_completed",
                "message": f"All signers completed. Final signed document at {final_path}.",
                "metadata": {"envelope_id": env["envelope_id"]},
            }
        )
        .execute()
    )

    await send_event(
        source_meta["completion_event"],
        {"onboarding_run_id": env["run_id"], "org_id": env["org_id"]},
    )
    return SubmitSignatureResponse(status="signed", next="completed")
