"""DocuSeal REST client — submission CRUD + webhook ingest.

See the module docstring in __init__.py for the design rationale.

Free-tier note: self-hosted open-source DocuSeal gates every file-ingest API
endpoint (POST /submissions/pdf|docx|html, POST /templates/*) behind the Pro
license — they 404 with "available in Pro Edition". The only free way to
create a signable submission is POST /submissions against a template_id built
once in the DocuSeal web UI. Per-candidate variable data is injected as
read-only pre-filled field values. The (org, envelope) → template_id binding
lives in the onboarding_docuseal_templates table (migration 081); callers
resolve it and pass template_id + field_values in.

Lifecycle of an envelope from our side:

    create_routed_signing_envelope(run, template_id, field_values, [hr, candidate])
        ├─ POST /submissions  {template_id, submitters:[{role, email, fields:[…]}]}
        │     (returns per-submitter submission_id + slug + embed_src)
        └─ INSERT onboarding_signing_envelopes (provider=docuseal, status=sent)

    [HR clicks signing link → DocuSeal POSTs form.completed]
    [DocuSeal then emails candidate → candidate signs → DocuSeal POSTs submission.completed]
        verify_webhook_signature(body, header) → bool
        ingest_webhook_event(body)
            ├─ UPDATE onboarding_signing_envelopes (status, *_at, signers JSONB)
            ├─ UPDATE onboarding_documents (sign_status, esign_status)
            ├─ INSERT onboarding_events
            ├─ if completed → download signed PDF → Supabase Storage
            └─ if LOI envelope completed → flip run status + Inngest kick

    void_envelopes_for_run(run_id, reason)
        ├─ for each active envelope: DELETE /submissions/{id}
        └─ UPDATE onboarding_signing_envelopes (status=voided)
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import get_settings
from app.database import get_service_client

log = logging.getLogger(__name__)


class DocuSealError(RuntimeError):
    """Deterministic DocuSeal API failure — 4xx, bad submission, missing key."""


class DocuSealUnavailable(RuntimeError):
    """DocuSeal isn't configured. Onboarding agent falls back to plain
    email-with-PDF delivery and the HR dashboard shows "Configure DocuSeal"."""


# ── Config helpers ─────────────────────────────────────────────────────────

def _config() -> dict[str, str]:
    s = get_settings()
    return {
        "base_url": (getattr(s, "docuseal_base_url", "") or "").rstrip("/"),
        "api_key": getattr(s, "docuseal_api_key", "") or "",
        "webhook_secret": getattr(s, "docuseal_webhook_secret", "") or "",
    }


def _require_configured() -> dict[str, str]:
    cfg = _config()
    missing = [k for k in ("base_url", "api_key") if not cfg.get(k)]
    if missing:
        raise DocuSealUnavailable(
            "DocuSeal not fully configured. Missing: " + ", ".join(missing)
        )
    return cfg


def is_configured() -> bool:
    """True only when DocuSeal is wired end-to-end: base_url + api_key to
    create envelopes, and webhook_secret to HMAC-verify the completion
    callbacks. Gate the onboarding e-sign path on this — entering e-signing
    without the webhook secret would start a signing flow that can never be
    driven to completion, stranding the run."""
    cfg = _config()
    return all(cfg.get(k) for k in ("base_url", "api_key", "webhook_secret"))


# ── HTTP client ────────────────────────────────────────────────────────────

# Free-tier hosts (Render, Koyeb, ...) scale to zero and can take 30-90s to
# wake on the first request. A single long timeout plus a couple of retries
# absorbs that without turning a cold start into a hard failure for the user.
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = (3.0, 8.0)
_RETRYABLE_EXC = (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError)


class DocuSealClient:
    """Thin REST wrapper around DocuSeal's HTTP API. Auth is a static
    X-Auth-Token header — no token exchange, no caching."""

    def __init__(self, *, cfg: dict[str, str] | None = None) -> None:
        self.cfg = cfg or _require_configured()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        timeout: float = 90.0,
    ) -> Any:
        url = f"{self.cfg['base_url']}{path}"
        headers = {
            "X-Auth-Token": self.cfg["api_key"],
            "Accept": "application/json",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        resp = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.request(method, url, headers=headers, json=json_body)
                break
            except _RETRYABLE_EXC as exc:
                if attempt == _MAX_ATTEMPTS:
                    raise DocuSealError(
                        f"DocuSeal {method} {path} unreachable after {attempt} "
                        f"attempts (host may be cold-starting): {exc}"
                    ) from exc
                log.warning(
                    "docuseal.request_retry method=%s path=%s attempt=%d err=%s",
                    method, path, attempt, exc,
                )
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])

        if resp.status_code >= 400:
            snippet = (resp.text or "")[:500]
            raise DocuSealError(
                f"DocuSeal {method} {path} → {resp.status_code}: {snippet}"
            )
        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return resp.content

    async def _get_bytes(self, url: str, *, timeout: float = 90.0) -> bytes:
        """GET a fully-qualified URL (e.g. signed-PDF download). Uses the
        API key in case the URL is on our DocuSeal host; harmless on
        signed download URLs that ignore the header."""
        headers = {"X-Auth-Token": self.cfg["api_key"]}

        resp = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                    resp = await client.get(url, headers=headers)
                break
            except _RETRYABLE_EXC as exc:
                if attempt == _MAX_ATTEMPTS:
                    raise DocuSealError(
                        f"DocuSeal GET {url[:80]}… unreachable after {attempt} "
                        f"attempts (host may be cold-starting): {exc}"
                    ) from exc
                log.warning(
                    "docuseal.get_bytes_retry url=%s attempt=%d err=%s",
                    url[:80], attempt, exc,
                )
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])

        if resp.status_code != 200:
            raise DocuSealError(
                f"DocuSeal GET {url[:80]}… → {resp.status_code}: {(resp.text or '')[:200]}"
            )
        return resp.content


# ── Public API ─────────────────────────────────────────────────────────────


async def create_signing_envelope(
    *,
    org_id: str,
    run_id: str,
    recipient_email: str,
    recipient_name: str,
    template_id: str,
    document_kinds: list[str],
    field_values: dict[str, Any] | None = None,
    role_name: str = "First Party",
    field_map: dict[str, str] | None = None,
    email_subject: str,
    email_body: str | None = None,
    send_email: bool = False,
    return_url: str | None = None,  # noqa: ARG001 — DocuSeal redirect lives in the template
) -> dict[str, Any]:
    """Create a single-signer DocuSeal submission from a pre-built template.

    `template_id` is a DocuSeal template built once in the web UI (free tier
    cannot ingest a PDF via API). `field_values` are per-candidate variable
    values (our render context); they're injected as read-only pre-filled
    fields on the signer. `field_map` optionally remaps a variable name to a
    differently-named field in the template.

    Returns:
        {
            "envelope_id": str (DocuSeal submission_id, stringified),
            "signing_url": str (embed_src URL — stable until completion),
            "signing_url_expires_at": None (DocuSeal slugs do not expire),
        }
    """
    if not document_kinds:
        raise DocuSealError("No document kinds supplied for envelope.")

    client = DocuSealClient()
    submitter: dict[str, Any] = {
        "role": role_name,
        "email": recipient_email,
        "name": recipient_name,
        "send_email": send_email,
    }
    fields = _prefill_fields(field_values, field_map)
    if fields:
        submitter["fields"] = fields

    body = {
        "template_id": _template_id_int(template_id),
        "send_email": send_email,  # Single embedded signer — we surface the link in UI.
        "order": "preserved",
        "message": {
            "subject": email_subject,
            "body": email_body or email_subject,
        },
        "submitters": [submitter],
    }

    log.info(
        "docuseal.create_envelope run=%s recipient=%s template=%s kinds=%s",
        run_id, recipient_email, template_id, document_kinds,
    )
    res = await client._request("POST", "/submissions", json_body=body)
    submission_id, submitters = _extract_submission(res)

    if not submitters:
        raise DocuSealError(f"DocuSeal submission returned no submitters: {res}")
    signing_url = submitters[0].get("embed_src") or submitters[0].get("url")
    if not signing_url:
        raise DocuSealError("DocuSeal submitter missing embed_src.")

    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("onboarding_signing_envelopes").insert(
            {
                "org_id": org_id,
                "run_id": run_id,
                "provider": "docuseal",
                "envelope_id": str(submission_id),
                "document_kinds": document_kinds,
                "status": "sent",
                "recipient_email": recipient_email,
                "recipient_name": recipient_name,
                "signing_url": signing_url,
                "signing_url_expires_at": None,
                "sent_at": datetime.now(UTC).isoformat(),
            }
        ).execute()
    )

    return {
        "envelope_id": str(submission_id),
        "signing_url": signing_url,
        "signing_url_expires_at": None,
    }


async def get_signing_url(
    *,
    envelope_id: str,
    recipient_email: str,
    recipient_name: str,  # noqa: ARG001 — kept for API parity with DocuSign signature
    return_url: str,  # noqa: ARG001 — DocuSeal redirect lives in submission settings
    client_user_id: str | None = None,  # noqa: ARG001 — DocuSeal slugs are stable
) -> str:
    """Return the embedded-signing URL for a known submitter.

    DocuSeal embed URLs are stable for the lifetime of the submission, so
    this hits GET /submissions/{id} and reads the per-submitter `embed_src`
    rather than minting a fresh view URL on every call. Kwarg signature is
    preserved for compatibility with the DocuSign predecessor.
    """
    client = DocuSealClient()
    res = await client._request("GET", f"/submissions/{envelope_id}")
    submitters = _submitters_from_response(res)
    target = next(
        (
            s for s in submitters
            if (s.get("email") or "").lower() == recipient_email.lower()
        ),
        None,
    )
    if not target:
        raise DocuSealError(
            f"DocuSeal submission {envelope_id} has no submitter for {recipient_email}."
        )
    url = target.get("embed_src") or target.get("url")
    if not url:
        raise DocuSealError("DocuSeal submitter missing embed_src.")
    return url


# ── Multi-signer routed envelopes (used by LOI: HR → candidate) ───────────


async def create_routed_signing_envelope(
    *,
    org_id: str,
    run_id: str,
    document_kinds: list[str],
    signers: list[dict[str, Any]],
    template_id: str,
    field_values: dict[str, Any] | None = None,
    role_names: list[str] | None = None,
    field_map: dict[str, str] | None = None,
    email_subject: str,
    email_body: str | None = None,
) -> dict[str, Any]:
    """Create a DocuSeal submission routed to multiple signers in order.

    `signers` is a list of {role, email, name, routing_order, embedded} dicts,
    where `role` is our *internal* role ('hr' | 'candidate') used by the webhook
    ingest + mint_signer_url. `template_id` is a DocuSeal template built once in
    the web UI; `role_names` are that template's *DocuSeal* role names in
    routing order (position i → the i-th sorted signer). Embedded signers get
    `send_email=false` (we surface the link in our UI); remote signers get
    `send_email=true` so DocuSeal mails them when their turn arrives.

    `field_values` (our render context) are injected as read-only pre-filled
    fields on the FIRST submitter — DocuSeal shows them to every party but only
    that submitter "owns" them, so the template must assign the data fields to
    the first role. `field_map` optionally remaps a variable to a differently-
    named field.

    `document_kinds` lists the onboarding kinds wrapped in this envelope
    (e.g. ["loi"]). Used for the signing_envelopes row + webhook fan-out.

    Returns:
        {
            "envelope_id": str (DocuSeal submission_id, stringified),
            "signing_url_expires_at": None (DocuSeal slugs do not expire),
            "signers": [
                {role, email, name, routing_order, recipient_id (submitter_id),
                 client_user_id (=submitter_slug), embedded, status, signing_url},
                ...
            ],
        }
    """
    if not signers:
        raise DocuSealError("No signers supplied for envelope.")

    client = DocuSealClient()
    sorted_signers = sorted(signers, key=lambda s: int(s.get("routing_order", 1)))
    prefill = _prefill_fields(field_values, field_map)

    submitters_payload: list[dict[str, Any]] = []
    for idx, s in enumerate(sorted_signers):
        is_embedded = bool(s.get("embedded", True))
        submitter: dict[str, Any] = {
            # DocuSeal role name (matches a role in the template), NOT our
            # internal 'hr'/'candidate' role — those stay in the signers JSONB.
            "role": _docuseal_role(role_names, idx),
            "email": s["email"],
            "name": s["name"],
            # Embedded signers: link surfaced in our UI, no email from DocuSeal.
            # Remote signers: DocuSeal emails them when their turn arrives.
            "send_email": not is_embedded,
        }
        # Pre-filled read-only data goes on the first signer only.
        if idx == 0 and prefill:
            submitter["fields"] = prefill
        submitters_payload.append(submitter)

    body = {
        "template_id": _template_id_int(template_id),
        "send_email": True,  # Per-submitter send_email overrides this.
        "order": "preserved",
        "message": {
            "subject": email_subject,
            "body": email_body or email_subject,
        },
        "submitters": submitters_payload,
    }

    log.info(
        "docuseal.create_routed_envelope run=%s template=%s kinds=%s signer_count=%d",
        run_id, template_id, document_kinds, len(signers),
    )
    res = await client._request("POST", "/submissions", json_body=body)
    submission_id, returned_submitters = _extract_submission(res)
    if not returned_submitters:
        raise DocuSealError(f"DocuSeal submission returned no submitters: {res}")

    # Merge the request-side signer metadata with the DocuSeal-assigned
    # submitter_id + slug + embed_src. Match by email (canonical key).
    by_email = {
        (s.get("email") or "").lower(): s for s in returned_submitters
    }
    enriched: list[dict[str, Any]] = []
    for idx, s in enumerate(sorted_signers, start=1):
        ret = by_email.get((s["email"] or "").lower(), {})
        role = s["role"]
        is_embedded = bool(s.get("embedded", True))
        enriched.append(
            {
                "role": role,
                "email": s["email"],
                "name": s["name"],
                "routing_order": int(s.get("routing_order", idx)),
                # recipient_id stays the canonical key the webhook ingest uses
                # to fan status back to the per-signer JSONB. DocuSeal exposes
                # `id` (numeric) and `slug` (string); slug is unique and stable.
                "recipient_id": str(ret.get("id") or idx),
                "client_user_id": ret.get("slug"),
                "embedded": is_embedded,
                "status": (ret.get("status") or "sent").lower(),
                "signing_url": ret.get("embed_src") or ret.get("url"),
            }
        )

    first = enriched[0]
    now_iso = datetime.now(UTC).isoformat()

    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("onboarding_signing_envelopes").insert(
            {
                "org_id": org_id,
                "run_id": run_id,
                "provider": "docuseal",
                "envelope_id": str(submission_id),
                "document_kinds": document_kinds,
                "status": "sent",
                "recipient_email": first["email"],
                "recipient_name": first["name"],
                "signers": enriched,
                "signing_url_expires_at": None,
                "sent_at": now_iso,
            }
        ).execute()
    )

    return {
        "envelope_id": str(submission_id),
        "signing_url_expires_at": None,
        "signers": enriched,
    }


async def mint_signer_url(
    *,
    envelope_id: str,
    run_id: str,  # noqa: ARG001 — kept for API parity with DocuSign signature
    role: str,
    email: str,
    name: str,  # noqa: ARG001 — kept for API parity with DocuSign signature
    return_url: str,  # noqa: ARG001 — DocuSeal redirect is set per-submission, not per-view
) -> str:
    """Return the embedded-signing URL for a known signer on a routed
    submission.

    Fast path: read the stored `signing_url` from onboarding_signing_envelopes
    .signers[*]. DocuSeal slugs don't rotate, so this is a pure DB read with
    no DocuSeal API hop. Falls back to GET /submissions/{id} if the row is
    missing the URL (e.g. first call after a partial insert).
    """
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("onboarding_signing_envelopes")
        .select("signers, status")
        .eq("envelope_id", envelope_id)
        .maybe_single()
        .execute()
    )
    if row and row.data:
        if row.data.get("status") in ("completed", "voided", "declined", "expired"):
            raise DocuSealError(
                f"Envelope {envelope_id} is {row.data['status']}; no signing URL available."
            )
        for sig in row.data.get("signers") or []:
            if not isinstance(sig, dict):
                continue
            if sig.get("role") == role and (sig.get("email") or "").lower() == email.lower():
                if sig.get("signing_url"):
                    return sig["signing_url"]
                break  # Found the signer but URL is missing — fall through to API.

    # Fallback — pull live from DocuSeal.
    client = DocuSealClient()
    res = await client._request("GET", f"/submissions/{envelope_id}")
    submitters = _submitters_from_response(res)
    target = next(
        (
            s for s in submitters
            if (s.get("email") or "").lower() == email.lower()
        ),
        None,
    )
    if not target:
        raise DocuSealError(
            f"DocuSeal submission {envelope_id} has no submitter for {email}."
        )
    url = target.get("embed_src") or target.get("url")
    if not url:
        raise DocuSealError("DocuSeal submitter missing embed_src.")
    return url


async def void_envelopes_for_run(
    *, org_id: str, run_id: str, reason: str,  # noqa: ARG001 — org_id kept for API parity
) -> int:
    """Void every active envelope linked to a run. Returns count voided."""
    cfg = _config()
    if not cfg.get("api_key"):
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

    client = DocuSealClient()
    voided = 0
    for env in active:
        try:
            await client._request(
                "DELETE", f"/submissions/{env['envelope_id']}",
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
                "docuseal.void_failed envelope=%s err=%s",
                env.get("envelope_id"), exc,
            )
    return voided


# ── Webhook ingestion ──────────────────────────────────────────────────────

def verify_webhook_signature(
    *, body: bytes, signature_header: str | None,
) -> bool:
    """Verify a DocuSeal webhook signature.

    Our DocuSeal instance's webhook is configured in "Secret" mode (DocuSeal
    admin → Webhooks → Security), not "HMAC" mode. In that mode DocuSeal
    echoes the shared secret directly in the `X-Docuseal-Signature` header —
    no digest, no timestamp — so the primary check is a constant-time
    compare against the configured WEBHOOK_SECRET.

    We also accept HMAC-SHA256 of the raw body (hex or base64) as a fallback,
    in case the webhook is ever switched to "HMAC" mode. Note HMAC mode's
    real header format is `<timestamp>.<sha256>`, which these two fallbacks
    do NOT parse — they only match a bare digest with no timestamp prefix.
    If you switch to HMAC mode, update this function to split on the `.` and
    verify against DocuSeal's actual signed-payload construction.
    """
    cfg = _config()
    secret = cfg.get("webhook_secret")
    if not secret or not signature_header:
        return False
    secret_bytes = secret.encode("utf-8")
    received = signature_header.strip()

    # Primary: plain shared-secret echo ("Secret" mode — what we're configured for).
    if hmac.compare_digest(secret, received):
        return True

    # Fallback: HMAC-SHA256 hex digest (bare, no timestamp prefix).
    expected_hex = hmac.new(secret_bytes, body, hashlib.sha256).hexdigest()
    if hmac.compare_digest(expected_hex, received.lower()):
        return True

    # Fallback: HMAC-SHA256 base64 digest (bare, no timestamp prefix).
    expected_b64 = base64.b64encode(
        hmac.new(secret_bytes, body, hashlib.sha256).digest()
    ).decode("ascii")
    if hmac.compare_digest(expected_b64, received):
        return True

    return False


# Map DocuSeal event_type → our internal envelope status.
# Per-submitter events (form.*) update the signers JSONB; envelope-level
# events (submission.*) update the envelope row's status column.
_ENVELOPE_STATUS_FROM_EVENT = {
    "submission.created": "sent",
    "submission.completed": "completed",
    "submission.expired": "expired",
    "submission.archived": "voided",
}
_SUBMITTER_STATUS_FROM_EVENT = {
    "form.viewed": "delivered",
    "form.started": "delivered",
    "form.completed": "completed",
    "form.declined": "declined",
}


async def ingest_webhook_event(*, body: dict[str, Any]) -> dict[str, Any]:
    """Process a DocuSeal webhook payload. Caller has already verified HMAC.

    Payload shape (DocuSeal v1):
        {
            "event_type": "form.completed" | "submission.completed" | ...,
            "timestamp": "2026-06-30T10:00:00Z",
            "data": {
                "id": <submitter_id> | <submission_id>,
                "submission_id": <submission_id>,
                "submission": { "id", "status", ... },
                "email": "...",
                "status": "completed" | "declined" | ...,
                "completed_at": "...",
                "audit_log_url": "...",
                "combined_document_url": "..." (only on submission.completed),
            },
        }
    """
    event_type = (body.get("event_type") or "").lower()
    data = body.get("data") or {}

    # Extract submission_id — DocuSeal places it differently for form.* vs submission.*
    submission_id = (
        data.get("submission_id")
        or (data.get("submission") or {}).get("id")
        or (data.get("id") if event_type.startswith("submission.") else None)
    )
    if submission_id is None:
        log.warning(
            "docuseal.webhook_missing_submission_id event=%s body=%s",
            event_type, json.dumps(body)[:500],
        )
        return {"status": "ignored", "reason": "no_submission_id"}
    submission_id_str = str(submission_id)

    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("onboarding_signing_envelopes")
        .select(
            "id, org_id, run_id, status, document_kinds, "
            "recipient_email, recipient_name, signers"
        )
        .eq("envelope_id", submission_id_str)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        log.warning("docuseal.webhook_unknown_submission id=%s", submission_id_str)
        return {"status": "ignored", "reason": "unknown_submission"}

    env = row.data
    now_iso = datetime.now(UTC).isoformat()
    update: dict[str, Any] = {}

    # ── Envelope-level transitions ─────────────────────────────────────────
    envelope_status = _ENVELOPE_STATUS_FROM_EVENT.get(event_type)
    if envelope_status:
        update["status"] = envelope_status
        if envelope_status == "completed":
            update["completed_at"] = data.get("completed_at") or now_iso
        elif envelope_status == "expired":
            update["voided_at"] = now_iso  # 'expired' is a kind of void from our POV
        elif envelope_status == "voided":
            update["voided_at"] = now_iso

    # ── Per-submitter fan-out ──────────────────────────────────────────────
    submitter_status = _SUBMITTER_STATUS_FROM_EVENT.get(event_type)
    existing_signers = env.get("signers") or []
    if submitter_status and isinstance(existing_signers, list) and existing_signers:
        evt_email = (data.get("email") or "").lower()
        merged: list[dict[str, Any]] = []
        for sig in existing_signers:
            if not isinstance(sig, dict):
                merged.append(sig)
                continue
            if (sig.get("email") or "").lower() == evt_email:
                sig = {**sig, "status": submitter_status}
                if submitter_status == "completed":
                    sig["completed_at"] = data.get("completed_at") or now_iso
                elif submitter_status == "declined":
                    sig["declined_at"] = now_iso
                    sig["decline_reason"] = (
                        data.get("decline_reason") or sig.get("decline_reason")
                    )
            merged.append(sig)
        update["signers"] = merged

        # If a single submitter declined, the whole envelope is dead.
        if submitter_status == "declined" and env.get("status") not in (
            "declined", "voided", "completed", "expired"
        ):
            update["status"] = "declined"
            update["declined_at"] = now_iso
            update["decline_reason"] = data.get("decline_reason")
        # If the only submitter completed and there's no envelope-level event
        # coming (e.g. self-signed submissions), promote to completed.
        elif submitter_status == "completed" and all(
            (s.get("status") == "completed") for s in (update.get("signers") or [])
        ):
            update.setdefault("status", "completed")
            update.setdefault("completed_at", now_iso)

    # ── Append to events audit log (capped + deduped) ──────────────────────
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
    event_hash = hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    already_seen = any(
        isinstance(e, dict) and e.get("hash") == event_hash
        for e in existing_events[-10:]
    )
    if not already_seen:
        existing_events.append(
            {"at": now_iso, "event": event_type, "hash": event_hash, "raw": body}
        )
        update["events"] = existing_events[-50:]

    if not update:
        return {"status": "ok", "envelope_status": env.get("status")}

    await asyncio.to_thread(
        lambda: svc.table("onboarding_signing_envelopes")
        .update(update)
        .eq("id", env["id"])
        .execute()
    )

    # ── Mirror status to onboarding_documents ──────────────────────────────
    kinds = env.get("document_kinds") or []
    is_loi_envelope = "loi" in kinds
    final_status = update.get("status") or env.get("status")
    if kinds and final_status in ("completed", "declined", "delivered", "voided", "expired"):
        doc_update: dict[str, Any] = {"esign_status": final_status}
        if final_status == "completed":
            doc_update["esign_completed_at"] = update.get("completed_at") or now_iso
            doc_update["sign_status"] = "signed_by_candidate"
        elif final_status == "declined":
            doc_update["esign_declined_at"] = update.get("declined_at") or now_iso
            doc_update["esign_decline_reason"] = update.get("decline_reason")
            doc_update["sign_status"] = "sent_to_candidate"
        await asyncio.to_thread(
            lambda: svc.table("onboarding_documents")
            .update(doc_update)
            .eq("run_id", env["run_id"])
            .in_("kind", kinds)
            .execute()
        )

    # ── On envelope completion: download signed PDF + kick agent ───────────
    if final_status == "completed" and not already_seen:
        try:
            await _download_signed_documents(
                org_id=env["org_id"], run_id=env["run_id"],
                envelope_id=submission_id_str, kinds=kinds,
                combined_url=data.get("combined_document_url"),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "docuseal.signed_pdf_download_failed envelope=%s err=%s",
                submission_id_str, exc,
            )

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
                    "docuseal.loi_status_flip_failed envelope=%s err=%s",
                    submission_id_str, exc,
                )

        try:
            import inngest

            from app.inngest.client import get_inngest_client
            inngest_client = get_inngest_client()
            event_name = (
                "onboarding_v2/loi_signed_uploaded" if is_loi_envelope
                else "onboarding_v2/esign_completed"
            )
            await inngest_client.send(
                inngest.Event(
                    name=event_name,
                    data={"onboarding_run_id": env["run_id"], "org_id": env["org_id"]},
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("docuseal.inngest_kick_failed err=%s", exc)

    # ── Forensic timeline event ────────────────────────────────────────────
    if not already_seen:
        from app.services.agents.onboarding_v2 import storage as ob_storage
        actor_kind = (
            "candidate" if event_type in ("form.completed", "form.declined")
            else "system"
        )
        await ob_storage.log_onboarding_event(
            org_id=env["org_id"], run_id=env["run_id"],
            actor_kind=actor_kind,
            event_type=f"esign_{event_type.replace('.', '_')}",
            message=_human_event_message(event_type, final_status, is_loi_envelope),
            metadata={
                "envelope_id": submission_id_str,
                "event_type": event_type,
                "submitter_email": data.get("email"),
            },
        )

    return {"status": "ok", "envelope_status": final_status}


def _human_event_message(
    event_type: str, envelope_status: str | None, is_loi: bool,
) -> str:
    """Compact human-readable summary for the onboarding_events timeline."""
    if event_type == "submission.completed" or envelope_status == "completed":
        return (
            "LOI signed by HR and candidate." if is_loi
            else "Document bundle signed by candidate."
        )
    if event_type == "form.declined":
        return "Signer declined the document."
    if event_type == "form.completed":
        return "Signer completed their step."
    if event_type == "form.viewed":
        return "Signer opened the signing page."
    if event_type == "submission.expired":
        return "Submission expired before all signers completed."
    return f"DocuSeal event: {event_type}"


# ── Helpers ────────────────────────────────────────────────────────────────


def _template_id_int(template_id: str | int) -> int:
    """DocuSeal's POST /submissions expects template_id as an integer. We store
    it as text to avoid JSON-number precision surprises, so coerce here and
    surface a clean error if HR pasted something non-numeric."""
    try:
        return int(str(template_id).strip())
    except (TypeError, ValueError) as exc:
        raise DocuSealError(
            f"DocuSeal template_id must be numeric, got {template_id!r}. "
            "Copy the numeric id from the template's URL in the DocuSeal admin."
        ) from exc


def _docuseal_role(role_names: list[str] | None, idx: int) -> str:
    """Map a routing position to the DocuSeal role name defined in the template.
    Falls back to DocuSeal's default names when the mapping is short/unset."""
    if role_names and idx < len(role_names) and (role_names[idx] or "").strip():
        return role_names[idx].strip()
    return "First Party" if idx == 0 else "Second Party"


def _stringify_value(val: Any) -> str:
    """Render a pre-fill value as the string DocuSeal expects for a text field."""
    if isinstance(val, bool):
        return "Yes" if val else "No"
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    return str(val)


def _prefill_fields(
    field_values: dict[str, Any] | None,
    field_map: dict[str, str] | None,
) -> list[dict[str, Any]]:
    """Turn a render context into DocuSeal read-only pre-filled fields.

    Each `{name, default_value, readonly}` targets a field named after our
    template_vars.py variable (or its `field_map` override). Complex values
    (dicts/lists like ctc_breakdown) and None are skipped — only the flat
    scalar blanks map onto DocuSeal fields."""
    if not field_values:
        return []
    fmap = field_map or {}
    out: list[dict[str, Any]] = []
    for var, val in field_values.items():
        if val is None or isinstance(val, (dict, list)):
            continue
        out.append(
            {
                "name": fmap.get(var, var),
                "default_value": _stringify_value(val),
                "readonly": True,
            }
        )
    return out


def _extract_submission(res: Any) -> tuple[Any, list[dict[str, Any]]]:
    """DocuSeal returns either a single submission dict OR a list of submitters
    on creation depending on endpoint version. Normalize both shapes."""
    if isinstance(res, list):
        if not res:
            raise DocuSealError("DocuSeal returned empty submitter list.")
        submission_id = res[0].get("submission_id") or res[0].get("submission", {}).get("id")
        return submission_id, [s for s in res if isinstance(s, dict)]
    if isinstance(res, dict):
        submission_id = res.get("id") or res.get("submission_id")
        submitters = res.get("submitters") or []
        return submission_id, [s for s in submitters if isinstance(s, dict)]
    raise DocuSealError(f"Unexpected DocuSeal response shape: {type(res).__name__}")


def _submitters_from_response(res: Any) -> list[dict[str, Any]]:
    if isinstance(res, dict):
        return [s for s in (res.get("submitters") or []) if isinstance(s, dict)]
    if isinstance(res, list):
        return [s for s in res if isinstance(s, dict)]
    return []


async def _download_signed_documents(
    *,
    org_id: str,
    run_id: str,
    envelope_id: str,
    kinds: list[str],
    combined_url: str | None = None,
) -> None:
    """Pull the signed combined PDF from DocuSeal and upload to Supabase
    Storage. All documents in this envelope get the same signed_pdf_path."""
    if not kinds:
        return
    client = DocuSealClient()

    pdf_bytes: bytes | None = None
    if combined_url:
        try:
            pdf_bytes = await client._get_bytes(combined_url)
        except DocuSealError as exc:
            log.warning(
                "docuseal.combined_url_fetch_failed envelope=%s err=%s",
                envelope_id, exc,
            )

    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
        # Fallback: ask DocuSeal directly for the merged signed doc.
        res = await client._request(
            "GET", f"/submissions/{envelope_id}/documents?combined=true",
        )
        if isinstance(res, dict):
            docs = res.get("documents") or []
        elif isinstance(res, list):
            docs = res
        else:
            docs = []
        url = next(
            (d.get("url") for d in docs if isinstance(d, dict) and d.get("url")),
            None,
        )
        if not url:
            raise DocuSealError(
                f"DocuSeal envelope {envelope_id} has no signed PDF URL."
            )
        pdf_bytes = await client._get_bytes(url)
        if not pdf_bytes.startswith(b"%PDF"):
            raise DocuSealError("DocuSeal signed-PDF fetch did not return a PDF.")

    from app.services.agents.onboarding_v2.storage import STORAGE_BUCKET
    svc = get_service_client()
    blob_slug = "_".join(sorted(kinds)) or "envelope"
    path = (
        f"orgs/{org_id}/onboarding/{run_id}/{blob_slug}_docuseal_signed.pdf"
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
