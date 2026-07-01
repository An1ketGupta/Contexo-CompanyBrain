"""Internal API — apps/api calls this to create/void signing envelopes.
Authenticated via X-Esign-Api-Key (see app/auth.py), not a candidate/HR JWT —
this service has no notion of NirnayaIQ user accounts."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import verify_internal_api_key
from app.config import get_settings
from app.database import get_service_client
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

    try:
        pdf_bytes = await download_pdf(body.storage_path)
    except Exception as exc:  # noqa: BLE001
        log.warning("esign.envelope_source_download_failed path=%s err=%s", body.storage_path, exc)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Couldn't download the source PDF at {body.storage_path}.",
        ) from exc
    document_sha256 = hashlib.sha256(pdf_bytes).hexdigest()

    sorted_signers = sorted(body.signers, key=lambda s: s.routing_order)
    settings = get_settings()
    now_iso = datetime.now(UTC).isoformat()

    enriched: list[dict[str, Any]] = []
    for s in sorted_signers:
        token = str(uuid.uuid4())
        enriched.append(
            {
                "role": s.role,
                "email": s.email,
                "name": s.name,
                "routing_order": s.routing_order,
                "status": "pending",
                "public_token": token,
                "public_token_expires_at": expires_at_iso(PUBLIC_TOKEN_TTL_DAYS),
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
                "provider": "inhouse",
                "envelope_id": envelope_id,
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
                        "document_sha256": document_sha256,
                    }
                ],
            }
        )
        .execute()
    )

    return CreateEnvelopeResponse(
        envelope_id=envelope_id,
        signers=[
            SignerOut(
                role=s["role"],
                email=s["email"],
                name=s["name"],
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
        .eq("provider", "inhouse")
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
        .update({"status": "voided", "voided_at": datetime.now(UTC).isoformat()})
        .eq("envelope_id", envelope_id)
        .eq("provider", "inhouse")
        .not_.in_("status", ["completed", "declined", "voided", "expired"])
        .execute()
    )
    voided = bool(res.data)
    return {"status": "voided" if voided else "no_op"}
