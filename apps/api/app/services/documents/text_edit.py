"""Read a DOCX as editable lines, and write edited lines back into it.

This is what backs "edit the draft here" rather than "download it, open Word,
re-upload it". HR sees one textarea per paragraph; a save posts back only the
paragraphs whose text actually changed.

The write-back goes through `splice_paragraph`, so a paragraph nobody touched
is never assigned to and its XML stays bit-identical — the pipeline's
formatting guarantee holds for the review loop too. Inside an *edited*
paragraph the text necessarily takes the style of its first non-empty run,
because a flat string carries no run boundaries to restore: editing
`Annual CTC: ₹12,00,000` as one line loses the bold on `Annual CTC:`. That
trade is confined to the lines HR deliberately rewrote, which is the point —
the old flat-text editor applied it to every paragraph containing a field.

Paragraph indices are `canonical_paragraphs()` positions, the same addressing
the detector and renderer use, so an index means the same paragraph on the way
in and on the way out. Blank paragraphs are dropped from the read but keep
their true index, so nothing shifts.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

from docx import Document

from app.services.documents.docx_positions import (
    canonical_paragraphs,
    file_hash,
    paragraph_run_text,
)
from app.services.documents.generation.docx_splice import SpliceEdit, splice_paragraph


class TextEditError(Exception):
    """The DOCX couldn't be read, or an edit didn't address a real paragraph."""


@dataclass(frozen=True)
class EditableParagraph:
    index: int
    kind: str
    text: str


def extract_editable_paragraphs(docx_bytes: bytes) -> list[EditableParagraph]:
    """Every non-blank paragraph, in canonical order, with its true index."""
    try:
        doc = Document(io.BytesIO(docx_bytes))
        canonical = canonical_paragraphs(doc)
    except Exception as exc:  # noqa: BLE001 — a bad upload, not a bug
        raise TextEditError(
            "Couldn't read this Word file. It may be corrupted or "
            "password-protected."
        ) from exc

    return [
        EditableParagraph(index=idx, kind=kind, text=text)
        for idx, (paragraph, kind) in enumerate(canonical)
        if (text := paragraph_run_text(paragraph)).strip()
    ]


def apply_paragraph_edits(
    *, docx_bytes: bytes, edits: dict[int, str]
) -> tuple[bytes, int]:
    """Overwrite whole paragraphs by index; return the new bytes and how many
    paragraphs actually changed.

    An index outside the document is an error rather than a silent skip: it
    means the client's view of the file has drifted from the stored one, and
    guessing which paragraph was meant is how a wrong contract gets sent.
    """
    try:
        doc = Document(io.BytesIO(docx_bytes))
        canonical = canonical_paragraphs(doc)
    except Exception as exc:  # noqa: BLE001
        raise TextEditError(
            "Couldn't read this Word file. It may be corrupted or "
            "password-protected."
        ) from exc

    changed = 0
    for index, new_text in edits.items():
        if index < 0 or index >= len(canonical):
            raise TextEditError(
                f"Line {index} isn't in this document any more. Reopen the "
                "editor to pick up the current draft."
            )
        paragraph, _kind = canonical[index]
        original = paragraph_run_text(paragraph)
        if new_text == original:
            continue
        if splice_paragraph(
            paragraph, [SpliceEdit(0, len(original), new_text)]
        ):
            changed += 1

    if changed == 0:
        return docx_bytes, 0

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue(), changed


def draft_fingerprint(docx_bytes: bytes) -> str:
    """Identifies the exact bytes a set of edits was made against.

    The editor sends this back with a save. If it no longer matches, the draft
    was re-rendered underneath HR (a resumed agent step, another reviewer) and
    their paragraph indices may point somewhere else — better to reload than to
    write blind.
    """
    return file_hash(docx_bytes)
