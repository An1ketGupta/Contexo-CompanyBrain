"""Internal API — apps/api calls this to create/void signing envelopes.

Authenticated via X-Esign-Api-Key (see app/auth.py). This is the *adapter*
seam: the request/response contract with apps/api is unchanged from the old
in-house signer (create → signing URLs; void), but the guts now drive
Documenso. apps/api and its Inngest agents don't know Documenso exists.

create flow:
  download source PDF  → white out markers + derive SIGNATURE field coords
  → Documenso create+fields+distribute → per-recipient embed token
  → persist onboarding_signing_envelopes (provider='documenso') with our own
    public_token per signer → return {app_url}/sign/{public_token} links.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app import documenso_client as documenso
from app.auth import verify_internal_api_key
from app.config import get_settings
from app.database import get_service_client
from app.field_placement import extract_fields_and_clean
from app.models import (
    CreateEnvelopeRequest,
    CreateEnvelopeResponse,
    EnvelopeStatus,
    SignerOut,
)
from app.storage import download_pdf, expires_at_iso

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/envelopes", tags=["envelopes"], dependencies=[Depends(verify_internal_api_key)]
)

PUBLIC_TOKEN_TTL_DAYS = 14


@router.post("", response_model=CreateEnvelopeResponse, status_code=status.HTTP_201_CREATED)
async def create_envelope(body: CreateEnvelopeRequest) -> CreateEnvelopeResponse:
    if not body.signers:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "At least one signer is required.")
    if not documenso.is_configured():
        # Mirrors apps/api's gate — a half-wired deploy should surface loudly
        # so the caller can fall back to print/scan rather than half-create.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Documenso is not configured on the e-sign service.",
        )

    try:
        pdf_bytes = await download_pdf(body.storage_path)
    except Exception as exc:  # noqa: BLE001
        log.warning("esign.envelope_source_download_failed path=%s err=%s", body.storage_path, exc)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Couldn't download the source PDF at {body.storage_path}.",
        ) from exc

    sorted_signers = sorted(body.signers, key=lambda s: s.routing_order)
    roles = [s.role for s in sorted_signers]

    # White out the ◇SIGN:*◇ markers and turn them into Documenso field boxes.
    cleaned_pdf, fields = await asyncio.to_thread(
        extract_fields_and_clean, pdf_bytes, roles=roles
    )

    title = f"{', '.join(body.document_kinds) or 'Document'} — {body.run_id[:8]}"
    try:
        result = await documenso.create_and_distribute(
            title=title,
            pdf_bytes=cleaned_pdf,
            pdf_filename=f"{body.run_id}.pdf",
            recipients=[
                documenso.RecipientSpec(
                    role=s.role, email=s.email, name=s.name, signing_order=s.routing_order,
                )
                for s in sorted_signers
            ],
            fields=fields,
        )
    except documenso.DocumensoUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except documenso.DocumensoError as exc:
        log.warning("esign.documenso_create_failed run=%s err=%s", body.run_id, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Documenso rejected the envelope: {exc}") from exc

    by_role = {r.role: r for r in result.recipients}
    now_iso = datetime.now(UTC).isoformat()

    enriched: list[dict[str, Any]] = []
    for s in sorted_signers:
        r = by_role.get(s.role)
        enriched.append(
            {
                "role": s.role,
                "email": s.email,
                "name": s.name,
                "routing_order": s.routing_order,
                "status": "pending",
                "public_token": str(uuid.uuid4()),
                "public_token_expires_at": expires_at_iso(PUBLIC_TOKEN_TTL_DAYS),
                "documenso_recipient_id": r.documenso_recipient_id if r else None,
                "documenso_token": r.documenso_token if r else None,
                "completed_at": None,
            }
        )

    envelope_id = str(uuid.uuid4())
    first = enriched[0]
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("onboarding_signing_envelopes")
        .insert(
            {
                "org_id": body.org_id,
                "run_id": body.run_id,
                "provider": "documenso",
                "envelope_id": envelope_id,
                "documenso_envelope_id": result.envelope_id,
                "document_kinds": body.document_kinds,
                "status": "sent",
                "recipient_email": first["email"],
                "recipient_name": first["name"],
                "signers": enriched,
                "sent_at": now_iso,
                "events": [
                    {
                        "at": now_iso,
                        "event": "envelope_created",
                        "pdf_path": body.storage_path,
                        "completion_event": body.completion_event,
                        "documenso_envelope_id": result.envelope_id,
                    }
                ],
            }
        )
        .execute()
    )

    settings = get_settings()
    return CreateEnvelopeResponse(
        envelope_id=envelope_id,
        signers=[
            SignerOut(
                role=s["role"],
                email=s["email"],
                name=s["name"],
                # Contract preserved: our own /sign/{token} link. The page
                # embeds Documenso's signer using this signer's Documenso token.
                signing_url=f"{settings.app_url.rstrip('/')}/sign/{s['public_token']}",
            )
            for s in enriched
        ],
    )


@router.get("/{envelope_id}", response_model=EnvelopeStatus)
async def get_envelope(envelope_id: str) -> EnvelopeStatus:
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("onboarding_signing_envelopes")
        .select("envelope_id, status, signers")
        .eq("envelope_id", envelope_id)
        .eq("provider", "documenso")
        .maybe_single()
        .execute()
    )
    if not res or not res.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Envelope not found.")
    return EnvelopeStatus(
        envelope_id=res.data["envelope_id"],
        status=res.data["status"],
        signers=res.data.get("signers") or [],
    )


@router.post("/{envelope_id}/void")
async def void_envelope(envelope_id: str) -> dict[str, str]:
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("onboarding_signing_envelopes")
        .select("documenso_envelope_id, status")
        .eq("envelope_id", envelope_id)
        .eq("provider", "documenso")
        .maybe_single()
        .execute()
    )
    if not res or not res.data:
        return {"status": "no_op"}
    if res.data["status"] in ("completed", "declined", "voided", "expired"):
        return {"status": "no_op"}

    # Best-effort cancel in Documenso; our row is the source of truth for the
    # onboarding pipeline, so we void it even if the remote call fails.
    documenso_envelope_id = res.data.get("documenso_envelope_id")
    if documenso_envelope_id:
        try:
            await documenso.cancel_envelope(documenso_envelope_id)
        except (documenso.DocumensoError, documenso.DocumensoUnavailable) as exc:
            log.warning("esign.documenso_cancel_failed envelope=%s err=%s", envelope_id, exc)

    await asyncio.to_thread(
        lambda: svc.table("onboarding_signing_envelopes")
        .update({"status": "voided", "voided_at": datetime.now(UTC).isoformat()})
        .eq("envelope_id", envelope_id)
        .eq("provider", "documenso")
        .not_.in_("status", ["completed", "declined", "voided", "expired"])
        .execute()
    )
    return {"status": "voided"}
