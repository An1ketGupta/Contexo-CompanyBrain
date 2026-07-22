"""Public, no-API-key route — the signing token in the URL is the credential.

Just a prefill lookup now: it maps our own `public_token` to the signer's
Documenso embed token so apps/web's /sign/{token} page can render Documenso's
signer inline. The actual signature capture + PDF sealing happen in Documenso;
completion comes back via the /webhooks/documenso receiver, not a POST here.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status

from app import documenso_client as documenso
from app.database import get_service_client
from app.models import SignPrefill

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sign", tags=["public-sign"])


def _validate_token(token: str) -> None:
    try:
        uuid.UUID(token)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Malformed token.") from exc


def _find_signer(signers: list[dict[str, Any]], token: str) -> dict[str, Any] | None:
    return next((s for s in signers if s.get("public_token") == token), None)


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

    signer = _find_signer(env.get("signers") or [], token)
    if not signer:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not recognised.")

    expires_at = signer.get("public_token_expires_at")
    if expires_at and datetime.fromisoformat(expires_at.replace("Z", "+00:00")) < datetime.now(UTC):
        raise HTTPException(status.HTTP_410_GONE, "This signing link has expired.")

    if env.get("status") in ("voided", "declined", "expired"):
        raise HTTPException(status.HTTP_410_GONE, f"This envelope is {env['status']}.")

    documenso_token = signer.get("documenso_token")
    if not documenso_token:
        # Historical inhouse/docuseal rows don't carry a Documenso token.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This document isn't available for online signing.",
        )

    return SignPrefill(
        envelope_id=env["envelope_id"],
        role=signer["role"],
        signer_name=signer["name"],
        document_kinds=env.get("document_kinds") or [],
        documenso_host=documenso.public_url(),
        documenso_token=str(documenso_token),
        already_signed=signer.get("status") == "completed",
    )
