"""Gmail integration router (V5 Day 3).

Endpoints:
    GET  /integrations/gmail/connect      — returns Google consent URL
    GET  /integrations/gmail/callback     — code exchange + install
    GET  /integrations/gmail/status       — connected/scope-check (frontend
                                            uses this to decide whether to
                                            show the re-auth banner)
    POST /integrations/gmail/send         — enqueue an outbound email
    DEL  /integrations/gmail              — disconnect

The send endpoint optimistically marks the message's delivery_status as
`queued` and fans out to Inngest. The worker (`gmail_functions.py`) does the
actual API call with retries; failure paths flip status to `failed` with the
error message stored alongside.

Per-user OAuth — every user authorizes their own Gmail. The admin-only check
that gates Drive doesn't apply here: any user can connect their own mailbox.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import inngest
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator

from app.auth import verify_jwt
from app.config import get_settings
from app.core.rate_limiter import oauth_callback_limiter
from app.database import get_service_client
from app.errors import NoOrganization
from app.inngest.client import get_inngest_client
from app.services.integrations import gmail

log = logging.getLogger(__name__)

router = APIRouter(tags=["gmail"])

_STATE_TTL_SECONDS = 600
_PROVIDER = "gmail"

# Pragma: we want to catch typos like missing "@", not validate against the
# full RFC 5322 grammar. Pydantic's EmailStr pulls in the email-validator
# dependency which isn't in our base image — this regex is the smallest
# correct-enough guard for the API surface.
import re as _re

_EMAIL_REGEX = _re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

# Catches the `[Your Name]` / `[Name]` / `[Sender]` placeholders the LLM
# leaves behind when it doesn't know who is signing. We substitute these at
# send-time with the authenticated user's display_name so the recipient
# never sees a raw template hole.
_NAME_PLACEHOLDER_RE = _re.compile(
    r"\[\s*(?:your\s+(?:full\s+)?name|your\s+signature|sender(?:\s+name)?|my\s+name|name|signature)\s*\]",
    _re.IGNORECASE,
)


def _substitute_signature(text: str, signature: str) -> str:
    if not signature or not text:
        return text
    return _NAME_PLACEHOLDER_RE.sub(signature, text)


# Hard ceiling on the attachment we generate from retrieved chunks. Gmail's
# total-message cap is 25 MB; we stay well under that since base64 inflates
# by ~33% and the body + HTML also count toward the limit.
_MAX_SOURCES_ATTACHMENT_BYTES = 2_000_000  # 2 MB of raw text → ~2.7 MB on the wire


async def _build_sources_attachment(
    *,
    svc: Any,
    org_id: str,
    sources: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Render the chunks cited in the assistant turn into a plain-text
    attachment so the recipient receives the full source material that
    grounded the email.

    `sources` is the JSONB array stored on `messages.sources` — already
    deduped and ordered by relevance. We re-fetch each chunk's full content
    from `chunks` because the excerpt on `sources` is truncated to 280 chars.

    Returns None when there's nothing usable (no sources, all empty chunks).
    """
    chunk_ids = [s.get("chunk_id") for s in sources if s.get("chunk_id")]
    if not chunk_ids:
        return None

    # Single batched read. RLS not strictly necessary here (service-role) but
    # we still scope to org_id as defense-in-depth — a stale chunk_id from a
    # different org would otherwise leak through if `messages.sources` ever
    # got corrupted.
    try:
        result = await asyncio.to_thread(
            lambda: svc.table("chunks")
            .select("id, content, page_number, section_heading")
            .eq("org_id", org_id)
            .in_("id", chunk_ids)
            .execute()
        )
    except Exception as exc:
        log.warning("sources attachment chunk fetch failed: %s", exc)
        return None

    content_by_id: dict[str, dict[str, Any]] = {
        row["id"]: row for row in (result.data or [])
    }

    lines: list[str] = []
    lines.append("SOURCE MATERIAL")
    lines.append(
        "Retrieved from the company knowledge base by Contexo and used to "
        "ground the email you just received."
    )
    lines.append(f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")
    lines.append("=" * 72)
    lines.append("")

    n = 0
    for src in sources:
        chunk = content_by_id.get(src.get("chunk_id") or "")
        text = (chunk or {}).get("content") or src.get("excerpt") or ""
        text = text.strip()
        if not text:
            continue
        n += 1
        doc_name = src.get("document_name") or "(unnamed document)"
        page = (chunk or {}).get("page_number") or src.get("page_number")
        section = (chunk or {}).get("section_heading") or src.get("section_heading")

        header_bits = [f"{n}. {doc_name}"]
        if page is not None:
            header_bits.append(f"page {page}")
        if section:
            header_bits.append(f"section: {section}")
        lines.append(" — ".join(header_bits))
        lines.append("-" * 72)
        lines.append(text)
        lines.append("")
        lines.append("")

    if n == 0:
        return None

    body_text = "\n".join(lines).rstrip() + "\n"
    encoded = body_text.encode("utf-8")
    if len(encoded) > _MAX_SOURCES_ATTACHMENT_BYTES:
        # Defensive truncation — chat caps context at 20 chunks of ~1KB, so
        # we'd only hit this if someone bumps the chunk size dramatically.
        encoded = encoded[:_MAX_SOURCES_ATTACHMENT_BYTES] + b"\n\n[truncated]\n"

    return {
        "filename": "Sources_used.txt",
        "mime_type": "text/plain",
        "content": encoded,
    }


def _validate_email(value: str) -> str:
    value = value.strip()
    if not _EMAIL_REGEX.match(value):
        raise ValueError("must be a valid email address")
    return value


# ── Auth + state helpers (mirroring slack_router) ───────────────────────────

def _require_user(current_user: dict) -> tuple[str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    if not org_id or not user_id:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return org_id, user_id


def _mint_state(*, user_id: str, org_id: str) -> str:
    settings = get_settings()
    secret = settings.oauth_state_secret
    if not secret:
        raise HTTPException(status_code=500, detail="OAuth state secret not configured.")
    payload = f"{_PROVIDER}.{user_id}.{org_id}.{int(time.time())}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _parse_state(token: str) -> tuple[str, str]:
    settings = get_settings()
    secret = settings.oauth_state_secret
    if not secret:
        raise HTTPException(status_code=500, detail="OAuth state secret not configured.")
    try:
        prov, user_id, org_id, ts, sig = token.split(".")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Malformed state.") from exc
    if prov != _PROVIDER:
        raise HTTPException(status_code=400, detail="State provider mismatch.")
    payload = f"{prov}.{user_id}.{org_id}.{ts}"
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=400, detail="Invalid state signature.")
    if int(time.time()) - int(ts) > _STATE_TTL_SECONDS:
        raise HTTPException(status_code=400, detail="OAuth state expired — retry.")
    return user_id, org_id


def _settings_redirect(*, connected: str | None = None, error: str | None = None) -> RedirectResponse:
    settings = get_settings()
    base = settings.app_url.rstrip("/") + "/settings/integrations"
    if connected:
        return RedirectResponse(url=f"{base}?connected={connected}", status_code=302)
    if error:
        return RedirectResponse(url=f"{base}?error={error}", status_code=302)
    return RedirectResponse(url=base, status_code=302)


# ── OAuth: connect + callback ───────────────────────────────────────────────

@router.get("/integrations/gmail/connect")
async def gmail_connect(
    current_user: dict = Depends(verify_jwt),
    _rl: None = Depends(oauth_callback_limiter),
) -> dict[str, Any]:
    org_id, user_id = _require_user(current_user)
    settings = get_settings()
    if not settings.google_client_id:
        raise HTTPException(status_code=503, detail="Gmail integration is not configured.")
    state = _mint_state(user_id=user_id, org_id=org_id)
    return {"auth_url": gmail.build_auth_url(state=state)}


@router.get("/integrations/gmail/callback")
async def gmail_callback(
    code: str | None = Query(default=None),
    state: str = Query(...),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    """Google redirects here after consent. We exchange the code, persist
    credentials, then bounce back to /settings/integrations."""
    if error:
        # User declined consent screen.
        return _settings_redirect(error=f"gmail_oauth_{error}")
    if not code:
        return _settings_redirect(error="gmail_oauth_missing_code")

    user_id, org_id = _parse_state(state)
    try:
        payload = await gmail.exchange_code(code=code)
    except Exception as exc:
        log.error("gmail_oauth_exchange_failed: %s", exc)
        return _settings_redirect(error="gmail_oauth_failed")
    try:
        await gmail.store_credentials(org_id=org_id, user_id=user_id, token_payload=payload)
    except RuntimeError as exc:
        if str(exc) == "gmail_send_scope_not_granted":
            return _settings_redirect(error="gmail_send_scope_not_granted")
        log.error("gmail_store_credentials_failed: %s", exc)
        return _settings_redirect(error="gmail_store_failed")
    except Exception as exc:
        log.error("gmail_store_credentials_failed: %s", exc)
        return _settings_redirect(error="gmail_store_failed")
    return _settings_redirect(connected="gmail")


# ── Status / scope-check ────────────────────────────────────────────────────

@router.get("/integrations/gmail/status")
async def gmail_status(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    """Single endpoint the frontend uses to decide:
        - Show "Connect Gmail" CTA (connected = false)
        - Show "Reconnect for send permission" banner (has_send_scope = false)
        - Show "Send via Gmail" button (both true)
    """
    org_id, user_id = _require_user(current_user)
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("gmail_integrations")
        .select("email_address, scopes, connected_at, last_used_at")
        .eq("org_id", org_id)
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        return {
            "connected": False,
            "has_send_scope": False,
            "email_address": None,
        }
    scopes = row.data.get("scopes") or []
    return {
        "connected": True,
        "has_send_scope": gmail.has_send_scope(scopes),
        "email_address": row.data.get("email_address"),
        "connected_at": row.data.get("connected_at"),
        "last_used_at": row.data.get("last_used_at"),
    }


# ── Send ────────────────────────────────────────────────────────────────────

class SendEmailRequest(BaseModel):
    message_id: str = Field(..., min_length=1, max_length=64)
    to: str = Field(..., min_length=3, max_length=320)  # RFC 5321 SMTP cap
    subject: str = Field(..., min_length=1, max_length=998)  # RFC 2822 subject cap
    body: str = Field(..., min_length=1, max_length=200_000)
    cc: str | None = Field(default=None, max_length=320)
    reply_to: str | None = Field(default=None, max_length=320)
    # When true (default), attach a plain-text file containing the full
    # chunk content the LLM retrieved for this turn. Gives the recipient
    # the same source material that grounded the email body.
    include_sources: bool = True
    acknowledged_warnings: bool = Field(default=False)

    @field_validator("to")
    @classmethod
    def _check_to(cls, v: str) -> str:
        return _validate_email(v)

    @field_validator("cc", "reply_to")
    @classmethod
    def _check_optional_email(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        return _validate_email(v)


class SendEmailResponse(BaseModel):
    queued: bool
    job_id: str


@router.post("/integrations/gmail/send", response_model=SendEmailResponse)
async def gmail_send(
    body: SendEmailRequest,
    current_user: dict = Depends(verify_jwt),
) -> SendEmailResponse:
    org_id, user_id = _require_user(current_user)

    # Cheap up-front check so the user gets an actionable 403 immediately
    # rather than discovering via a silent failure after queueing. We also
    # pull email_address here so we can derive a fallback signature label
    # without a second roundtrip.
    svc = get_service_client()
    row, user_row = await asyncio.gather(
        asyncio.to_thread(
            lambda: svc.table("gmail_integrations")
            .select("id, scopes, email_address")
            .eq("org_id", org_id)
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        ),
        asyncio.to_thread(
            lambda: svc.table("users")
            .select("display_name")
            .eq("id", user_id)
            .maybe_single()
            .execute()
        ),
    )
    if not row or not row.data:
        raise HTTPException(status_code=400, detail="gmail_not_connected")
    scopes = row.data.get("scopes") or []
    if not gmail.has_send_scope(scopes):
        # Frontend pattern-matches on this code to render the re-auth banner.
        raise HTTPException(status_code=403, detail="gmail_send_scope_missing")

    # Resolve the sender's name for placeholder substitution. Prefer the
    # display_name set in their profile; fall back to the local-part of their
    # Gmail address so we never leave a raw `[Your Name]` in the wire copy.
    display_name = ((user_row.data or {}).get("display_name") or "").strip() if user_row else ""
    sender_email = (row.data.get("email_address") or "").strip()
    sender_label = display_name or (sender_email.split("@", 1)[0] if sender_email else "")
    final_subject = _substitute_signature(body.subject, sender_label)
    final_body = _substitute_signature(body.body, sender_label)

    # Verify the message belongs to this org before we touch it. RLS would
    # also block a write, but we want a clean 404 — not a confusing 500. We
    # pull `sources` in the same query so the attachment builder doesn't
    # need a second roundtrip.
    msg = await asyncio.to_thread(
        lambda: svc.table("messages")
        .select("id, org_id, conversation_id, delivery_status, sources")
        .eq("id", body.message_id)
        .maybe_single()
        .execute()
    )
    if not msg or not msg.data or msg.data.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail="message_not_found")
    existing_delivery = msg.data.get("delivery_status") or {}
    if existing_delivery.get("status") == "sent":
        raise HTTPException(status_code=409, detail="message_already_sent")
    conversation_id: str | None = msg.data.get("conversation_id")

    # Shared outbound guard rails — same as Slack/Notion/Gdocs.
    from app.services.outbound_gate import (
        OutboundGateError,
        enforce_outbound_write_guards,
    )

    try:
        await enforce_outbound_write_guards(
            channel="gmail",
            org_id=org_id,
            user_id=user_id,
            message_id=body.message_id,
            content=final_body,
            competitor_acknowledged=body.acknowledged_warnings,
        )
    except OutboundGateError as gate_err:
        headers: dict[str, str] = {}
        retry = gate_err.extra.get("retry_after")
        if isinstance(retry, int) and retry > 0:
            headers["Retry-After"] = str(retry)
        raise HTTPException(
            status_code=gate_err.status_code,
            detail=gate_err.code,
            headers=headers or None,
        )

    # Build the sources attachment (best-effort, optional). On any failure
    # we still send the email — the attachment is a nice-to-have, not a
    # blocker for the user's primary action.
    attachment_payload: dict[str, Any] | None = None
    if body.include_sources:
        msg_sources = msg.data.get("sources") or []
        if msg_sources:
            attachment = await _build_sources_attachment(
                svc=svc, org_id=org_id, sources=msg_sources
            )
            if attachment:
                # Inngest events are JSON, so we base64 the bytes before they
                # ride the event payload. The worker un-encodes before passing
                # to send_email().
                attachment_payload = {
                    "filename": attachment["filename"],
                    "mime_type": attachment["mime_type"],
                    "content_b64": base64.b64encode(attachment["content"]).decode("ascii"),
                }

    job_id = str(uuid.uuid4())
    queued_at = datetime.now(UTC).isoformat()

    # Optimistically mark queued so the UI shows "Sending…" immediately.
    await asyncio.to_thread(
        lambda: svc.table("messages")
        .update({
            "delivery_status": {
                "channel": "gmail",
                "status": "queued",
                "recipient": body.to,
                "job_id": job_id,
                "queued_at": queued_at,
            }
        })
        .eq("id", body.message_id)
        .execute()
    )

    from app.services.outbound_audit import record_queued

    await record_queued(
        run_id=job_id,
        channel="gmail",
        org_id=org_id,
        user_id=user_id,
        message_id=body.message_id,
        destination=body.to,
        input_payload={
            "to_emails": body.to,
            "subject": final_subject,
            "body": final_body,
        },
    )

    # Fan out to Inngest. Idempotency id = job_id so duplicate clicks within
    # the dedupe window don't double-send.
    client = get_inngest_client()
    await client.send(
        inngest.Event(
            name="gmail/send-email",
            data={
                "job_id": job_id,
                "message_id": body.message_id,
                "org_id": org_id,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "to": body.to,
                "subject": final_subject,
                "body": final_body,
                "cc": body.cc,
                "reply_to": body.reply_to,
                "attachments": [attachment_payload] if attachment_payload else [],
            },
            id=f"gmail-send-{job_id}",
        )
    )
    return SendEmailResponse(queued=True, job_id=job_id)


@router.delete("/integrations/gmail", status_code=status.HTTP_204_NO_CONTENT)
async def gmail_disconnect(current_user: dict = Depends(verify_jwt)) -> None:
    org_id, user_id = _require_user(current_user)
    await gmail.disconnect(org_id=org_id, user_id=user_id)
