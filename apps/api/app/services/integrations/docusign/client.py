"""DocuSign REST client — JWT grant + envelope CRUD + webhook ingest.

See the module docstring in __init__.py for the design rationale (small
surface area, no SDK lock-in).

Lifecycle of an envelope from our side:

    create_signing_envelope(run, [al_pdf, nda_pdf])
        ├─ POST /envelopes  (status=sent)
        ├─ POST /views/recipient  → embedded-signing URL
        └─ INSERT onboarding_signing_envelopes (status=sent)

    [candidate visits URL, signs] → DocuSign POSTs to /docusign/webhook
        verify_webhook_signature(headers, body) → bool
        ingest_webhook_event(envelope_id, payload)
            ├─ UPDATE onboarding_signing_envelopes (status, *_at)
            ├─ UPDATE onboarding_documents (sign_status, docusign_status)
            └─ INSERT onboarding_events
            └─ if completed → send Inngest event onboarding_v2/docusign_signed
                              and download the signed PDF into Storage

    void_envelopes_for_run(run_id, reason)
        ├─ for each active envelope: PUT /envelopes/{id} (status=voided)
        └─ UPDATE onboarding_signing_envelopes (status=voided)
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import get_settings
from app.database import get_service_client

log = logging.getLogger(__name__)


class DocuSignError(RuntimeError):
    """Deterministic DocuSign API failure — 4xx, bad envelope, invalid JWT."""


class DocuSignUnavailable(RuntimeError):
    """DocuSign isn't configured. The agent falls back to plain-email-with-PDF
    delivery and surfaces a "configure DocuSign in Settings" banner."""


# Module-level access-token cache. JWT-grant tokens last 1 hour; refresh
# slightly early so a concurrent call doesn't hit a 401.
_TOKEN_CACHE: dict[str, Any] = {"access_token": None, "expires_at": 0.0}
_TOKEN_REFRESH_SAFETY_SECONDS = 60


# ── Config helpers ─────────────────────────────────────────────────────────

def _config() -> dict[str, str]:
    s = get_settings()
    cfg = {
        "integration_key": getattr(s, "docusign_integration_key", "") or "",
        "user_id": getattr(s, "docusign_user_id", "") or "",
        "account_id": getattr(s, "docusign_account_id", "") or "",
        "base_url": (getattr(s, "docusign_base_url", "") or "").rstrip("/"),
        "rsa_private_key": getattr(s, "docusign_rsa_private_key", "") or "",
        "webhook_hmac_key": getattr(s, "docusign_webhook_hmac_key", "") or "",
        "auth_server": (
            getattr(s, "docusign_auth_server", "") or "account-d.docusign.com"
        ),
    }
    return cfg


def _require_configured() -> dict[str, str]:
    cfg = _config()
    missing = [
        k for k in ("integration_key", "user_id", "account_id", "base_url", "rsa_private_key")
        if not cfg.get(k)
    ]
    if missing:
        raise DocuSignUnavailable(
            "DocuSign not fully configured. Missing: " + ", ".join(missing)
        )
    return cfg


# ── JWT grant: get an access token ─────────────────────────────────────────

def _sign_jwt(cfg: dict[str, str]) -> str:
    """Build & sign a DocuSign JWT-grant assertion. Pure function so we can
    test in isolation without hitting the network."""
    try:
        import jwt  # PyJWT — already in deps
    except ImportError as exc:
        raise DocuSignUnavailable(
            "PyJWT not installed; required for DocuSign JWT grant."
        ) from exc

    now = int(time.time())
    payload = {
        "iss": cfg["integration_key"],
        "sub": cfg["user_id"],
        "aud": cfg["auth_server"],
        "iat": now,
        "exp": now + 3600,
        "scope": "signature impersonation",
    }
    return jwt.encode(payload, cfg["rsa_private_key"], algorithm="RS256")


async def _get_access_token() -> str:
    cfg = _require_configured()
    if (
        _TOKEN_CACHE["access_token"]
        and _TOKEN_CACHE["expires_at"] > time.time() + _TOKEN_REFRESH_SAFETY_SECONDS
    ):
        return _TOKEN_CACHE["access_token"]

    assertion = await asyncio.to_thread(_sign_jwt, cfg)
    url = f"https://{cfg['auth_server']}/oauth/token"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
        )
    if resp.status_code == 400 and "consent_required" in (resp.text or ""):
        raise DocuSignError(
            "DocuSign consent_required — visit the admin consent URL once "
            "to grant the impersonation user permission."
        )
    if resp.status_code != 200:
        raise DocuSignError(
            f"DocuSign token exchange failed: {resp.status_code} {resp.text[:200]}"
        )
    body = resp.json()
    _TOKEN_CACHE["access_token"] = body["access_token"]
    _TOKEN_CACHE["expires_at"] = time.time() + int(body.get("expires_in", 3600))
    return _TOKEN_CACHE["access_token"]


# ── HTTP client ────────────────────────────────────────────────────────────

class DocuSignClient:
    """Thin REST wrapper. One instance per request keeps the connection
    pool tidy; the access token is module-level so we don't re-fetch."""

    def __init__(self, *, cfg: dict[str, str] | None = None) -> None:
        self.cfg = cfg or _require_configured()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        token = await _get_access_token()
        url = f"{self.cfg['base_url']}/v2.1/accounts/{self.cfg['account_id']}{path}"
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(
                method, url, headers=headers, json=json_body
            )
        if resp.status_code >= 400:
            snippet = (resp.text or "")[:500]
            raise DocuSignError(
                f"DocuSign {method} {path} → {resp.status_code}: {snippet}"
            )
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()


# ── Public API ─────────────────────────────────────────────────────────────

async def create_signing_envelope(
    *,
    org_id: str,
    run_id: str,
    recipient_email: str,
    recipient_name: str,
    documents: list[dict[str, Any]],
    email_subject: str,
    email_body: str | None = None,
    return_url: str | None = None,
) -> dict[str, Any]:
    """Create a DocuSign envelope wrapping one or more PDFs and return the
    embedded-signing URL.

    `documents` is a list of {kind, pdf_bytes, name} dicts. We attach all
    in one envelope so the candidate signs both AL + NDA together — the
    "bundle" notion from the spec.

    Returns:
        {
            "envelope_id": str,
            "signing_url": str,
            "signing_url_expires_at": ISO timestamp,
        }
    """
    if not documents:
        raise DocuSignError("No documents supplied for envelope.")

    client = DocuSignClient()

    docs_payload = []
    for idx, d in enumerate(documents, start=1):
        pdf_b64 = base64.b64encode(d["pdf_bytes"]).decode("ascii")
        docs_payload.append(
            {
                "documentId": str(idx),
                "name": d.get("name") or f"{d['kind']}.pdf",
                "fileExtension": "pdf",
                "documentBase64": pdf_b64,
            }
        )

    # Single signer (the candidate). We use clientUserId to mark this as an
    # embedded signer so DocuSign returns a signing-ceremony URL.
    client_user_id = f"candidate-{run_id}"
    signer = {
        "email": recipient_email,
        "name": recipient_name,
        "recipientId": "1",
        "routingOrder": "1",
        "clientUserId": client_user_id,
        # Auto-place a signature anchor at "Sign here" text in the PDF; if
        # the template doesn't include the anchor, the candidate can still
        # add their signature via DocuSign's free-form placement.
        "tabs": {
            "signHereTabs": [
                {
                    "anchorString": "Sign here",
                    "anchorUnits": "pixels",
                    "anchorXOffset": "0",
                    "anchorYOffset": "0",
                    "anchorIgnoreIfNotPresent": "true",
                }
            ]
        },
    }

    envelope_payload = {
        "emailSubject": email_subject,
        "emailBlurb": email_body or email_subject,
        "documents": docs_payload,
        "recipients": {"signers": [signer]},
        "status": "sent",
    }

    log.info(
        "docusign.create_envelope run=%s recipient=%s doc_count=%d",
        run_id, recipient_email, len(documents),
    )
    res = await client._request("POST", "/envelopes", json_body=envelope_payload)
    envelope_id = res.get("envelopeId")
    if not envelope_id:
        raise DocuSignError(f"DocuSign envelope creation returned no envelopeId: {res}")

    signing_url_obj = await client._request(
        "POST",
        f"/envelopes/{envelope_id}/views/recipient",
        json_body={
            "returnUrl": return_url or "https://nirnayaiq.com/candidate/done",
            "authenticationMethod": "none",
            "email": recipient_email,
            "userName": recipient_name,
            "clientUserId": client_user_id,
        },
    )
    signing_url = signing_url_obj.get("url")
    if not signing_url:
        raise DocuSignError("DocuSign signing-URL response missing url field.")

    # DocuSign signing URLs are valid for 5 minutes — re-mint on each
    # candidate visit by hitting GET /onboarding/runs/{id}/docusign-url.
    expires_at = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()

    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("onboarding_signing_envelopes").insert(
            {
                "org_id": org_id,
                "run_id": run_id,
                "provider": "docusign",
                "envelope_id": envelope_id,
                "document_kinds": [d["kind"] for d in documents],
                "status": "sent",
                "recipient_email": recipient_email,
                "recipient_name": recipient_name,
                "signing_url": signing_url,
                "signing_url_expires_at": expires_at,
                "sent_at": datetime.now(UTC).isoformat(),
            }
        ).execute()
    )

    return {
        "envelope_id": envelope_id,
        "signing_url": signing_url,
        "signing_url_expires_at": expires_at,
    }


async def get_signing_url(
    *,
    envelope_id: str,
    recipient_email: str,
    recipient_name: str,
    return_url: str,
    client_user_id: str | None = None,
) -> str:
    """Re-mint a fresh signing-ceremony URL for an existing envelope. Used
    by the candidate portal when the cached URL has expired (5min TTL).

    `client_user_id` must match exactly what was used when the envelope was
    created — otherwise DocuSign returns "user not an embedded signer".
    Defaults to the legacy candidate-only shape so the AL+NDA flow keeps
    working without changes.
    """
    client = DocuSignClient()
    if client_user_id is None:
        client_user_id_seed = hashlib.sha1(f"{envelope_id}".encode()).hexdigest()[:16]
        client_user_id = f"candidate-{client_user_id_seed}"
    res = await client._request(
        "POST",
        f"/envelopes/{envelope_id}/views/recipient",
        json_body={
            "returnUrl": return_url,
            "authenticationMethod": "none",
            "email": recipient_email,
            "userName": recipient_name,
            "clientUserId": client_user_id,
        },
    )
    url = res.get("url")
    if not url:
        raise DocuSignError("DocuSign returned no signing URL.")
    return url


# ── Multi-signer routed envelopes (used by LOI: HR → candidate) ───────────


def _embedded_client_user_id(*, run_id: str, role: str) -> str:
    """Stable per-signer clientUserId so we can re-mint the same embedded
    signing URL on demand. DocuSign requires the exact same value at view
    creation as at envelope creation."""
    return f"{role}-{run_id}"


async def create_routed_signing_envelope(
    *,
    org_id: str,
    run_id: str,
    document_kinds: list[str],
    documents: list[dict[str, Any]],
    signers: list[dict[str, Any]],
    email_subject: str,
    email_body: str | None = None,
) -> dict[str, Any]:
    """Create a DocuSign envelope wrapping one or more PDFs and routed to
    multiple signers in order (lowest routing_order signs first).

    `signers` is a list of {role, email, name, routing_order} dicts. Each
    signer is registered as an *embedded* signer (we mint our own signing
    URL and surface it via our UI / our branded email). This gives us full
    control over delivery — DocuSign won't send its own emails because every
    recipient has a clientUserId set.

    `document_kinds` lists the onboarding kinds wrapped in this envelope
    (e.g. ["loi"]). Used for the signing_envelopes row + webhook fan-out.

    Returns:
        {
            "envelope_id": str,
            "signing_url_expires_at": ISO timestamp (5 minutes from now),
            "signers": [
                {role, email, name, routing_order, recipient_id,
                 client_user_id, signing_url, status},
                ...
            ],
        }
    """
    if not documents:
        raise DocuSignError("No documents supplied for envelope.")
    if not signers:
        raise DocuSignError("No signers supplied for envelope.")

    client = DocuSignClient()

    docs_payload = []
    for idx, d in enumerate(documents, start=1):
        pdf_b64 = base64.b64encode(d["pdf_bytes"]).decode("ascii")
        docs_payload.append(
            {
                "documentId": str(idx),
                "name": d.get("name") or f"{d['kind']}.pdf",
                "fileExtension": "pdf",
                "documentBase64": pdf_b64,
            }
        )

    sorted_signers = sorted(signers, key=lambda s: int(s.get("routing_order", 1)))

    signer_payload = []
    enriched: list[dict[str, Any]] = []
    for idx, s in enumerate(sorted_signers, start=1):
        role = s["role"]
        is_embedded = bool(s.get("embedded", True))
        client_user_id: str | None = (
            _embedded_client_user_id(run_id=run_id, role=role) if is_embedded else None
        )
        payload: dict[str, Any] = {
            "email": s["email"],
            "name": s["name"],
            "recipientId": str(idx),
            "routingOrder": str(int(s.get("routing_order", idx))),
            "roleName": role,
            "tabs": {
                "signHereTabs": [
                    {
                        "anchorString": s.get("anchor_string", f"[{role.upper()} SIGN]"),
                        "anchorUnits": "pixels",
                        "anchorXOffset": "0",
                        "anchorYOffset": "0",
                        "anchorIgnoreIfNotPresent": "true",
                    },
                    # Fallback anchor — many templates just have "Sign here"
                    # or the signer's role keyword. Both anchors set
                    # anchorIgnoreIfNotPresent so a missing anchor is OK;
                    # DocuSign lets the signer free-place if needed.
                    {
                        "anchorString": "Sign here",
                        "anchorUnits": "pixels",
                        "anchorIgnoreIfNotPresent": "true",
                    },
                ]
            },
        }
        # Embedded signers get a clientUserId — we mint the signing URL
        # on demand from our UI. Remote signers (no clientUserId) trigger
        # DocuSign's own email delivery, which is exactly what we want for
        # candidates who aren't logged into NirnayaIQ.
        if client_user_id:
            payload["clientUserId"] = client_user_id
        signer_payload.append(payload)
        enriched.append(
            {
                "role": role,
                "email": s["email"],
                "name": s["name"],
                "routing_order": int(s.get("routing_order", idx)),
                "recipient_id": str(idx),
                "client_user_id": client_user_id,
                "embedded": is_embedded,
                "status": "sent",
                "signing_url": None,
            }
        )

    envelope_payload = {
        "emailSubject": email_subject,
        "emailBlurb": email_body or email_subject,
        "documents": docs_payload,
        "recipients": {"signers": signer_payload},
        "status": "sent",
    }

    log.info(
        "docusign.create_routed_envelope run=%s doc_count=%d signer_count=%d",
        run_id, len(documents), len(signers),
    )
    res = await client._request("POST", "/envelopes", json_body=envelope_payload)
    envelope_id = res.get("envelopeId")
    if not envelope_id:
        raise DocuSignError(f"DocuSign envelope creation returned no envelopeId: {res}")

    # Persist envelope. We store the FIRST signer (lowest routing order) as
    # the row's primary recipient — that's who can sign right now. The full
    # per-signer state lives in the `signers` JSONB column.
    first = enriched[0]
    now_iso = datetime.now(UTC).isoformat()
    expires_at = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()

    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("onboarding_signing_envelopes").insert(
            {
                "org_id": org_id,
                "run_id": run_id,
                "provider": "docusign",
                "envelope_id": envelope_id,
                "document_kinds": document_kinds,
                "status": "sent",
                "recipient_email": first["email"],
                "recipient_name": first["name"],
                "signers": enriched,
                "signing_url_expires_at": expires_at,
                "sent_at": now_iso,
            }
        ).execute()
    )

    return {
        "envelope_id": envelope_id,
        "signing_url_expires_at": expires_at,
        "signers": enriched,
    }


async def mint_signer_url(
    *,
    envelope_id: str,
    run_id: str,
    role: str,
    email: str,
    name: str,
    return_url: str,
) -> str:
    """Re-mint a fresh embedded-signing URL for a known signer on an existing
    routed envelope. Caller is responsible for verifying the requesting user
    is authorised to view this signer's link. Raises DocuSignError if the
    signer was set up as remote (no clientUserId) — DocuSign handles email
    delivery for those."""
    return await get_signing_url(
        envelope_id=envelope_id,
        recipient_email=email,
        recipient_name=name,
        return_url=return_url,
        client_user_id=_embedded_client_user_id(run_id=run_id, role=role),
    )


async def void_envelopes_for_run(
    *, org_id: str, run_id: str, reason: str,
) -> int:
    """Void every active envelope linked to a run. Returns count voided."""
    cfg = _config()
    if not cfg.get("integration_key"):
        return 0

    svc = get_service_client()
    rows = await asyncio.to_thread(
        lambda: svc.table("onboarding_signing_envelopes")
        .select("id, envelope_id, status")
        .eq("run_id", run_id)
        .not_.in_("status", ["completed", "declined", "voided", "expired"])
        .execute()
    )
    active = rows.data or []
    if not active:
        return 0

    client = DocuSignClient()
    voided = 0
    for env in active:
        try:
            await client._request(
                "PUT",
                f"/envelopes/{env['envelope_id']}",
                json_body={"status": "voided", "voidedReason": reason[:200]},
            )
            await asyncio.to_thread(
                lambda eid=env["id"]: svc.table("onboarding_signing_envelopes")
                .update(
                    {
                        "status": "voided",
                        "voided_at": datetime.now(UTC).isoformat(),
                        "decline_reason": reason[:500],
                    }
                )
                .eq("id", eid)
                .execute()
            )
            voided += 1
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "docusign.void_failed envelope=%s err=%s",
                env.get("envelope_id"), exc,
            )
    return voided


# ── Webhook ingestion ──────────────────────────────────────────────────────

def verify_webhook_signature(
    *, body: bytes, signature_header: str | None,
) -> bool:
    """Verify the X-DocuSign-Signature-1 header (HMAC-SHA256 of the body
    with the configured webhook key). Returns False on any failure — caller
    must reject the request with 401.

    Connect Key 1 is the legacy header; newer accounts use HMAC keys 1-10.
    We support the first key only; rotate by setting DOCUSIGN_WEBHOOK_HMAC_KEY.
    """
    cfg = _config()
    secret = cfg.get("webhook_hmac_key")
    if not secret or not signature_header:
        return False
    expected = base64.b64encode(
        hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("ascii")
    # Use compare_digest to avoid timing oracle.
    return hmac.compare_digest(expected, signature_header)


# Map DocuSign envelope-status to our onboarding_signing_envelopes.status.
_STATUS_MAP = {
    "sent": "sent",
    "delivered": "delivered",
    "completed": "completed",
    "declined": "declined",
    "voided": "voided",
    "expired": "expired",
}


async def ingest_webhook_event(*, body: dict[str, Any]) -> dict[str, Any]:
    """Process a DocuSign Connect webhook payload. Caller has already
    verified the HMAC signature.

    Supports both the legacy XML→JSON-converted shape and the new JSON
    Connect shape. We key on the envelope status; if we see a completed
    envelope, we download the signed PDF and stash it in Storage so the
    HR dashboard surface keeps working without further DocuSign calls.
    """
    svc = get_service_client()

    # Tolerate both shapes:
    #   { "event": "...", "data": { "envelopeId": "...", "envelopeSummary": {...} } }
    #   { "envelopeId": "...", "status": "completed", ... }
    data = body.get("data") or body
    envelope_id = (
        data.get("envelopeId")
        or data.get("envelope_id")
        or (data.get("envelopeSummary") or {}).get("envelopeId")
    )
    raw_status = (
        data.get("status")
        or (data.get("envelopeSummary") or {}).get("status")
        or body.get("event")
        or ""
    ).lower()
    our_status = _STATUS_MAP.get(raw_status, raw_status)

    if not envelope_id:
        log.warning("docusign.webhook_missing_envelope_id body=%s", json.dumps(body)[:500])
        return {"status": "ignored", "reason": "no_envelope_id"}

    # Look up the row.
    row = await asyncio.to_thread(
        lambda: svc.table("onboarding_signing_envelopes")
        .select(
            "id, org_id, run_id, status, document_kinds, "
            "recipient_email, recipient_name, signers"
        )
        .eq("envelope_id", envelope_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        log.warning("docusign.webhook_unknown_envelope envelope=%s", envelope_id)
        return {"status": "ignored", "reason": "unknown_envelope"}

    env = row.data
    update: dict[str, Any] = {"status": our_status}
    now_iso = datetime.now(UTC).isoformat()
    if our_status == "delivered":
        update["delivered_at"] = now_iso
    elif our_status == "completed":
        update["completed_at"] = now_iso
    elif our_status == "declined":
        update["declined_at"] = now_iso
        update["decline_reason"] = (
            data.get("declineReason") or data.get("decline_reason") or None
        )
    elif our_status == "voided":
        update["voided_at"] = now_iso

    # Per-signer status fan-out for routed envelopes. DocuSign Connect ships
    # the current recipient set under recipients.signers — we mirror the
    # per-signer status into our `signers` JSONB so the dashboard can show
    # "HR signed ✓, candidate pending" without another DocuSign API call.
    existing_signers = env.get("signers") or []
    if isinstance(existing_signers, list) and existing_signers:
        webhook_signers = (
            (data.get("recipients") or {}).get("signers")
            or (data.get("envelopeSummary") or {})
                .get("recipients", {})
                .get("signers")
            or []
        )
        by_email = {
            (s.get("email") or "").lower(): s for s in webhook_signers if isinstance(s, dict)
        }
        merged: list[dict[str, Any]] = []
        for sig in existing_signers:
            if not isinstance(sig, dict):
                merged.append(sig)
                continue
            wh = by_email.get((sig.get("email") or "").lower())
            if wh:
                wh_status = (wh.get("status") or "").lower() or sig.get("status")
                sig = {**sig, "status": wh_status}
                if wh_status == "completed":
                    sig["completed_at"] = wh.get("signedDateTime") or now_iso
                elif wh_status == "declined":
                    sig["declined_at"] = wh.get("declinedDateTime") or now_iso
                    sig["decline_reason"] = (
                        wh.get("declinedReason") or sig.get("decline_reason")
                    )
            merged.append(sig)
        update["signers"] = merged

    # Append event for forensic audit. The envelopes row's `events` column
    # is a JSONB array — append by reading + writing back (small list, no
    # contention issue at the rate webhooks arrive).
    existing_events_row = await asyncio.to_thread(
        lambda: svc.table("onboarding_signing_envelopes")
        .select("events")
        .eq("id", env["id"])
        .maybe_single()
        .execute()
    )
    existing_events = (existing_events_row.data or {}).get("events") or []
    if not isinstance(existing_events, list):
        existing_events = []
    existing_events.append(
        {"at": now_iso, "status": raw_status, "raw": body}
    )
    update["events"] = existing_events[-50:]  # cap retention

    await asyncio.to_thread(
        lambda: svc.table("onboarding_signing_envelopes")
        .update(update)
        .eq("id", env["id"])
        .execute()
    )

    # Mirror status on onboarding_documents so the dashboard reads work
    # without joining envelopes. Only documents in this envelope.
    kinds = env.get("document_kinds") or []
    is_loi_envelope = "loi" in kinds
    if kinds:
        # LOI envelopes are HR-then-candidate; on completion the LOI is
        # signed by both parties. AL+NDA envelopes are candidate-only today.
        completed_sign_status = "signed_by_candidate"
        declined_sign_status = "sent_to_candidate"
        sign_status = {
            "completed": completed_sign_status,
            "declined": declined_sign_status,
        }.get(our_status)
        doc_update: dict[str, Any] = {"docusign_status": our_status}
        if sign_status:
            doc_update["sign_status"] = sign_status
        if our_status == "completed":
            doc_update["docusign_completed_at"] = now_iso
        elif our_status == "declined":
            doc_update["docusign_declined_at"] = now_iso
            doc_update["docusign_decline_reason"] = update.get("decline_reason")
        await asyncio.to_thread(
            lambda: svc.table("onboarding_documents")
            .update(doc_update)
            .eq("run_id", env["run_id"])
            .in_("kind", kinds)
            .execute()
        )

    # On completion: fetch the signed PDFs and stash them in Storage so
    # subsequent dashboard reads don't require DocuSign roundtrips.
    if our_status == "completed":
        try:
            await _download_signed_documents(
                org_id=env["org_id"], run_id=env["run_id"],
                envelope_id=envelope_id, kinds=kinds,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "docusign.signed_pdf_download_failed envelope=%s err=%s",
                envelope_id, exc,
            )

        # LOI envelopes drive the run state machine differently from the
        # AL+NDA bundle: an LOI completion means HR + candidate both signed,
        # so the run is ready to move into the "send LOI to candidate"
        # step (which mints the BGV references-form link). We flip the run
        # status before kicking the agent so the resume dispatch lands in
        # _step_send_loi_to_candidate.
        if is_loi_envelope:
            try:
                await asyncio.to_thread(
                    lambda: svc.table("onboarding_runs")
                    .update(
                        {
                            "status": "loi_signed_uploaded",
                            "loi_signed_at": now_iso,
                        }
                    )
                    .eq("id", env["run_id"])
                    .execute()
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "docusign.loi_status_flip_failed envelope=%s err=%s",
                    envelope_id, exc,
                )

        # Kick the agent forward.
        try:
            import inngest

            from app.inngest.client import get_inngest_client
            inngest_client = get_inngest_client()
            event_name = (
                "onboarding_v2/loi_signed_uploaded" if is_loi_envelope
                else "onboarding_v2/docusign_signed"
            )
            await inngest_client.send(
                inngest.Event(
                    name=event_name,
                    data={"onboarding_run_id": env["run_id"], "org_id": env["org_id"]},
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("docusign.inngest_kick_failed err=%s", exc)

    # Log to onboarding_events for the timeline.
    from app.services.agents.onboarding_v2 import storage as ob_storage
    await ob_storage.log_onboarding_event(
        org_id=env["org_id"], run_id=env["run_id"],
        actor_kind="candidate" if our_status in ("completed", "declined") else "system",
        event_type=f"docusign_{our_status}",
        message=(
            "Candidate signed the offer bundle via DocuSign." if our_status == "completed"
            else f"DocuSign envelope status changed to {our_status}."
        ),
        metadata={"envelope_id": envelope_id, "raw_status": raw_status},
    )

    return {"status": "ok", "envelope_status": our_status}


async def _download_signed_documents(
    *, org_id: str, run_id: str, envelope_id: str, kinds: list[str],
) -> None:
    """Pull the signed-combined PDF from DocuSign and upload to Storage.
    Each document kind in the envelope gets its signed_pdf_path stamped.

    DocuSign returns one combined PDF (default) — we store that under a
    `_docusign_signed.pdf` suffix and point every doc's signed_pdf_path
    at the same blob. If we ever need per-document split, the API supports
    /envelopes/{id}/documents/{docId} but the combined view matches HR's
    expectation of "one signed contract".
    """
    if not kinds:
        return
    client = DocuSignClient()
    token = await _get_access_token()
    url = (
        f"{client.cfg['base_url']}/v2.1/accounts/{client.cfg['account_id']}"
        f"/envelopes/{envelope_id}/documents/combined"
    )
    async with httpx.AsyncClient(timeout=60.0) as http:
        resp = await http.get(url, headers={"Authorization": f"Bearer {token}"})
    if resp.status_code != 200 or not resp.content.startswith(b"%PDF"):
        raise DocuSignError(
            f"Signed PDF fetch returned {resp.status_code}, not a PDF."
        )
    pdf_bytes = resp.content

    from app.services.agents.onboarding_v2.storage import STORAGE_BUCKET
    svc = get_service_client()
    # Name the blob by the document kinds in the envelope so multiple
    # envelopes per run (e.g. LOI + later AL/NDA bundle) don't collide.
    blob_slug = "_".join(sorted(kinds)) or "envelope"
    path = (
        f"orgs/{org_id}/onboarding/{run_id}/{blob_slug}_docusign_signed.pdf"
    )
    await asyncio.to_thread(
        lambda: svc.storage.from_(STORAGE_BUCKET).upload(
            path=path,
            file=pdf_bytes,
            file_options={"content-type": "application/pdf", "upsert": "true"},
        )
    )
    await asyncio.to_thread(
        lambda: svc.table("onboarding_documents")
        .update({"signed_pdf_path": path})
        .eq("run_id", run_id)
        .in_("kind", kinds)
        .execute()
    )
    await asyncio.to_thread(
        lambda: svc.table("onboarding_signing_envelopes")
        .update({"signed_pdf_path": path})
        .eq("envelope_id", envelope_id)
        .execute()
    )
