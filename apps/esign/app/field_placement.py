"""Turn the onboarding PDF's signature markers into Documenso signature-field
coordinates.

apps/api's template_vars.py renders each signer's signature block as a
sentinel marker string (◇SIGN:HR◇ / ◇SIGN:CANDIDATE◇) — the same markers the
old PyMuPDF stamper searched for. We keep them: they tell us *where on the
page* each role signs, positioned by whoever authored the template.

Documenso expects field coordinates as **percentages of the page** (0–100),
origin top-left. PyMuPDF gives us marker rects in points (also top-left
origin), so the conversion is a straight ratio against page width/height.

Two things happen here:
  1. Find every marker occurrence per role → a Documenso field box anchored at
     the marker, expanded to a sensible signature size (a marker glyph run is
     too small to sign in).
  2. White out the marker text in the PDF we hand to Documenso, so the literal
     "◇SIGN:HR◇" never shows behind the drawn signature.

Returns the cleaned PDF bytes + the field list. If a role's marker is absent,
we fall back to a single box at the bottom of the last page so signing never
breaks on a template that forgot the placeholder — mirrors the old stamper's
fallback.
"""
from __future__ import annotations

from dataclasses import dataclass

import fitz  # PyMuPDF

# Must match apps/api template_vars.py's rendered marker values exactly.
SIGNATURE_MARKERS: dict[str, str] = {
    "hr": "◇SIGN:HR◇",
    "candidate": "◇SIGN:CANDIDATE◇",
}

# A signature field wants a real box, not the width of the marker glyphs.
# Anchor the box's top-left at the marker; these are point dimensions,
# clamped so the box never runs off the page edge.
_FIELD_WIDTH_PT = 180.0
_FIELD_HEIGHT_PT = 48.0
_FALLBACK_MARGIN_PT = 36.0
_DUPLICATE_OVERLAP_RATIO = 0.5


@dataclass(frozen=True)
class SignatureField:
    """One Documenso SIGNATURE field for a given role, in page-percentage
    coordinates (0–100, origin top-left) — the shape Documenso's
    /envelope/field/create-many wants."""
    role: str
    page: int  # 1-indexed
    position_x: float
    position_y: float
    width: float
    height: float


def _dedupe_rects(rects: list[fitz.Rect]) -> list[fitz.Rect]:
    """Collapse near-duplicate hit boxes for what is visually one marker.
    DOCX→PDF conversion can emit overlapping rects for a single glyph run."""
    merged: list[fitz.Rect] = []
    for rect in rects:
        match = next(
            (
                m
                for m in merged
                if (m & rect).get_area()
                > _DUPLICATE_OVERLAP_RATIO * min(m.get_area(), rect.get_area())
            ),
            None,
        )
        if match is not None:
            merged[merged.index(match)] = match | rect
        else:
            merged.append(fitz.Rect(rect))
    return merged


def _field_box(anchor: fitz.Rect, page: fitz.Rect) -> fitz.Rect:
    """A signature-sized box anchored at the marker's top-left, clamped to the
    page so no coordinate exceeds 100%."""
    x0 = min(anchor.x0, page.width - _FIELD_WIDTH_PT)
    y0 = min(anchor.y0, page.height - _FIELD_HEIGHT_PT)
    x0 = max(x0, 0.0)
    y0 = max(y0, 0.0)
    return fitz.Rect(x0, y0, x0 + _FIELD_WIDTH_PT, y0 + _FIELD_HEIGHT_PT)


def _to_pct(box: fitz.Rect, page: fitz.Rect) -> tuple[float, float, float, float]:
    return (
        round(box.x0 / page.width * 100, 4),
        round(box.y0 / page.height * 100, 4),
        round(box.width / page.width * 100, 4),
        round(box.height / page.height * 100, 4),
    )


def extract_fields_and_clean(
    pdf_bytes: bytes, *, roles: list[str]
) -> tuple[bytes, list[SignatureField]]:
    """For each role, locate its marker(s), emit a percentage-coordinate
    signature field, and white out the marker text. Returns (cleaned PDF
    bytes, fields).

    `roles` scopes which markers to look for (an AL+NDA candidate-only bundle
    passes ["candidate"]; a routed LOI passes ["hr", "candidate"]).
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        fields: list[SignatureField] = []
        for role in roles:
            marker = SIGNATURE_MARKERS.get(role)
            found_any = False
            if marker:
                for page_index, page in enumerate(doc):
                    for rect in _dedupe_rects(page.search_for(marker)):
                        # Cover the literal marker text before Documenso draws
                        # its field over the same spot.
                        page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1), overlay=True)
                        box = _field_box(rect, page.rect)
                        px, py, w, h = _to_pct(box, page.rect)
                        fields.append(
                            SignatureField(
                                role=role, page=page_index + 1,
                                position_x=px, position_y=py, width=w, height=h,
                            )
                        )
                        found_any = True

            if not found_any:
                # No marker for this role — fall back to bottom of last page so
                # a template missing the placeholder still gets a signable field.
                last = doc[-1]
                y0 = last.rect.height - _FALLBACK_MARGIN_PT - _FIELD_HEIGHT_PT
                box = fitz.Rect(
                    _FALLBACK_MARGIN_PT, y0,
                    _FALLBACK_MARGIN_PT + _FIELD_WIDTH_PT, y0 + _FIELD_HEIGHT_PT,
                )
                px, py, w, h = _to_pct(box, last.rect)
                fields.append(
                    SignatureField(
                        role=role, page=len(doc),
                        position_x=px, position_y=py, width=w, height=h,
                    )
                )

        return doc.tobytes(), fields
    finally:
        doc.close()
