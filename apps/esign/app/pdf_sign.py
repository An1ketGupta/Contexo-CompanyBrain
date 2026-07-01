"""PDF signature stamping via PyMuPDF (fitz) — no cryptographic signing in
this pass (see plan: visible signature + audit trail, PAdES is a future
add-on). Two operations:

  1. stamp_signature() — finds the sentinel marker text this signer's
     {{ hr_signature_block }} / {{ candidate_signature_block }} template
     variable rendered as (see apps/api's template_vars.py), covers it, and
     draws the signature (image or typed name) in its place. Falls back to
     a fixed bottom-of-last-page position if no marker is found, so a
     template without the placeholder never breaks signing.

  2. append_audit_page() — adds a final "Certificate of Completion" page
     listing every signer's identity/consent/timestamp metadata + the
     document hash, appended once, when the last signer completes.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime

import fitz  # PyMuPDF

# Sentinel marker strings — must match template_vars.py's rendered values
# for hr_signature_block / candidate_signature_block exactly.
SIGNATURE_MARKERS: dict[str, str] = {
    "hr": "◇SIGN:HR◇",
    "candidate": "◇SIGN:CANDIDATE◇",
}

_MARKER_BOX_HEIGHT = 60.0
_MARKER_BOX_WIDTH = 200.0
_FALLBACK_MARGIN = 36.0


class SignatureImageError(ValueError):
    """Raised when signature_image_base64 isn't valid image data."""


def _decode_signature_image(data_url_or_b64: str) -> bytes:
    raw = data_url_or_b64
    if "," in raw and raw.strip().lower().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SignatureImageError("signature_image_base64 is not valid base64 image data") from exc


def stamp_signature(
    pdf_bytes: bytes,
    *,
    role: str,
    signer_name: str,
    signature_image_base64: str | None,
    typed_name: str | None,
    signed_at: datetime,
) -> bytes:
    """Return new PDF bytes with this signer's signature stamped in place
    of their marker (or at a fallback position)."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        marker = SIGNATURE_MARKERS.get(role)
        rect = None
        marker_page = None
        if marker:
            for page in doc:
                hits = page.search_for(marker)
                if hits:
                    rect = hits[0]
                    marker_page = page
                    break

        if rect is None:
            # No marker found — fall back to the bottom of the last page,
            # never fail the sign step over a missing placeholder.
            marker_page = doc[-1]
            rect = fitz.Rect(
                _FALLBACK_MARGIN,
                marker_page.rect.height - _FALLBACK_MARGIN - _MARKER_BOX_HEIGHT,
                _FALLBACK_MARGIN + _MARKER_BOX_WIDTH,
                marker_page.rect.height - _FALLBACK_MARGIN,
            )
        else:
            # Cover the literal marker text with white before drawing over it.
            marker_page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1), overlay=True)

        if signature_image_base64:
            img_bytes = _decode_signature_image(signature_image_base64)
            marker_page.insert_image(rect, stream=img_bytes, keep_proportion=True)
        else:
            label = typed_name or signer_name
            marker_page.insert_textbox(
                rect,
                label,
                fontname="helv",
                fontsize=18,
                color=(0.1, 0.1, 0.5),
                align=fitz.TEXT_ALIGN_LEFT,
            )

        caption_rect = fitz.Rect(rect.x0, rect.y1, rect.x1 + 150, rect.y1 + 14)
        marker_page.insert_textbox(
            caption_rect,
            f"Signed by {signer_name} on {signed_at.strftime('%Y-%m-%d %H:%M UTC')}",
            fontname="helv",
            fontsize=7,
            color=(0.4, 0.4, 0.4),
        )

        return doc.tobytes()
    finally:
        doc.close()


@dataclass
class AuditEntry:
    role: str
    name: str
    email: str
    ip_address: str
    user_agent: str
    consent_accepted_at: datetime
    completed_at: datetime


def append_audit_page(
    pdf_bytes: bytes,
    *,
    document_sha256: str,
    entries: list[AuditEntry],
) -> bytes:
    """Append a Certificate of Completion page listing every signer's
    identity/consent/timestamp metadata and the pre-signature document hash."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc.new_page()
        lines = [
            "Certificate of Completion",
            "",
            f"Document SHA-256 (pre-signature): {document_sha256}",
            "",
        ]
        for e in entries:
            lines += [
                f"Signer: {e.name} <{e.email}> ({e.role})",
                f"  Consent accepted: {e.consent_accepted_at.isoformat()}",
                f"  Signed: {e.completed_at.isoformat()}",
                f"  IP address: {e.ip_address}",
                f"  User agent: {e.user_agent}",
                "",
            ]
        text = "\n".join(lines)
        page.insert_textbox(
            fitz.Rect(48, 48, page.rect.width - 48, page.rect.height - 48),
            text,
            fontname="helv",
            fontsize=10,
        )
        return doc.tobytes()
    finally:
        doc.close()
