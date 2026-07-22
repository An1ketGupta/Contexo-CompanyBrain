"""Inbound Documenso webhook receiver — the completion half of the adapter.

Documenso fires DOCUMENT_* events as recipients sign. We reconcile those
against our own onboarding_signing_envelopes row and drive the SAME outward
effects the old in-house signer produced, so apps/api's Inngest agents are
untouched:

  • a signer finished, another is next  → fire `esign/signer_turn`
  • every signer finished (DOCUMENT_COMPLETED) → download the PAdES-sealed PDF,
    store it at the run's signed_pdf_path, flip onboarding_documents +
    onboarding_signing_envelopes to completed, fire the envelope's
    completion_event (onboarding_v2/loi_signed_uploaded or esign_completed).

Design note: we key transitions off the *recipient statuses in the payload*
(matched by email), not off exact event-name strings, so a v2 event-name
change doesn't silently break routing. DOCUMENT_COMPLETED is the definite
finalize trigger. Idempotent: retries after completion are a no-op.

⚠️ Verify the signature header + payload shape against your Documenso version
(see services/documenso/README.md). Verification is isolated in `_verify`.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from app import documenso_client as documenso
from app.config import get_settings
from app.database import get_service_client
from app.inngest_events import send_event
from app.storage import upload_pdf

log = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Recipient signingStatus values Documenso uses for "this person is done".
_DONE_STATUSES = {"SIGNED", "COMPLETED"}
# Envelope-level event that means every signer is done.
_COMPLETED_EVENTS = {"DOCUMENT_COMPLETED", "ENVELOPE_COMPLETED"}


def _verify(request_body: bytes, headers: dict[str, str]) -> None:
    """Reject unless the request carries the shared webhook secret.

    Documenso sends the secret configured on the webhook. Different versions
    have used a plain `X-Documenso-Secret` header equal to the secret. If your
    instance signs the body with HMAC instead, swap the comparison here — this
    is the single place webhook trust is established.
    """
    settings = get_settings()
    secret = settings.documenso_webhook_secret
    if not secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Webhook secret not configured.")
    provided = headers.get("x-documenso-secret") or headers.get("x-webhook-secret") or ""
    if not hmac.compare_digest(provided, secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook signature.")


def _correlation_id(payload: dict[str, Any]) -> str | None:
    inner = payload.get("payload") or payload
    return (
        inner.get("id")
        or inner.get("envelopeId")
        or inner.get("documentId")
        or inner.get("externalId")
    )


def _payload_recipients(payload: dict[str, Any]) -> list[dict[str, Any]]:
    inner = payload.get("payload") or payload
    return inner.get("recipients") or inner.get("Recipient") or []


def _source_event(events: list[dict[str, Any]]) -> dict[str, Any]:
    for e in events:
        if isinstance(e, dict) and e.get("event") == "envelope_created":
            return e
    raise RuntimeError("envelope missing envelope_created event")


@router.post("/documenso")
async def documenso_webhook(request: Request) -> dict[str, str]:
    raw = await request.body()
    _verify(raw, {k.lower(): v for k, v in request.headers.items()})

    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid JSON body.") from exc

    event_type = str(payload.get("event") or payload.get("type") or "").upper()
    documenso_envelope_id = _correlation_id(payload)
    if not documenso_envelope_id:
        # Not an envelope we can correlate (e.g. a template event) — ack so
        # Documenso stops retrying.
        return {"status": "ignored"}

    svc = get_service_client()
    env = await asyncio.to_thread(
        lambda: svc.table("onboarding_signing_envelopes")
        .select("*")
        .eq("documenso_envelope_id", str(documenso_envelope_id))
        .eq("provider", "documenso")
        .maybe_single()
        .execute()
    )
    env = env.data if env else None
    if not env:
        return {"status": "unknown_envelope"}
    if env.get("status") in ("completed", "voided", "declined", "expired"):
        return {"status": "no_op"}  # idempotent: already terminal

    # Reconcile our signer statuses from the payload (match by email).
    done_emails = {
        str(r.get("email", "")).strip().lower()
        for r in _payload_recipients(payload)
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

    all_done = event_type in _COMPLETED_EVENTS or all(
        s.get("status") == "completed" for s in signers
    )

    if not all_done:
        # Interim: persist progress and, if a signer just finished and another
        # is pending, hand routing back to apps/api via esign/signer_turn.
        if changed:
            await asyncio.to_thread(
                lambda: svc.table("onboarding_signing_envelopes")
                .update({"signers": signers})
                .eq("envelope_id", env["envelope_id"])
                .execute()
            )
        pending = [s for s in signers if s.get("status") != "completed"]
        if changed and pending:
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
        return {"status": "in_progress"}

    # ── Terminal: every signer is done. Seal-fetch → store → finalize. ──────
    source_meta = _source_event(env.get("events") or [])
    prefix = source_meta["pdf_path"].rsplit(".pdf", 1)[0]
    final_path = f"{prefix}_signed.pdf"

    try:
        sealed = await documenso.download_signed_pdf(str(documenso_envelope_id))
        await upload_pdf(final_path, sealed)
    except Exception as exc:  # noqa: BLE001
        # Don't ack: let Documenso retry the webhook so we get another shot at
        # fetching the sealed PDF (its storage write may still be settling).
        log.warning("esign.documenso_seal_fetch_failed envelope=%s err=%s", documenso_envelope_id, exc)
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
                "actor_kind": "candidate",
                "event_type": "esign_envelope_completed",
                "message": f"All signers completed via Documenso. Sealed document at {final_path}.",
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
    return {"status": "completed"}
