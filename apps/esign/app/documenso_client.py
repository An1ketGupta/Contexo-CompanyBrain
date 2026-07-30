"""Typed async client for Documenso's v2 Envelope API — the ONLY module that
knows Documenso's HTTP shape. Everything Documenso-specific is isolated here
so a v2 API change is a one-file edit.

⚠️  Documenso's v2 API is young and reorganised around an "Envelope" model.
The endpoints/fields below are what the docs describe (see services/documenso
/README.md), but each assumption is flagged inline. After first boot, verify
against `{base_url}/api/v2/openapi.json` and adjust here if the running
version differs. The adapter fails safe — any error here raises and apps/api
falls back to the print/scan flow.

Signing flow this client drives:
    create (upload PDF + recipients)  → envelopeId (and nothing else)
    GET envelope/{id}                 → recipient ids + envelope item id
    field/create-many (SIGNATURE @ %) → fields bound to recipients
    distribute (meta.distributionMethod=NONE) → signable, no Documenso email;
                                        response carries each signing token
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings
from app.field_placement import SignatureField

log = logging.getLogger(__name__)

# Both HR and candidate actually apply a signature, so both are SIGNER. (CC /
# APPROVER / VIEWER exist but we don't use them.)
_DOCUMENSO_ROLE = "SIGNER"

_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = (3.0, 8.0)  # free-tier cold start can take 30-90s
_RETRYABLE_EXC = (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError)


class DocumensoError(RuntimeError):
    """Deterministic Documenso failure — 4xx, unparseable response, bad config."""


class DocumensoUnavailable(RuntimeError):
    """Documenso isn't configured. Callers fall back to the print/scan flow."""


@dataclass(frozen=True)
class RecipientSpec:
    role: str  # our internal role: "hr" | "candidate"
    email: str
    name: str
    signing_order: int


@dataclass(frozen=True)
class RecipientResult:
    role: str
    email: str
    name: str
    documenso_recipient_id: int
    documenso_token: str


@dataclass(frozen=True)
class EnvelopeResult:
    envelope_id: str
    recipients: list[RecipientResult]


def _cfg() -> dict[str, str]:
    s = get_settings()
    return {
        "base_url": (s.documenso_base_url or "").rstrip("/"),
        "public_url": (s.documenso_public_url or s.documenso_base_url or "").rstrip("/"),
        "token": s.documenso_api_token or "",
        "team_id": s.documenso_team_id or "",
    }


def is_configured() -> bool:
    c = _cfg()
    return bool(c["base_url"] and c["token"])


def public_url() -> str:
    return _cfg()["public_url"]


def _require_configured() -> dict[str, str]:
    if not is_configured():
        raise DocumensoUnavailable(
            "Documenso not configured. Set DOCUMENSO_BASE_URL and DOCUMENSO_API_TOKEN."
        )
    return _cfg()


def _headers(cfg: dict[str, str]) -> dict[str, str]:
    # Documenso authenticates with the raw token in Authorization (no "Bearer").
    headers = {"Authorization": cfg["token"], "Accept": "application/json"}
    # Some deployments key the API on a team header when the token isn't
    # team-scoped. Harmless when unset/ignored by the server.
    if cfg["team_id"]:
        headers["X-Team-Id"] = cfg["team_id"]
    return headers


async def _request(
    method: str,
    path: str,
    *,
    cfg: dict[str, str],
    json_body: Any | None = None,
    data: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
) -> Any:
    url = f"{cfg['base_url']}{path}"
    resp: httpx.Response | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.request(
                    method, url, headers=_headers(cfg),
                    json=json_body, data=data, files=files,
                )
            break
        except _RETRYABLE_EXC as exc:
            if attempt == _MAX_ATTEMPTS:
                raise DocumensoError(
                    f"Documenso {method} {path} unreachable after {attempt} "
                    f"attempts (free-tier cold start?): {exc}"
                ) from exc
            log.warning("documenso.request_retry method=%s path=%s attempt=%d", method, path, attempt)
            await asyncio.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])

    assert resp is not None
    if resp.status_code >= 400:
        raise DocumensoError(
            f"Documenso {method} {path} -> {resp.status_code}: {(resp.text or '')[:500]}"
        )
    try:
        return resp.json() if resp.content else {}
    except ValueError as exc:
        raise DocumensoError(f"Documenso {method} {path}: non-JSON response") from exc


def _recipient_id(r: dict) -> int | None:
    rid = r.get("id") or r.get("recipientId")
    if rid is None:
        return None
    try:
        return int(rid)
    except (TypeError, ValueError):
        return None


def _match_recipient_id(
    recipients: list[dict], *, email: str, signing_order: int, claimed: set[int]
) -> int:
    """Resolve one Documenso recipient id for one of our signers.

    Email alone is NOT a key. HR and the candidate are legitimately the same
    address sometimes (a founder onboarding themselves, anyone testing the
    flow), and Documenso happily creates two recipients for it. Matching on
    email then handed both roles the same id, so every signature field piled
    onto the first recipient and the second was left with none — `distribute`
    rejects that with MISSING_SIGNATURE_FIELD. So: disambiguate by signing
    order, and never hand the same recipient to two roles.
    """
    want = email.strip().lower()
    same_email = [r for r in recipients if str(r.get("email", "")).strip().lower() == want]
    by_order = [r for r in same_email if r.get("signingOrder") == signing_order]
    for pool in (by_order, same_email):
        for r in pool:
            rid = _recipient_id(r)
            if rid is not None and rid not in claimed:
                return rid
    raise DocumensoError(
        f"Documenso envelope has no unclaimed recipient for {email} "
        f"(signing order {signing_order})"
    )


def _parse_create(payload: dict) -> str:
    """The create response is just `{"id": "envelope_..."}` — recipients and
    envelope items have to be read back with GET /envelope/{id}."""
    envelope_id = (
        payload.get("id")
        or payload.get("envelopeId")
        or (payload.get("envelope") or {}).get("id")
    )
    if not envelope_id:
        raise DocumensoError("Documenso create response missing envelope id")
    return str(envelope_id)


async def _get_envelope(envelope_id: str, *, cfg: dict[str, str]) -> dict:
    detail = await _request("GET", f"/api/v2/envelope/{envelope_id}", cfg=cfg)
    if not isinstance(detail, dict):
        raise DocumensoError(f"Documenso envelope {envelope_id}: unexpected response shape")
    return detail


def _first_item_id(envelope: dict) -> str:
    """The uploaded PDF becomes an "envelope item"; its id binds fields to a
    specific document within the envelope, and is what /download takes."""
    items = envelope.get("envelopeItems") or envelope.get("items") or []
    if not items:
        return ""
    return str(items[0].get("id") or items[0].get("envelopeItemId") or "")


async def create_and_distribute(
    *,
    title: str,
    pdf_bytes: bytes,
    pdf_filename: str,
    recipients: list[RecipientSpec],
    fields: list[SignatureField],
) -> EnvelopeResult:
    """Create the envelope, place signature fields, distribute (no Documenso
    email), and return each recipient with their embed signing token."""
    cfg = _require_configured()

    # 1) Create envelope with the PDF + recipients (multipart/form-data).
    create_payload = {
        "type": "DOCUMENT",
        "title": title,
        "recipients": [
            {
                "email": r.email,
                "name": r.name,
                "role": _DOCUMENSO_ROLE,
                "signingOrder": r.signing_order,
            }
            for r in recipients
        ],
    }
    created = await _request(
        "POST", "/api/v2/envelope/create",
        cfg=cfg,
        data={"payload": json.dumps(create_payload)},
        files={"files": (pdf_filename, pdf_bytes, "application/pdf")},
    )
    envelope_id = _parse_create(created)

    # Create returns only the envelope id, so read the envelope back for the
    # recipient ids and the envelope item id the fields must be bound to.
    envelope = await _get_envelope(envelope_id, cfg=cfg)
    envelope_item_id = _first_item_id(envelope)
    created_recipients = envelope.get("recipients") or []

    # Map our roles to Documenso recipient ids, one recipient per role.
    role_to_recipient_id: dict[str, int] = {}
    claimed: set[int] = set()
    for r in recipients:
        rid = _match_recipient_id(
            created_recipients,
            email=r.email,
            signing_order=r.signing_order,
            claimed=claimed,
        )
        role_to_recipient_id[r.role] = rid
        claimed.add(rid)

    # 2) Place SIGNATURE fields (percentage coords) bound to each recipient.
    field_data = []
    for f in fields:
        recipient_id = role_to_recipient_id.get(f.role)
        if recipient_id is None:
            continue  # a field for a role that isn't a recipient of this envelope
        entry: dict[str, Any] = {
            "type": "SIGNATURE",
            "recipientId": recipient_id,
            "page": f.page,
            "positionX": f.position_x,
            "positionY": f.position_y,
            "width": f.width,
            "height": f.height,
        }
        if envelope_item_id:
            entry["envelopeItemId"] = envelope_item_id
        field_data.append(entry)

    # Distribute 400s if any SIGNER owns no field, and its error names the
    # Documenso recipient rather than our role — catch it here where we can say
    # which role lost its signature box.
    fielded = {e["recipientId"] for e in field_data}
    unfielded = [role for role, rid in role_to_recipient_id.items() if rid not in fielded]
    if unfielded:
        raise DocumensoError(
            f"No signature field was placed for: {', '.join(sorted(unfielded))}. "
            "The document has no signature marker for that signer."
        )

    if field_data:
        await _request(
            "POST", "/api/v2/envelope/field/create-many",
            cfg=cfg,
            json_body={"envelopeId": envelope_id, "data": field_data},
        )

    # 3) Distribute → moves the envelope to a signable state. distributionMethod
    #    NONE (nested under `meta`) suppresses Documenso's own signer emails:
    #    Contexo sends the "your turn to sign" email itself with the embedded
    #    /sign/{token} link. Every SIGNER must already own at least one field
    #    or this 400s — field_placement guarantees a fallback box per role.
    distributed = await _request(
        "POST", "/api/v2/envelope/distribute",
        cfg=cfg,
        json_body={"envelopeId": envelope_id, "meta": {"distributionMethod": "NONE"}},
    )

    # 4) Signing tokens for the embed. Distribute echoes them back; the
    #    envelope read-back is the fallback if that shape ever changes.
    tokens_by_id: dict[int, str] = {}
    distributed_recipients = distributed.get("recipients") if isinstance(distributed, dict) else None
    for source in (distributed_recipients or [], created_recipients):
        for rec in source:
            rid_raw, token = rec.get("id"), rec.get("token") or rec.get("signingToken")
            if rid_raw is not None and token:
                tokens_by_id.setdefault(int(rid_raw), str(token))

    results: list[RecipientResult] = []
    for r in recipients:
        rid = role_to_recipient_id[r.role]
        token = tokens_by_id.get(rid)
        if not token:
            detail = await _request("GET", f"/api/v2/envelope/recipient/{rid}", cfg=cfg)
            token = detail.get("token") or detail.get("signingToken")
        if not token:
            raise DocumensoError(f"Documenso recipient {rid} has no signing token")
        results.append(
            RecipientResult(
                role=r.role, email=r.email, name=r.name,
                documenso_recipient_id=rid, documenso_token=str(token),
            )
        )

    return EnvelopeResult(envelope_id=envelope_id, recipients=results)


async def cancel_envelope(envelope_id: str) -> None:
    """Void an envelope in Documenso (best-effort; caller logs failures).

    `cancel` voids and keeps the envelope + its audit trail, unlike `delete`
    which removes it outright. Only PENDING envelopes can be cancelled — a
    never-distributed draft has to be deleted instead.
    """
    cfg = _require_configured()
    envelope = await _get_envelope(envelope_id, cfg=cfg)
    if str(envelope.get("status", "")).upper() == "DRAFT":
        await _request(
            "POST", "/api/v2/envelope/delete", cfg=cfg, json_body={"envelopeId": envelope_id}
        )
        return
    await _request(
        "POST", "/api/v2/envelope/cancel",
        cfg=cfg,
        json_body={"envelopeId": envelope_id, "reason": "Voided in Contexo"},
    )


async def download_signed_pdf(envelope_id: str) -> bytes:
    """Fetch the final PAdES-sealed PDF for a completed envelope.

    v2 downloads per *envelope item*, not per envelope, so read the envelope
    back for its item id first. `version=signed` is the completed document
    with signatures and audit trail. Returns raw bytes; the webhook receiver
    stores them at onboarding_documents' signed_pdf_path so the rest of the
    pipeline is unchanged.
    """
    cfg = _require_configured()
    envelope = await _get_envelope(envelope_id, cfg=cfg)
    item_id = _first_item_id(envelope)
    if not item_id:
        raise DocumensoError(f"Documenso envelope {envelope_id} has no envelope item to download")

    url = f"{cfg['base_url']}/api/v2/envelope/item/{item_id}/download"
    async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as client:
        resp = await client.get(url, headers=_headers(cfg), params={"version": "signed"})
    if resp.status_code >= 400:
        raise DocumensoError(
            f"Documenso download {envelope_id} -> {resp.status_code}: {(resp.text or '')[:300]}"
        )
    return resp.content
