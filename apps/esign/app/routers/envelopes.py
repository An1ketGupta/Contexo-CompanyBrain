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
from app.storage import download_pdf, expires_at_iso, upload_pdf
from app.inngest_events import send_event
from app.routers.webhooks import _DONE_STATUSES, _source_event

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/envelopes", tags=["envelopes"], dependencies=[Depends(verify_internal_api_key)]
)

PUBLIC_TOKEN_TTL_DAYS = 14

# The envelope title is what the *signer* reads above the document in the
# embedded viewer, so it has to be a sentence a candidate understands rather
# than our internal kind slugs. Keep in sync with apps/web's
# lib/onboarding-documents.ts.
DOCUMENT_KIND_LABELS = {
    "loi": "Letter of Intent",
    "appointment_letter": "Appointment Letter",
    "nda": "NDA",
    "induction": "Induction Document",
    "offer_bundle": "Offer Bundle",
}


def _envelope_title(kinds: list[str], signer_name: str) -> str:
    """e.g. "Letter of Intent — Aniket Gupta".

    The run_id used to be the suffix; it made the title unreadable for the one
    person who actually sees it, and traceability doesn't depend on it —
    `onboarding_signing_envelopes` maps documenso_envelope_id → run_id.
    """
    labels = [
        DOCUMENT_KIND_LABELS.get(k, k.replace("_", " ").title()) for k in kinds
    ]
    return f"{' & '.join(labels) or 'Document'} — {signer_name}"


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

    title = _envelope_title(body.document_kinds, sorted_signers[0].name)
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


@router.post("/{envelope_id}/reconcile")
async def reconcile_envelope(envelope_id: str) -> dict[str, Any]:
    svc = get_service_client()
    env_res = await asyncio.to_thread(
        lambda: svc.table("onboarding_signing_envelopes")
        .select("*")
        .eq("envelope_id", envelope_id)
        .eq("provider", "documenso")
        .maybe_single()
        .execute()
    )
    env = env_res.data if env_res else None
    if not env:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Envelope not found.")

    if env.get("status") in ("completed", "voided", "declined", "expired"):
        return {"status": "no_op", "envelope_status": env.get("status")}

    documenso_envelope_id = env.get("documenso_envelope_id")
    if not documenso_envelope_id:
        return {"status": "no_op", "envelope_status": env.get("status")}

    try:
        detail = await documenso._get_envelope(str(documenso_envelope_id), cfg=documenso._require_configured())
    except (documenso.DocumensoError, documenso.DocumensoUnavailable) as exc:
        log.warning("esign.documenso_reconcile_fetch_failed envelope=%s err=%s", envelope_id, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Documenso fetch failed: {exc}") from exc

    done_emails = {
        str(r.get("email", "")).strip().lower()
        for r in (detail.get("recipients") or [])
        if str(r.get("signingStatus") or r.get("status") or "").upper() in _DONE_STATUSES
    }

    signers: list[dict[str, Any]] = env.get("signers") or []
    now_iso = datetime.now(UTC).isoformat()
    changed = False
    for s in signers:
        if s.get("status") != "completed" and s.get("email", "").strip().lower() in done_emails:
            s["status"] = "completed"
            s["completed_at"] = now_iso
            changed = True

    documenso_status = str(detail.get("status") or "").upper()
    all_done = documenso_status in ("COMPLETED",) or all(
        s.get("status") == "completed" for s in signers
    )

    if not all_done:
        if changed:
            await asyncio.to_thread(
                lambda: svc.table("onboarding_signing_envelopes")
                .update({"signers": signers})
                .eq("envelope_id", envelope_id)
                .execute()
            )
            pending = [s for s in signers if s.get("status") != "completed"]
            if pending:
                nxt = min(pending, key=lambda s: s.get("routing_order", 1))
                settings = get_settings()
                await send_event(
                    "esign/signer_turn",
                    {
                        "org_id": env["org_id"],
                        "run_id": env["run_id"],
                        "envelope_id": env["envelope_id"],
                        "document_kinds": env.get("document_kinds") or [],
                        "signer_email": nxt["email"],
                        "signer_name": nxt["name"],
                        "signer_role": nxt["role"],
                        "signing_url": f"{settings.app_url.rstrip('/')}/sign/{nxt['public_token']}",
                    },
                )
        return {
            "status": "reconciled",
            "changed": changed,
            "signers": signers,
            "envelope_status": env.get("status", "sent")
        }

    source_meta = _source_event(env.get("events") or [])
    prefix = source_meta["pdf_path"].rsplit(".pdf", 1)[0]
    final_path = f"{prefix}_signed.pdf"

    try:
        sealed = await documenso.download_signed_pdf(str(documenso_envelope_id))
        await upload_pdf(final_path, sealed)
    except Exception as exc:  # noqa: BLE001
        log.warning("esign.documenso_reconcile_seal_fetch_failed envelope=%s err=%s", documenso_envelope_id, exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Signed PDF not ready yet; retry.",
        ) from exc

    new_events = (env.get("events") or []) + [
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
        .eq("envelope_id", envelope_id)
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
                "actor_kind": "candidate",
                "event_type": "esign_envelope_completed",
                "message": f"All signers completed via Documenso (Reconciled). Sealed document at {final_path}.",
                "metadata": {
                    "envelope_id": env["envelope_id"],
                    "documenso_envelope_id": documenso_envelope_id,
                },
            }
        )
        .execute()
    )

    await send_event(
        source_meta["completion_event"],
        {"onboarding_run_id": env["run_id"], "org_id": env["org_id"]},
    )
    
    return {
        "status": "reconciled",
        "changed": changed,
        "signers": signers,
        "envelope_status": "completed"
    }
