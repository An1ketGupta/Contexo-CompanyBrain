"""Deterministic document generation — no LLM, ever.

Loads the original template bytes, verifies that the anchors still describe the
document they were detected against, splices values in at run level, and
converts to PDF.

There is no templating language anywhere in this path. A customer's document
never has to be valid Jinja source, which is what made the pre-096 pipeline
fail: one hand-typed `{{ Signing Date }}` — two words, not an expression — took
down the render of an entire six-page appointment letter with a parser error no
HR user could act on. Here a malformed placeholder is just text.

Failure modes, all raised BEFORE any bytes are modified, so a failed render can
never leave a half-filled document:

  * `SlotDriftError`  — an anchor paragraph is not the text its slot was mapped
    against. HR edited the template after confirming, so the offsets can no
    longer be vouched for.
  * `MissingValueError` — a confirmed slot's variable has no value in the
    context. Should have been caught by the validation engine; this is the
    backstop that keeps a half-filled contract from ever existing.
  * `UnmappedSlotError` — a confirmed slot has no variable at all. The database
    CHECK makes this unreachable; it is asserted here anyway because the cost of
    being wrong is a blank in a legal document.
"""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

from docx import Document

from app.services.documents.constants import (
    ACTION_INSERT_AFTER_LABEL,
    ACTION_INSERT_EMPTY_CELL,
    ACTION_REPLACE_SPAN,
)
from app.services.documents.docx_positions import (
    canonical_paragraphs,
    paragraph_hash,
    paragraph_run_text,
)
from app.services.documents.generation.docx_splice import SpliceEdit, splice_paragraph
from app.services.pdf.renderer import convert_docx_to_pdf

log = logging.getLogger(__name__)


class DocumentRenderError(RuntimeError):
    """Base for render failures the caller should surface to HR."""


class SlotDriftError(DocumentRenderError):
    """An anchor paragraph no longer matches what the slot was mapped against.

    Deliberately recoverable and distinct from a hard render failure: the fix is
    for HR to re-open the template and re-confirm, not for an operator to
    inspect a stack trace. We refuse to write a value at an offset we cannot
    vouch for, because the edit may have shifted or removed the field and
    guessing risks splicing a salary into the middle of a clause.
    """

    def __init__(self, paragraph_indexes: list[int], *, template_name: str | None = None):
        self.paragraph_indexes = paragraph_indexes
        where = ", ".join(str(i) for i in paragraph_indexes[:5])
        detail = (
            f"This template changed since its fields were confirmed "
            f"({len(paragraph_indexes)} field(s) affected, at paragraph(s) {where}). "
            "Re-open the template and confirm the fields again."
        )
        if template_name:
            detail = f"[{template_name}] " + detail
        super().__init__(detail)


class MissingValueError(DocumentRenderError):
    """A confirmed slot's variable has no value in the render context."""

    def __init__(self, variable: str, *, template_name: str | None = None):
        self.variable = variable
        detail = (
            f"No value was supplied for '{variable}', which this template "
            "requires. Fill it in and generate again."
        )
        if template_name:
            detail = f"[{template_name}] " + detail
        super().__init__(detail)


class UnmappedSlotError(DocumentRenderError):
    """A confirmed slot has no variable assigned."""


def format_value(value: Any) -> str:
    """Render a context value as document text.

    `None` becomes an empty string rather than the literal "None" — a missing
    optional value should read as a blank, not as a Python repr in a contract.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def _resolve_edits(
    *,
    text: str,
    slots: list[dict[str, Any]],
    context: dict[str, Any],
) -> list[SpliceEdit]:
    """Turn slot rows for one paragraph into splice edits."""
    edits: list[SpliceEdit] = []
    for slot in slots:
        value = format_value(context.get(slot["variable"]))
        action = slot.get("action") or ACTION_REPLACE_SPAN
        start = slot["start_offset"]
        end = slot["end_offset"]

        if action == ACTION_INSERT_AFTER_LABEL:
            # Separate the value from the label's colon, unless the label
            # already ends in whitespace.
            head = text[:start]
            separator = "" if not head or head.endswith((" ", "\t")) else " "
            edits.append(SpliceEdit(start, end, separator + value))
        elif action == ACTION_INSERT_EMPTY_CELL:
            # The cell is empty by definition, so an insertion at its start is
            # equivalent to replacing its contents.
            edits.append(SpliceEdit(start, end, value))
        else:
            edits.append(SpliceEdit(start, end, value))
    return edits


def fill_docx(
    *,
    docx_bytes: bytes,
    slots: list[dict[str, Any]],
    context: dict[str, Any],
    template_name: str | None = None,
) -> bytes:
    """Splice `context` values into `docx_bytes` at each slot's anchor.

    Expects only CONFIRMED slots — filtering is the caller's job, so that "what
    gets rendered" is decided in exactly one place rather than re-derived here.
    Each slot dict needs: `paragraph_index`, `paragraph_hash`, `start_offset`,
    `end_offset`, `action`, and `variable` (the resolved internal_name).

    Returns new bytes; the input is never mutated.
    """
    if not slots:
        # A template with no fields is odd but not an error. Hand it through
        # untouched; the post-render scan is what flags it to HR.
        return docx_bytes

    # ── Every variable must resolve, before anything is written ────────────
    unmapped = [s for s in slots if not s.get("variable")]
    if unmapped:
        raise UnmappedSlotError(
            f"{len(unmapped)} confirmed field(s) have no variable assigned. "
            "Re-open the template and pick a value for each field."
        )

    for slot in slots:
        if slot["variable"] not in context:
            raise MissingValueError(slot["variable"], template_name=template_name)

    doc = Document(io.BytesIO(docx_bytes))
    canonical = canonical_paragraphs(doc)

    # ── Drift guard, per-paragraph ─────────────────────────────────────────
    # Only the paragraphs about to be written into are checked, so HR rewording
    # an unrelated clause elsewhere in a long template never invalidates a
    # confirmed field.
    drifted: list[int] = []
    for slot in slots:
        idx = slot["paragraph_index"]
        if not (0 <= idx < len(canonical)):
            drifted.append(idx)
            continue
        current = paragraph_hash(paragraph_run_text(canonical[idx][0]))
        if current != slot.get("paragraph_hash"):
            drifted.append(idx)
    if drifted:
        raise SlotDriftError(sorted(set(drifted)), template_name=template_name)

    # ── Apply, grouped by paragraph ────────────────────────────────────────
    by_paragraph: dict[int, list[dict[str, Any]]] = {}
    for slot in slots:
        by_paragraph.setdefault(slot["paragraph_index"], []).append(slot)

    for para_idx, para_slots in by_paragraph.items():
        paragraph, _kind = canonical[para_idx]
        text = paragraph_run_text(paragraph)
        edits = _resolve_edits(text=text, slots=para_slots, context=context)
        splice_paragraph(paragraph, edits)

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


async def render_to_pdf(
    *,
    docx_bytes: bytes,
    slots: list[dict[str, Any]],
    context: dict[str, Any],
    template_name: str | None = None,
) -> tuple[bytes, bytes | None]:
    """Fill the template, then convert to PDF.

    Returns `(filled_docx, pdf_or_None)`. A PDF conversion failure is logged and
    yields None rather than raising: the spec requires that a failed conversion
    must not cost HR the generated DOCX, and `generated_files` models the PDF as
    optional for exactly this reason.
    """
    filled_docx = await asyncio.to_thread(
        fill_docx,
        docx_bytes=docx_bytes,
        slots=slots,
        context=context,
        template_name=template_name,
    )

    try:
        pdf_bytes = await convert_docx_to_pdf(filled_docx)
    except Exception as exc:  # noqa: BLE001 — see docstring: PDF is optional
        # Deliberately broad. Gotenberg being unconfigured raises
        # PdfRenderUnavailable, a bad conversion raises PdfRenderError, and a
        # transport hiccup raises neither — but all three have the same right
        # answer, which is to keep the DOCX and let HR retry the conversion.
        log.warning(
            "documents.pdf_conversion_failed template=%s err=%s", template_name, exc
        )
        return filled_docx, None

    return filled_docx, pdf_bytes
