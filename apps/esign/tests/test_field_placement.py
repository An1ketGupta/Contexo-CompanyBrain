"""Regression tests for signature-marker detection.

The bug behind them: DOCX→PDF conversion emits the bracketing ◇ of
`◇SIGN:HR◇` as its own glyph run on its own baseline box, and PyMuPDF's
`search_for` would not match across that boundary in a real Gotenberg-produced
LOI — so nothing was found, the marker text was never whited out, and every
signature field silently fell back to the bottom of the last page instead of
where the template put it.

These fixtures approximate that layout rather than reproduce the exact
extraction quirk (synthetic PDFs are easier to match than Gotenberg's), so they
guard the behaviour that matters: fields anchored at the marker, and both
brackets covered along with the core.
"""
from __future__ import annotations

import fitz
import pytest

from app.field_placement import extract_fields_and_clean

MARKER_X = 100.0
MARKER_Y = 300.0
DIAMOND = "◇"
FONT = "cjk"  # a built-in font that actually has ◇ (U+25C7)
BODY_SIZE = 11.0
BRACKET_SIZE = 13.0


def _write_split_marker(page: fitz.Page, core: str, y: float) -> None:
    """Draw ◇ | core | ◇ as three runs in two fonts, at one baseline.

    Gotenberg falls back to a second font for ◇ because the body font has no
    such glyph. The fallback lands on its own baseline box, so PyMuPDF's text
    model puts the brackets on a different line from the core and refuses to
    match a search spanning the two.
    """
    bracket, body = fitz.Font(FONT), fitz.Font("helv")
    x = MARKER_X
    for chunk, font, size, baseline in (
        (DIAMOND, bracket, BRACKET_SIZE, y - 1.5),
        (core, body, BODY_SIZE, y),
        (DIAMOND, bracket, BRACKET_SIZE, y - 1.5),
    ):
        writer = fitz.TextWriter(page.rect)
        writer.append((x, baseline), chunk, font=font, fontsize=size)
        writer.write_text(page)
        x += font.text_length(chunk, fontsize=size)


def _pdf_with_split_marker(core: str = "SIGN:HR") -> bytes:
    doc = fitz.open()
    _write_split_marker(doc.new_page(), core, MARKER_Y)
    return doc.tobytes()


def test_marker_is_found_when_diamonds_are_a_separate_run():
    pdf = _pdf_with_split_marker()
    page = fitz.open(stream=pdf, filetype="pdf")[0]

    _, fields = extract_fields_and_clean(pdf, roles=["hr"])

    assert len(fields) == 1
    field = fields[0]
    assert field.page == 1
    # Anchored at the marker, not at the bottom-of-last-page fallback.
    assert field.position_y == pytest.approx(
        (MARKER_Y - 11) / page.rect.height * 100, abs=2.0
    )


def test_bracketing_diamonds_are_whited_out():
    cleaned, _ = extract_fields_and_clean(_pdf_with_split_marker(), roles=["hr"])

    page = fitz.open(stream=cleaned, filetype="pdf")[0]
    diamonds = page.search_for("◇")
    assert len(diamonds) == 2, "test fixture should still have both brackets"

    pixmap = page.get_pixmap()
    for rect in diamonds:
        centre = pixmap.pixel(int(rect.x0 + rect.width / 2), int(rect.y0 + rect.height / 2))
        assert centre == (255, 255, 255), "marker glyph is still visible"


def test_missing_marker_falls_back_to_the_last_page():
    doc = fitz.open()
    doc.new_page()
    doc.new_page()
    pdf = doc.tobytes()

    _, fields = extract_fields_and_clean(pdf, roles=["candidate"])

    assert len(fields) == 1
    assert fields[0].page == 2
    assert fields[0].position_y > 80.0  # bottom of the page


def test_each_marker_occurrence_gets_its_own_field():
    doc = fitz.open()
    page = doc.new_page()
    for y in (200.0, 400.0):
        _write_split_marker(page, "SIGN:CANDIDATE", y)

    _, fields = extract_fields_and_clean(doc.tobytes(), roles=["candidate"])

    assert len(fields) == 2
    assert len({round(f.position_y) for f in fields}) == 2
