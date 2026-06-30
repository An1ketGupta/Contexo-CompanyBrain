"""DocuSeal webhook receiver.

Endpoint:    POST /docuseal/webhook
Auth:        HMAC-SHA256 (X-Docuseal-Signature) — no JWT
Side effects: updates onboarding_signing_envelopes + onboarding_documents,
              kicks the agent forward on completion.

Security: we reject anything without a valid signature. Body length is
capped at 5MB (DocuSeal payloads are typically <50KB — well within the
cap) to avoid amplification attacks.

We deliberately return 200 even for "unknown submission" / "ignored" cases.
DocuSeal retries failed webhooks; returning 4xx would cause infinite
re-delivery for submissions we don't own (e.g. another tenant or a stale
webhook target from a previous integration test).
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request, status

from app.services.integrations.docuseal import (
    DocuSealError,
    ingest_webhook_event,
    verify_webhook_signature,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/docuseal", tags=["docuseal-webhook"])

MAX_BODY_BYTES = 5 * 1024 * 1024


@router.post("/webhook")
async def docuseal_webhook(request: Request) -> dict[str, str]:
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Webhook body too large.",
        )

    # DocuSeal signs the body with HMAC-SHA256. Header name varies by
    # version — the verifier accepts both lower-case and the legacy
    # variants. FastAPI's header lookup is case-insensitive.
    sig = (
        request.headers.get("x-docuseal-signature")
        or request.headers.get("docuseal-signature")
    )
    if not verify_webhook_signature(body=raw, signature_header=sig):
        log.warning(
            "docuseal.webhook_bad_signature ip=%s",
            request.client.host if request.client else "?",
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid signature.")

    try:
        body = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        log.warning("docuseal.webhook_bad_json err=%s", exc)
        # 200 to stop the retry; a malformed payload won't get fixed by retry.
        return {"status": "ignored"}

    try:
        result = await ingest_webhook_event(body=body)
    except DocuSealError as exc:
        log.warning("docuseal.webhook_ingest_error err=%s", exc)
        # Still 200 — we don't want DocuSeal to retry forever on a content
        # bug. The error is in our logs + Sentry for follow-up.
        return {"status": "error", "detail": str(exc)[:200]}

    return {"status": result.get("status", "ok")}
