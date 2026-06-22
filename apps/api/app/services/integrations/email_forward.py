"""Email forward-to-brain (Day 14 / #83 + Agent Day 12).

Each org gets a unique inbound address (brain-<slug>-<rand>@inbound.<domain>).
We expect Resend Inbound (or a compatible MX router like Mailgun) to POST
parsed email envelopes here. The handler verifies the inbound signature,
finds the matching org, and fans the envelope out to the classifier
Inngest function which decides between three downstream paths:

  support / sales  → SupportResponseAgent drafts an AI reply
  knowledge        → existing chunk+embed pipeline as a doc
  internal         → drop (calendar bots, fwd: chains, etc.)

Format-agnostic on purpose: as long as the body delivered has at minimum
`to`, `from`, `subject`, `text` (or `html`), we can classify and route it.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import secrets
from typing import Any

from app.config import get_settings
from app.database import get_service_client

log = logging.getLogger(__name__)

SOURCE_TAG = "email_forward"

# Strip the most obvious HTML so we don't ship raw <div> noise to the embedder.
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# Email body cap — long forwarded threads get truncated before we ingest. The
# regular chunker handles overflow but billing matters for tokens.
_MAX_BODY_CHARS = 60_000


# ── Inbound address provisioning ────────────────────────────────────────────

async def ensure_inbound_address(*, org_id: str) -> str:
    """Lazily allocate an inbound address for the org if it doesn't have one.

    Format: brain-<slug>-<6hex>@<domain>. The 6-hex suffix is a random tag
    that makes the address un-guessable so a third party can't blast docs
    into another org's brain just by guessing the slug.
    """
    settings = get_settings()
    if not settings.inbound_email_domain:
        raise RuntimeError("INBOUND_EMAIL_DOMAIN is not configured.")

    svc = get_service_client()
    org = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select("id, slug, inbound_email").eq("id", org_id).maybe_single().execute()
    )
    if not org or not org.data:
        raise RuntimeError("Organization not found.")
    if org.data.get("inbound_email"):
        return org.data["inbound_email"]

    slug = (org.data.get("slug") or "brain")[:32]
    suffix = secrets.token_hex(3)  # 6 hex chars → 16M combinations, plenty
    address = f"brain-{slug}-{suffix}@{settings.inbound_email_domain}".lower()
    await asyncio.to_thread(
        lambda: svc.table("organizations").update({"inbound_email": address})
        .eq("id", org_id).execute()
    )
    return address


async def get_inbound_address(*, org_id: str) -> str | None:
    svc = get_service_client()
    result = await asyncio.to_thread(
        lambda: svc.table("organizations").select("inbound_email").eq("id", org_id)
        .maybe_single().execute()
    )
    return (result.data or {}).get("inbound_email") if result else None


# ── Webhook handler ─────────────────────────────────────────────────────────

def verify_signature(*, raw_body: bytes, signature: str | None) -> bool:
    """HMAC-SHA256 compare. Returns True on a successful match.

    Resend Inbound sends `svix-signature`; Mailgun sends `X-Mailgun-Signature`
    in a different format. We expect callers to normalise to a hex digest and
    pass it here. Signature absent + empty configured secret = open route
    (dev mode); we log a warning so it's not silent.
    """
    settings = get_settings()
    if not settings.inbound_email_webhook_secret:
        log.warning("inbound_email_signature_check_disabled (no secret configured)")
        return True
    if not signature:
        return False
    expected = hmac.new(
        settings.inbound_email_webhook_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    try:
        return hmac.compare_digest(signature.lower(), expected.lower())
    except (TypeError, ValueError):
        return False


def parse_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize across inbound providers into a single envelope shape.

    Returns {to, from_, from_raw, subject, body}. The bare `from_` is the
    lowercased SMTP address; `from_raw` preserves the "Name <addr@x>" form
    so downstream code can extract a display name for the support queue UI.
    Either `text` or `html` is acceptable for the body; HTML gets a naive
    tag strip.
    """
    to = _first_address(payload.get("to") or payload.get("recipient") or "")
    from_raw_value = payload.get("from") or payload.get("sender") or ""
    from_raw = from_raw_value[0] if isinstance(from_raw_value, list) and from_raw_value else from_raw_value
    if not isinstance(from_raw, str):
        from_raw = ""
    from_ = _first_address(from_raw_value)
    subject = (payload.get("subject") or "").strip()[:200]

    body_text = (payload.get("text") or "").strip()
    if not body_text:
        body_html = payload.get("html") or payload.get("body-html") or ""
        body_text = _strip_html(body_html)

    body_text = _WS_RE.sub(" ", body_text).strip()[:_MAX_BODY_CHARS]
    return {
        "to": to,
        "from_": from_,
        "from_raw": from_raw,
        "subject": subject,
        "body": body_text,
    }


async def ingest_email(envelope: dict[str, Any]) -> dict[str, Any]:
    """Find the org by inbound address and queue classification.

    The webhook handler stays fast: signature verify + org lookup + fan-out
    to the classifier Inngest function. The classifier decides whether to
    create a doc (knowledge) or open a support ticket (support/sales) or
    drop the message (internal). See `support_functions.py` for routing.
    """
    to_address = envelope.get("to") or ""
    if not to_address:
        return {"status": "ignored", "reason": "no_to_address"}

    org = await _find_org_by_inbound(to_address)
    if not org:
        return {"status": "ignored", "reason": "no_matching_org"}

    body = envelope.get("body") or ""
    if not body.strip():
        return {"status": "ignored", "reason": "empty_body"}

    subject = envelope.get("subject") or "Forwarded email"
    from_ = envelope.get("from_") or "unknown sender"
    # Idempotency key matches the dedupe sig used in the classifier and
    # SupportResponseAgent — same forwarded email won't reroute twice.
    sig = hashlib.sha256(f"{subject}\n{body[:256]}".encode()).hexdigest()[:24]

    import inngest

    from app.inngest.client import get_inngest_client
    client = get_inngest_client()
    await client.send(
        inngest.Event(
            name="support/classify-inbound",
            data={
                "org_id": org["id"],
                "from_email": from_,
                "from_raw": envelope.get("from_raw"),
                "subject": subject,
                "body": body,
            },
            id=f"classify-{org['id']}-{sig}",
        )
    )
    return {"status": "queued", "org_id": org["id"]}


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _find_org_by_inbound(to_address: str) -> dict[str, Any] | None:
    cleaned = to_address.strip().lower()
    svc = get_service_client()
    result = await asyncio.to_thread(
        lambda: svc.table("organizations").select("id, name").eq("inbound_email", cleaned)
        .maybe_single().execute()
    )
    return result.data if result else None


def _first_address(value: Any) -> str:
    """Extract a bare email from "Name <addr@x.y>" or just "addr@x.y", or
    the first element of a list of either."""
    if isinstance(value, list):
        if not value:
            return ""
        value = value[0]
    if not isinstance(value, str):
        return ""
    s = value.strip()
    m = re.search(r"<([^>]+)>", s)
    if m:
        return m.group(1).strip().lower()
    return s.lower()


def _strip_html(html: str) -> str:
    if not html:
        return ""
    return _HTML_TAG_RE.sub(" ", html)
