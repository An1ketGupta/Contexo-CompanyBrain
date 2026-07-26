"""Direct-fill DOCX rendering — write values at recorded positions, no Jinja.

This is the counterpart to `renderer.fill_docx_template`, and it exists because
routing customer documents through a templating language turned out to be the
wrong contract. docxtpl requires the `.docx` to be *valid Jinja source*: one
hand-typed `{{ Signing Date }}` anywhere in a 6-page appointment letter fails
the whole render with a parser error, and HR has no way to know which of their
placeholders was at fault.

Here nothing is parsed. Each `template_field_slots` row says "at paragraph 41,
characters 17–29, put the value of `ctc`", and this module does exactly that
against the original, untouched customer document. A malformed placeholder is
just text; there is no syntax to get wrong.

Ordering matters and is load-bearing: slots within a paragraph are applied
right-to-left so that rewriting one span never shifts the offsets of a span not
yet written.

Two failure modes, both raised BEFORE any bytes are modified so a failed render
can never leave a half-filled document:

  * `TemplateVariableError` — a confirmed slot names a variable the caller
    didn't supply. Reused from `renderer` on purpose: the agent's existing
    except-branch already handles it, so the slots path needs no new handling.
  * `SlotDriftError` — the anchor paragraph is not the text the slot was mapped
    against (HR edited the template after confirming).
"""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

from docx import Document

from app.services.agents.onboarding_v2.docx_positions import (
    canonical_paragraphs,
    paragraph_hash,
    paragraph_run_text,
)
from app.services.pdf.renderer import (
    PdfRenderError,
    TemplateVariableError,
    convert_docx_to_pdf,
)

log = logging.getLogger(__name__)

ACTION_REPLACE_SPAN = "replace_span"
ACTION_INSERT_AFTER_LABEL = "insert_after_label"
ACTION_INSERT_EMPTY_CELL = "insert_empty_cell"


class SlotDriftError(RuntimeError):
    """A confirmed slot's anchor paragraph is not the text it was mapped
    against — the template was edited after HR confirmed the mapping.

    We refuse to write a value at an offset we can no longer vouch for: the edit
    may have shifted the blank or removed it entirely, and guessing risks
    splicing a candidate's salary into the middle of a clause. The agent parks
    the run in `blocked_template_drift` so HR re-confirms, rather than shipping a
    corrupted document.

    Deliberately NOT a `PdfRenderError` subclass: that class already means
    "hard render failure, fail the run", and drift is a recoverable
    HR-fixable state. Keeping the hierarchies separate stops an existing broad
    `except PdfRenderError` from silently swallowing it.
    """

    def __init__(self, paragraph_indexes: list[int], *, template_kind: str | None = None):
        self.paragraph_indexes = paragraph_indexes
        self.template_kind = template_kind
        where = ", ".join(str(i) for i in paragraph_indexes[:5])
        detail = (
            f"Template changed since its fields were confirmed "
            f"({len(paragraph_indexes)} field(s) affected, at paragraph(s) {where}). "
            "Re-open the template mapper and confirm the fields again."
        )
        if template_kind:
            detail = f"[{template_kind}] " + detail
        super().__init__(detail)


def _format_value(value: Any) -> str:
    """Render a context value as document text.

    `None` becomes an empty string rather than the literal "None" — a missing
    optional value should read as a blank in the document, not as a Python
    repr in a legally-binding letter.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def _write_paragraph_text(paragraph: Any, new_text: str) -> None:
    """Overwrite a paragraph's text, preserving its first run's formatting.

    Word splits styled text across runs; we write the whole new string into the
    first run and blank the rest. Sub-run formatting on the replaced span
    collapses to the first run's style — the same trade-off (and the same trick)
    the flat-text editor and the legacy mapper already make.
    """
    runs = paragraph.runs
    if not runs:
        paragraph.add_run(new_text)
        return
    runs[0].text = new_text
    for r in runs[1:]:
        r.text = ""


def fill_docx_slots(
    *,
    docx_bytes: bytes,
    slots: list[dict[str, Any]],
    context: dict[str, Any],
    template_kind: str | None = None,
) -> bytes:
    """Splice `context` values into `docx_bytes` at each slot's position.

    Expects only `confirmed` slots — filtering is the caller's job
    (`template_slots.list_slots(confirmed_only=True)`), so that "what will be
    rendered" is decided in one place rather than re-derived here.

    Returns the filled `.docx` bytes; the original is never mutated.
    """
    if not slots:
        # Nothing mapped: hand the document through untouched rather than
        # failing. A template with no fields is odd but not an error, and the
        # post-render unfilled-scan is what flags it to HR.
        return docx_bytes

    # ── Validate every variable up front ───────────────────────────────────
    # Fail before writing anything, mirroring Jinja's StrictUndefined contract:
    # a misspelled placeholder must never reach a legally-binding PDF, and a
    # partially-filled document is worse than none.
    missing = [
        slot["variable"]
        for slot in slots
        if slot.get("variable") and slot["variable"] not in context
    ]
    if missing:
        raise TemplateVariableError(missing[0], template_kind=template_kind)

    unmapped = [slot for slot in slots if not slot.get("variable")]
    if unmapped:
        raise PdfRenderError(
            f"{len(unmapped)} confirmed field(s) have no variable assigned. "
            "Re-open the template mapper and pick a value for each field."
        )

    doc = Document(io.BytesIO(docx_bytes))
    canonical = canonical_paragraphs(doc)

    # ── Drift guard ────────────────────────────────────────────────────────
    # Per-paragraph, not whole-document: HR rewording an unrelated clause must
    # not invalidate the fields elsewhere in the template.
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
        raise SlotDriftError(sorted(set(drifted)), template_kind=template_kind)

    # ── Apply, grouped by paragraph, right-to-left within each ─────────────
    by_paragraph: dict[int, list[dict[str, Any]]] = {}
    for slot in slots:
        by_paragraph.setdefault(slot["paragraph_index"], []).append(slot)

    for para_idx, para_slots in by_paragraph.items():
        paragraph, _kind = canonical[para_idx]
        text = paragraph_run_text(paragraph)

        ordered = sorted(para_slots, key=lambda s: s["start_offset"], reverse=True)
        new_text = text
        last_start = len(text) + 1
        for slot in ordered:
            start = slot["start_offset"]
            end = slot["end_offset"]
            if end > last_start:
                # Overlaps a span already written in this paragraph. Skip rather
                # than corrupt — the anchor unique index makes this unreachable
                # for identical anchors, but merged/edited templates can still
                # produce overlapping ranges.
                log.warning(
                    "slot_renderer.overlapping_slot slot=%s paragraph=%s",
                    slot.get("id"), para_idx,
                )
                continue
            value = _format_value(context.get(slot["variable"]))
            action = slot.get("action") or ACTION_REPLACE_SPAN

            if action == ACTION_INSERT_AFTER_LABEL:
                # Label paragraph ends at `start`; separate the value from the
                # colon with a space so "Signature:" doesn't run into the value.
                sep = "" if new_text[:start].endswith((" ", "\t")) else " "
                new_text = new_text[:start] + sep + value + new_text[end:]
            elif action == ACTION_INSERT_EMPTY_CELL:
                new_text = value
            else:  # ACTION_REPLACE_SPAN
                new_text = new_text[:start] + value + new_text[end:]
            last_start = start

        if new_text != text:
            _write_paragraph_text(paragraph, new_text)

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


async def render_docx_slots_to_pdf(
    *,
    docx_bytes: bytes,
    slots: list[dict[str, Any]],
    context: dict[str, Any],
    template_kind: str | None = None,
) -> tuple[bytes, bytes]:
    """End-to-end slots render: fill by position, then convert to PDF.

    Mirrors `renderer.render_docx_template_to_pdf`'s signature and
    `(filled_docx, pdf_bytes)` return shape so the agent's three generation
    steps can branch on `fill_strategy` with one extra line each — and reuses
    `convert_docx_to_pdf` unchanged, since PDF conversion never cared how the
    `.docx` got filled.
    """
    filled_docx = await asyncio.to_thread(
        fill_docx_slots,
        docx_bytes=docx_bytes,
        slots=slots,
        context=context,
        template_kind=template_kind,
    )
    pdf_bytes = await convert_docx_to_pdf(filled_docx)
    return filled_docx, pdf_bytes
