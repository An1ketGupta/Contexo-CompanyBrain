"""PDF signature stamping via PyMuPDF (fitz) — no cryptographic signing in
this pass (see plan: visible signature + audit trail, PAdES is a future
add-on). Two operations:

  1. stamp_signature() — finds every occurrence of the sentinel marker text
     this signer's {{ hr_signature_block }} / {{ candidate_signature_block }}
     template variable rendered as (see apps/api's template_vars.py), covers
     each one, and draws the signature (image or typed name) in its place. A
     template may place the marker in more than one spot (e.g. a signature
     required on multiple pages) — all occurrences are stamped. Falls back to
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
_DUPLICATE_OVERLAP_RATIO = 0.5


def _dedupe_rects(rects: list[fitz.Rect]) -> list[fitz.Rect]:
    """Collapse near-duplicate hit boxes for what is visually one marker.

    DOCX→PDF conversion (Gotenberg/LibreOffice) can make search_for() return
    more than one rect for a single marker occurrence — e.g. bold emulation
    redraws the glyph run slightly offset, or the placeholder was split
    across adjacent runs in the source DOCX. Without this, each duplicate
    hit gets its own signature stamp drawn on top of the others."""
    merged: list[fitz.Rect] = []
    for rect in rects:
        match = next(
            (
                m
                for m in merged
                if (m & rect).get_area() > _DUPLICATE_OVERLAP_RATIO * min(m.get_area(), rect.get_area())
            ),
            None,
        )
        if match is not None:
            merged[merged.index(match)] = match | rect
        else:
            merged.append(fitz.Rect(rect))
    return merged


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
    """Return new PDF bytes with this signer's signature stamped at every
    occurrence of their marker (or at a single fallback position)."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        marker = SIGNATURE_MARKERS.get(role)
        # Collect every marker occurrence across all pages, not just the first.
        targets: list[tuple[fitz.Page, fitz.Rect]] = []
        if marker:
            for page in doc:
                for rect in _dedupe_rects(page.search_for(marker)):
                    targets.append((page, rect))

        # Decode the signature image once and reuse it for every stamp.
        img_bytes = (
            _decode_signature_image(signature_image_base64)
            if signature_image_base64
            else None
        )

        if not targets:
            # No marker found — fall back to the bottom of the last page,
            # never fail the sign step over a missing placeholder. Nothing
            # to cover, so no white-out here.
            fallback_page = doc[-1]
            fallback_rect = fitz.Rect(
                _FALLBACK_MARGIN,
                fallback_page.rect.height - _FALLBACK_MARGIN - _MARKER_BOX_HEIGHT,
                _FALLBACK_MARGIN + _MARKER_BOX_WIDTH,
                fallback_page.rect.height - _FALLBACK_MARGIN,
            )
            _draw_stamp(
                fallback_page,
                fallback_rect,
                img_bytes=img_bytes,
                typed_name=typed_name,
                signer_name=signer_name,
                signed_at=signed_at,
                cover_marker=False,
            )
        else:
            for page, rect in targets:
                _draw_stamp(
                    page,
                    rect,
                    img_bytes=img_bytes,
                    typed_name=typed_name,
                    signer_name=signer_name,
                    signed_at=signed_at,
                    cover_marker=True,
                )

        return doc.tobytes()
    finally:
        doc.close()


def _draw_stamp(
    page: "fitz.Page",
    rect: "fitz.Rect",
    *,
    img_bytes: bytes | None,
    typed_name: str | None,
    signer_name: str,
    signed_at: datetime,
    cover_marker: bool,
) -> None:
    """Draw one signature stamp (image or typed name) plus its caption at
    `rect` on `page`. Whites out the underlying marker text first when
    `cover_marker` is set."""
    if cover_marker:
        # Cover the literal marker text with white before drawing over it.
        page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1), overlay=True)

    if img_bytes is not None:
        page.insert_image(rect, stream=img_bytes, keep_proportion=True)
    else:
        label = typed_name or signer_name
        page.insert_textbox(
            rect,
            label,
            fontname="helv",
            fontsize=18,
            color=(0.1, 0.1, 0.5),
            align=fitz.TEXT_ALIGN_LEFT,
        )

    caption_rect = fitz.Rect(rect.x0, rect.y1, rect.x1 + 150, rect.y1 + 14)
    page.insert_textbox(
        caption_rect,
        f"Signed by {signer_name} on {signed_at.strftime('%Y-%m-%d %H:%M UTC')}",
        fontname="helv",
        fontsize=7,
        color=(0.4, 0.4, 0.4),
    )


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
