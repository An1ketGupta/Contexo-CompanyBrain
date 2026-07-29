"""Canonical position addressing for DOCX paragraphs.

Every part of the document pipeline that needs to point at a location in a Word
document — the blank detector, the persisted `doc_template_slots` rows, and the
direct-fill renderer — addresses it as `(paragraph_index, character offset)`
where `paragraph_index` is a position in the enumeration `canonical_paragraphs()`
returns.

That only works if detection and rendering agree byte-for-byte on the
enumeration and on how a paragraph's text is read. This module is the single
definition of both, so a second consumer can't drift from the first: a slot
detected at index 41 must resolve to the same paragraph weeks later when a
candidate's document is generated.

Carried over unchanged (bar naming) from the previous pipeline's
`agents/onboarding_v2/docx_positions.py` — the addressing scheme was the one
part of that design that was correct, and re-deriving it would only risk
introducing an off-by-one between detection and rendering.
"""
from __future__ import annotations

import hashlib
from typing import Any

from docx.text.paragraph import Paragraph


def canonical_paragraphs(doc: Any) -> list[tuple[Paragraph, str]]:
    """Enumerate every paragraph in a stable, document-first order.

    Body paragraphs and table cells are walked in true document order (so a
    table's cells appear where the table sits), followed by header and footer
    paragraphs. This ordering is an implementation detail — the only contract
    is that it is *deterministic* and *identical* between extraction and
    write-back, so a slot's `paragraph_index` always resolves to the same
    paragraph.
    """
    from docx.oxml.ns import qn

    out: list[tuple[Paragraph, str]] = []
    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            out.append((Paragraph(child, doc), "body"))
        elif child.tag == qn("w:tbl"):
            # `iter` yields every descendant paragraph (including nested
            # tables) once, in document order.
            for p_elem in child.iter(qn("w:p")):
                out.append((Paragraph(p_elem, doc), "table"))

    for section in doc.sections:
        if section.header:
            for p in section.header.paragraphs:
                out.append((p, "header"))
        if section.footer:
            for p in section.footer.paragraphs:
                out.append((p, "footer"))

    return out


def paragraph_run_text(paragraph: Paragraph) -> str:
    """Concatenate a paragraph's run text — the exact string offsets index into.

    We deliberately join `paragraph.runs` rather than read `Paragraph.text`:
    in python-docx 1.x the latter also pulls in hyperlink text, which would
    make detection-time offsets disagree with the run text rewritten at fill
    time. Joining runs keeps both sides byte-for-byte identical.
    """
    return "".join(r.text or "" for r in paragraph.runs)


def paragraph_hash(text: str) -> str:
    """Fingerprint one paragraph's text for drift detection.

    Truncated sha256 — this guards against a template being edited between
    "HR confirmed this slot" and "the agent rendered a document", not against
    an adversary, so 16 hex chars is ample and keeps the stored rows readable.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def file_hash(file_bytes: bytes) -> str:
    """Fingerprint a whole template file.

    Lets the UI answer "were these slots detected against the bytes currently
    stored?" with one comparison instead of re-hashing every anchor. The
    per-paragraph hash above is still what gates rendering — this is the
    coarse signal for prompting a re-analyze.
    """
    return hashlib.sha256(file_bytes).hexdigest()[:16]
