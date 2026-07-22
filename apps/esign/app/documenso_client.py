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
    create (upload PDF + recipients)  → envelopeId + recipient ids + item id
    field/create-many (SIGNATURE @ %) → fields bound to recipients
    distribute (distributionMethod=NONE) → makes it signable, no Documenso email
    recipient/{id} → per-recipient signing token for the embed
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


def _match_recipient_id(recipients: list[dict], *, email: str) -> int:
    """Find the created recipient's id by email (case-insensitive). Documenso
    returns the recipients array on create; ids are needed to bind fields."""
    want = email.strip().lower()
    for r in recipients:
        if str(r.get("email", "")).strip().lower() == want:
            rid = r.get("id") or r.get("recipientId")
            if rid is not None:
                return int(rid)
    raise DocumensoError(f"Documenso create response missing recipient id for {email}")


def _parse_create(payload: dict) -> tuple[str, str, list[dict]]:
    """Pull (envelopeId, envelopeItemId, recipients[]) out of the create
    response, tolerating a couple of shapes the v2 API has used."""
    envelope_id = (
        payload.get("id")
        or payload.get("envelopeId")
        or (payload.get("envelope") or {}).get("id")
    )
    if not envelope_id:
        raise DocumensoError("Documenso create response missing envelope id")

    # The uploaded PDF becomes an "envelope item"; its id binds fields to a
    # specific document within the envelope.
    items = (
        payload.get("envelopeItems")
        or payload.get("items")
        or payload.get("documents")
        or []
    )
    envelope_item_id = ""
    if items:
        envelope_item_id = str(items[0].get("id") or items[0].get("envelopeItemId") or "")

    recipients = payload.get("recipients") or []
    return str(envelope_id), envelope_item_id, recipients


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
    envelope_id, envelope_item_id, created_recipients = _parse_create(created)

    # Map our roles to Documenso recipient ids via email.
    role_to_recipient_id: dict[str, int] = {}
    for r in recipients:
        role_to_recipient_id[r.role] = _match_recipient_id(created_recipients, email=r.email)

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

    if field_data:
        await _request(
            "POST", "/api/v2/envelope/field/create-many",
            cfg=cfg,
            json_body={"envelopeId": envelope_id, "data": field_data},
        )

    # 3) Distribute → moves the envelope to a signable state. distributionMethod
    #    NONE suppresses Documenso's own signer emails: NirnayaIQ sends the
    #    "your turn to sign" email itself with the embedded /sign/{token} link.
    await _request(
        "POST", "/api/v2/envelope/distribute",
        cfg=cfg,
        json_body={"envelopeId": envelope_id, "distributionMethod": "NONE"},
    )

    # 4) Fetch each recipient's signing token for the embed.
    results: list[RecipientResult] = []
    for r in recipients:
        rid = role_to_recipient_id[r.role]
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
    """Void an envelope in Documenso (best-effort; caller logs failures)."""
    cfg = _require_configured()
    await _request(
        "POST", "/api/v2/envelope/delete",
        cfg=cfg,
        json_body={"envelopeId": envelope_id},
    )


async def download_signed_pdf(envelope_id: str) -> bytes:
    """Fetch the final PAdES-sealed PDF for a completed envelope.

    ⚠️ Verify the exact download route against the running instance — v2 has
    used a redirect-to-storage pattern. We follow redirects and return raw
    bytes; the webhook receiver stores them at onboarding_documents'
    signed_pdf_path so the rest of the pipeline is unchanged.
    """
    cfg = _require_configured()
    url = f"{cfg['base_url']}/api/v2/envelope/{envelope_id}/download"
    async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as client:
        resp = await client.get(url, headers=_headers(cfg))
    if resp.status_code >= 400:
        raise DocumensoError(
            f"Documenso download {envelope_id} -> {resp.status_code}: {(resp.text or '')[:300]}"
        )
    return resp.content
