"""AI-assisted DOCX template analyzer.

HR teams almost never upload DOCX templates with Jinja `{{ }}` placeholders
already in them — they have a Word document with blank spaces (underscores,
bracketed labels, empty table cells) that they used to fill in by hand. This
analyzer bridges the gap:

  1. Detect whether a DOCX already has Jinja placeholders (`{{ var }}`).
     If yes, nothing to do — caller falls through to the existing pipeline.

  2. If not, extract the document text and ask the LLM to identify blank
     spots and map each one to a known variable from `template_vars.py`.
     The LLM never touches legal language — it only proposes mappings of
     the form `(blank_pattern, variable_name)` over an extracted text view.

  3. HR reviews and confirms (in the UI). Confirmed mappings are then
     applied to the DOCX: each blank pattern is replaced with the
     corresponding `{{ variable }}` placeholder, preserving all original
     formatting (runs, fonts, tables, headers). The rewritten DOCX is the
     new template — every future run uses the standard docxtpl pipeline.

  4. Before persisting, the rewritten DOCX is dry-rendered with sample
     data to confirm it produces a valid PDF-ready result.

Why this design:

  * The LLM is used ONCE per template (at conversion time), not per run.
    Every actual document generation stays deterministic and AI-free.
  * The LLM only sees text + a fixed variable vocabulary. It cannot
    invent variables or rewrite clauses.
  * HR confirms mappings before they're applied — they see exactly what
    the AI proposes and can edit any wrong guesses.
"""
from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from docx import Document  # python-docx
from docx.text.paragraph import Paragraph

from app.services.llm.client import LLMError, get_llm_client
from app.services.llm.types import Message
from app.services.pdf import (
    PdfRenderError,
    PdfRenderUnavailable,
    TemplateVariableError,
    fill_docx_template,
)

from .template_vars import (
    PREVIEW_SAMPLE_CONTEXT,
    TEMPLATE_VARIABLES,
    get_variable_names,
)

log = logging.getLogger(__name__)


# ── Public types ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BlankCandidate:
    """A blank fill-spot located deterministically by exact character offset.

    Detection never asks the LLM to reproduce document text — these offsets
    are the single source of truth for both the classification prompt and the
    later substitution. So nothing can drift, and two identical-looking blanks
    (e.g. two `________` signature lines) are always distinguished by
    `paragraph_index` + `start_offset`, never by their (identical) text.
    """

    candidate_id: int
    paragraph_index: int  # index into _canonical_paragraphs()
    start_offset: int     # char offset into the paragraph's run text
    end_offset: int
    matched_text: str     # exact substring — regex-extracted, never LLM-typed
    context_before: str
    context_after: str
    paragraph_kind: str   # "body" | "table" | "header" | "footer"


@dataclass(frozen=True)
class CandidateClassification:
    """The LLM's decision for one candidate: which variable fills it, or None
    to skip it (no vocabulary variable fits)."""

    candidate_id: int
    variable: str | None
    confidence: str  # "high" | "medium" | "low"


@dataclass(frozen=True)
class ProposedMapping:
    """A single blank → variable mapping ready to apply.

    Carries the blank's exact position (`paragraph_index` +
    `start_offset`/`end_offset` into the canonical paragraph enumeration) so
    the apply step substitutes by position — never by re-finding `blank_text`
    as a substring, which is what let identical blanks collide before.
    `blank_text` is retained for HR-readability in the UI and as a drift
    safety-check at apply time (the text at that offset must still equal it).
    """

    blank_text: str
    variable: str
    context_before: str
    context_after: str
    confidence: str  # "high" | "medium" | "low"
    paragraph_index: int
    start_offset: int
    end_offset: int


@dataclass(frozen=True)
class SkippedMapping:
    """A mapping that couldn't be safely applied, with a machine-readable
    reason for logging. A partial skip is non-fatal — only an all-skipped
    apply raises."""

    mapping: ProposedMapping
    reason: str


class TemplateAnalyzerError(RuntimeError):
    """Raised when the analyzer can't produce usable output. The router
    converts this to a 422 with a human-readable detail so HR sees an
    actionable error rather than a 500."""


# ── Jinja detection ────────────────────────────────────────────────────────

# Match `{{ anything }}` allowing optional whitespace and basic Jinja
# operators (`.`, `|`, args) so we don't false-negative on `{{ var | upper }}`.
_JINJA_PLACEHOLDER_RE = re.compile(r"\{\{\s*[A-Za-z_][\w.\|\s\(\)\'\",-]*\s*\}\}")

# Any existing `{{ ... }}` span. Used both to reject blank candidates that
# fall inside a placeholder and to keep those spans verbatim during
# substitution.
_JINJA_SPAN_RE = re.compile(r"\{\{.*?\}\}", re.DOTALL)

# A blank fill-spot as it appears in a not-yet-templated HR document. Unlike
# the old marker *validator*, this matches the blank as a SPAN so we get its
# exact character offsets. Ordered alternation: underscore runs, bracketed
# labels, angle-bracket labels, dotted leaders / ellipsis, and single-curly
# `{label}` (the lookarounds exclude `{{ }}` Jinja, handled separately).
_BLANK_SPAN_RE = re.compile(
    r"_{3,}"                            # ________
    r"|\[[^\[\]{}\n]{1,60}\]"           # [CANDIDATE NAME]
    r"|<[^<>{}\n]{1,60}>"               # <NAME>
    r"|\.{3,}"                          # dotted leader ......
    r"|…"                               # unicode ellipsis
    r"|(?<!\{)\{[^{}\n]{1,60}\}(?!\})"  # {name} but never {{ jinja }}
)

# How much surrounding text to capture on each side of a blank — enough for
# the classifier (and HR) to read the label ("Name:", "Date:") without
# bloating the prompt.
_CONTEXT_CHARS = 60


def _paragraph_run_text(paragraph: Paragraph) -> str:
    """Concatenate a paragraph's run text — the exact string the apply step
    slices by offset.

    We deliberately join `paragraph.runs` rather than read `Paragraph.text`:
    in python-docx 1.x the latter also pulls in hyperlink text, which would
    make detection-time offsets disagree with the run text rewritten at apply
    time. Joining runs keeps both sides byte-for-byte identical.
    """
    return "".join(r.text or "" for r in paragraph.runs)


def has_jinja_placeholders(docx_bytes: bytes) -> bool:
    """Return True if the DOCX already contains `{{ var }}` placeholders.

    Reads paragraphs *and* table cells — placeholders inside tables are easy
    to miss with a naive XML grep."""
    text = extract_text(docx_bytes)
    return bool(_JINJA_PLACEHOLDER_RE.search(text))


# ── Text extraction ────────────────────────────────────────────────────────


def extract_text(docx_bytes: bytes) -> str:
    """Extract plain text from a DOCX, preserving paragraph + table order.

    Used to (a) detect existing Jinja placeholders and (b) feed the LLM a
    flat view of the document. Header/footer text is included since LOI/AL
    templates often put the candidate name in a letterhead block.
    """
    doc = Document(io.BytesIO(docx_bytes))
    lines: list[str] = []

    def _add(text: str) -> None:
        if text and text.strip():
            lines.append(text)

    for section in doc.sections:
        if section.header:
            for p in section.header.paragraphs:
                _add(p.text)
        if section.footer:
            for p in section.footer.paragraphs:
                _add(p.text)

    for block in _iter_body_blocks(doc):
        _add(block)

    return "\n".join(lines)


def _iter_body_blocks(doc: Any) -> list[str]:
    """Walk the body in document order so paragraphs and tables interleave
    correctly. python-docx exposes them as separate lists by default — we
    inspect the underlying XML order to get the linear sequence."""
    from docx.oxml.ns import qn

    out: list[str] = []
    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            # Locate the matching Paragraph object so we get its full text
            # (including style/run merging) instead of re-doing it from XML.
            para = Paragraph(child, doc)
            out.append(para.text)
        elif child.tag == qn("w:tbl"):
            for row in child.iter(qn("w:tr")):
                cells_text: list[str] = []
                for cell in row.iter(qn("w:tc")):
                    cell_text = " ".join(
                        p.text for p in (Paragraph(p_elem, doc) for p_elem in cell.iter(qn("w:p")))
                    ).strip()
                    if cell_text:
                        cells_text.append(cell_text)
                if cells_text:
                    out.append(" | ".join(cells_text))
    return out


# ── LLM mapping proposal ───────────────────────────────────────────────────


_CLASSIFIER_SYSTEM_PROMPT = """You are a document-template analyzer for HR documents (Letter of Intent, Appointment Letter, NDA, or Induction).

You are given:
1. A fixed vocabulary of variables that can be substituted into the template.
2. A numbered list of BLANK CANDIDATES already located in the document. Each
   candidate is a real blank fill-spot (underscores, a bracketed label, etc.)
   shown with its surrounding text; the blank itself is wrapped in ⟦ ⟧ markers.

Your ONLY job: for EVERY candidate, decide which vocabulary variable belongs in
that blank — or that none does. You do NOT locate blanks (that is already done)
and you do NOT reproduce or rewrite any document text.

Rules:
  * Pick the `variable` from the vocabulary that should fill the blank, judging
    from the surrounding text — labels like "Name:", "Date:", "CTC:", a
    signature line, the governing-law clause, etc.
  * If no vocabulary variable fits a blank, set `variable` to null. Do NOT
    invent a variable outside the vocabulary.
  * Return EXACTLY one entry for every candidate_id you were given — never omit
    one and never add ids that weren't given.
  * `confidence` is how sure you are: "high" | "medium" | "low". Low is fine —
    HR reviews every mapping before it is applied.

Output format — STRICT JSON only, no prose, no markdown fences:

{
  "classifications": [
    { "candidate_id": <int>, "variable": "<vocabulary name or null>", "confidence": "high" | "medium" | "low" }
  ]
}

Return ONLY the JSON object. No explanation."""


def _build_vocabulary_block() -> str:
    """Render the variable vocabulary for inclusion in the LLM user prompt."""
    lines = ["Vocabulary (the only allowed `variable` values):"]
    for v in TEMPLATE_VARIABLES:
        lines.append(f"  - {v['name']}: {v['description']}")
    return "\n".join(lines)


def find_blank_candidates(docx_bytes: bytes) -> list[BlankCandidate]:
    """Deterministically locate every blank fill-spot in the document, by
    exact character offset within each canonical paragraph.

    This replaces the old "let the LLM cite blank_text, then re-find it as a
    substring" step. Because the offsets come straight from a regex over the
    document (never re-typed by anything), the apply step can substitute by
    position — so nothing can drift out of the document, and identical-looking
    blanks are inherently distinguished by their `(paragraph_index, offset)`.

    Candidates overlapping an existing `{{ ... }}` span are dropped so we
    never re-template an already-templated field.
    """
    doc = Document(io.BytesIO(docx_bytes))
    candidates: list[BlankCandidate] = []
    cid = 0
    for idx, (paragraph, kind) in enumerate(_canonical_paragraphs(doc)):
        text = _paragraph_run_text(paragraph)
        if not text:
            continue
        jinja_spans = [(m.start(), m.end()) for m in _JINJA_SPAN_RE.finditer(text)]
        for m in _BLANK_SPAN_RE.finditer(text):
            start, end = m.start(), m.end()
            if any(s < end and start < e for s, e in jinja_spans):
                continue  # inside/overlapping an existing {{ }} — not a blank
            candidates.append(
                BlankCandidate(
                    candidate_id=cid,
                    paragraph_index=idx,
                    start_offset=start,
                    end_offset=end,
                    matched_text=m.group(0),
                    context_before=text[max(0, start - _CONTEXT_CHARS):start],
                    context_after=text[end:end + _CONTEXT_CHARS],
                    paragraph_kind=kind,
                )
            )
            cid += 1
    return candidates


def _build_candidate_block(candidates: list[BlankCandidate]) -> str:
    """Render the located candidates for the classification prompt — one line
    each, with the blank wrapped in ⟦ ⟧ inside its surrounding text."""
    lines = ["Blank candidates (classify EVERY one, by candidate_id):"]
    for c in candidates:
        snippet = f"{c.context_before}⟦{c.matched_text}⟧{c.context_after}"
        snippet = " ".join(snippet.split())  # collapse newlines/runs of spaces
        lines.append(f'  #{c.candidate_id} [{c.paragraph_kind}]: "{snippet}"')
    return "\n".join(lines)


async def classify_candidates(
    *,
    candidates: list[BlankCandidate],
    template_kind: str,
) -> list[CandidateClassification]:
    """Ask the LLM to assign a vocabulary variable (or null=skip) to each
    pre-located blank.

    This is a constrained classification task: the model only picks a variable
    name per `candidate_id` and never reproduces document text, so there is no
    verbatim-echo step that can drift. Raises TemplateAnalyzerError if the LLM
    is unreachable or returns unparseable JSON. Candidates the model omits or
    marks null are dropped by `merge_classifications`.
    """
    if not candidates:
        return []

    client = get_llm_client()
    user_prompt = (
        f"Template kind: {template_kind}\n\n"
        f"{_build_vocabulary_block()}\n\n"
        f"{_build_candidate_block(candidates)}\n\n"
        "Return the JSON classifications object now."
    )

    try:
        response = await client.complete(
            messages=[Message(role="user", content=user_prompt)],
            tools=(),
            temperature=0.0,
            replace_system_prompt=True,
            system_extra=_CLASSIFIER_SYSTEM_PROMPT,
        )
    except LLMError as exc:
        raise TemplateAnalyzerError(
            f"AI analyzer unavailable: {exc}. Try again or add {{{{ variable }}}} placeholders manually."
        ) from exc

    raw = (response.text or "").strip()
    if not raw:
        raise TemplateAnalyzerError("AI analyzer returned no output. Try again.")

    payload = _extract_json_object(raw)
    if payload is None:
        log.warning("template_analyzer.unparseable_response raw=%r", raw[:500])
        raise TemplateAnalyzerError(
            "AI analyzer returned unparseable output. Try again or add placeholders manually."
        )

    raw_items = payload.get("classifications")
    if not isinstance(raw_items, list):
        raise TemplateAnalyzerError("AI analyzer returned malformed classifications.")

    known_ids = {c.candidate_id for c in candidates}
    allowed = set(get_variable_names())
    result: list[CandidateClassification] = []
    seen: set[int] = set()
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        try:
            cid = int(entry.get("candidate_id"))
        except (TypeError, ValueError):
            continue
        if cid not in known_ids or cid in seen:
            continue
        var_raw = entry.get("variable")
        variable = str(var_raw).strip() if var_raw not in (None, "") else None
        if variable is not None and variable not in allowed:
            # Model named a variable outside the vocabulary — treat as skip
            # rather than invent one.
            log.info(
                "template_analyzer.skipping_unknown_var candidate_id=%s variable=%s",
                cid, variable,
            )
            variable = None
        confidence = str(entry.get("confidence") or "medium").lower()
        if confidence not in ("high", "medium", "low"):
            confidence = "medium"
        seen.add(cid)
        result.append(
            CandidateClassification(candidate_id=cid, variable=variable, confidence=confidence)
        )

    return result


def merge_classifications(
    candidates: list[BlankCandidate],
    classifications: list[CandidateClassification],
) -> list[ProposedMapping]:
    """Join candidates (position) with classifications (variable) into the
    final proposal list.

    Candidates the LLM mapped to null — or never classified — are dropped. No
    dedup is needed: every candidate is already unique by position.
    """
    by_id = {c.candidate_id: c for c in classifications}
    out: list[ProposedMapping] = []
    for cand in candidates:
        cls = by_id.get(cand.candidate_id)
        if cls is None or cls.variable is None:
            continue
        out.append(
            ProposedMapping(
                blank_text=cand.matched_text,
                variable=cls.variable,
                context_before=cand.context_before,
                context_after=cand.context_after,
                confidence=cls.confidence,
                paragraph_index=cand.paragraph_index,
                start_offset=cand.start_offset,
                end_offset=cand.end_offset,
            )
        )
    return out


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """LLMs occasionally wrap JSON in ``` fences despite being told not to.
    Tolerate both bare-JSON and fenced output. Returns None if nothing
    valid parses."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Strip ```json ... ``` or ``` ... ```
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        # Last-ditch: extract the first {...} block.
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


# ── Apply mappings to DOCX ─────────────────────────────────────────────────


def apply_mappings(
    *,
    docx_bytes: bytes,
    mappings: list[ProposedMapping],
) -> tuple[bytes, list[SkippedMapping]]:
    """Rewrite the DOCX, replacing each mapping's blank span with a
    `{{ variable }}` placeholder — located by exact position, not by
    re-finding `blank_text` as a substring.

    Substitution happens per canonical paragraph, right-to-left within a
    paragraph so replacing one span never shifts a not-yet-applied offset.
    Each span's current text is verified against `blank_text` first; a
    mismatch (e.g. the paragraph was edited via the block editor since it was
    analysed) skips ONLY that one mapping instead of corrupting the document.
    Formatting is preserved the same way as elsewhere in this module — the
    rewritten text collapses into the paragraph's first run and the rest are
    blanked.

    Returns the new DOCX bytes and the list of skipped mappings (each with a
    reason). Raises TemplateAnalyzerError only if EVERY mapping was skipped.
    """
    if not mappings:
        return docx_bytes, []

    doc = Document(io.BytesIO(docx_bytes))
    canonical = _canonical_paragraphs(doc)
    skipped: list[SkippedMapping] = []

    by_paragraph: dict[int, list[ProposedMapping]] = {}
    for m in mappings:
        by_paragraph.setdefault(m.paragraph_index, []).append(m)

    total_replacements = 0
    for para_idx, para_mappings in by_paragraph.items():
        if para_idx < 0 or para_idx >= len(canonical):
            skipped.extend(
                SkippedMapping(m, "paragraph_index_out_of_range") for m in para_mappings
            )
            continue
        paragraph, _kind = canonical[para_idx]
        runs = paragraph.runs
        full_text = "".join(r.text or "" for r in runs)
        jinja_spans = [(j.start(), j.end()) for j in _JINJA_SPAN_RE.finditer(full_text)]

        valid: list[ProposedMapping] = []
        for m in para_mappings:
            if (
                not runs
                or m.start_offset < 0
                or m.end_offset > len(full_text)
                or m.start_offset >= m.end_offset
            ):
                skipped.append(SkippedMapping(m, "offset_out_of_range"))
                continue
            if full_text[m.start_offset:m.end_offset] != m.blank_text:
                # Text at that span changed since analysis — refuse to guess.
                skipped.append(SkippedMapping(m, "text_drift"))
                continue
            if any(s < m.end_offset and m.start_offset < e for s, e in jinja_spans):
                skipped.append(SkippedMapping(m, "overlaps_existing_placeholder"))
                continue
            valid.append(m)

        # Right-to-left so accepted spans keep their offsets valid as we
        # rewrite, and drop any span that overlaps one already accepted here.
        valid.sort(key=lambda m: m.start_offset, reverse=True)
        new_text = full_text
        last_start = len(full_text) + 1
        applied_here = 0
        for m in valid:
            if m.end_offset > last_start:
                skipped.append(SkippedMapping(m, "overlapping_offset"))
                continue
            new_text = (
                new_text[: m.start_offset]
                + f"{{{{ {m.variable} }}}}"
                + new_text[m.end_offset :]
            )
            last_start = m.start_offset
            applied_here += 1
            total_replacements += 1

        if applied_here:
            runs[0].text = new_text
            for r in runs[1:]:
                r.text = ""

    if total_replacements == 0:
        raise TemplateAnalyzerError(
            "None of the proposed mappings matched the current document text. "
            "The document may have changed since it was last analysed — click "
            "Find blanks again to re-detect them."
        )

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue(), skipped


# ── Flat-text editing (paragraph-in-place) ─────────────────────────────────
#
# HR reviews a template as flat, editable text — one field per paragraph /
# table cell / header / footer line. When they save, we match each edited
# line back to its original DOCX paragraph *by position* and rewrite the run
# text in place. This preserves fonts, tables, letterheads, and existing
# `{{ placeholders }}`; HR can reword freely but can't add/remove/reorder
# paragraphs inline (that still goes through Download → edit → Replace .docx).


@dataclass(frozen=True)
class EditableBlock:
    """One editable line of a template.

    `index` is the position in the canonical paragraph enumeration — it is the
    stable key the write-back step uses to locate the paragraph again, so the
    SAME enumeration must drive both extraction and apply. `kind` is a display
    hint ("body" | "table" | "header" | "footer").
    """

    index: int
    text: str
    kind: str


def _canonical_paragraphs(doc: Any) -> list[tuple[Paragraph, str]]:
    """Enumerate every paragraph in a stable, document-first order.

    Body paragraphs and table cells are walked in true document order (so a
    table's cells appear where the table sits), followed by header and footer
    paragraphs. This ordering is an implementation detail — the only contract
    is that it is *deterministic* and *identical* between extraction and
    write-back, so a block's `index` always resolves to the same paragraph.
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


def extract_editable_blocks(docx_bytes: bytes) -> list[EditableBlock]:
    """Return the non-empty paragraphs of a DOCX as editable blocks.

    Blank/spacing paragraphs are skipped from the editable list but keep their
    slot in the canonical enumeration, so every block's `index` still points
    at the right paragraph on write-back.
    """
    doc = Document(io.BytesIO(docx_bytes))
    blocks: list[EditableBlock] = []
    for idx, (para, kind) in enumerate(_canonical_paragraphs(doc)):
        text = para.text
        if text and text.strip():
            blocks.append(EditableBlock(index=idx, text=text, kind=kind))
    return blocks


def _set_paragraph_text(paragraph: Paragraph, new_text: str) -> None:
    """Overwrite a paragraph's text, preserving its first run's formatting.

    Word splits styled text across runs; we write the whole new string into
    the first run and blank the rest (same run-collapse trick `apply_mappings`
    uses). Sub-run formatting on the replaced span collapses to the first
    run's style — acceptable for paragraph-level editing.
    """
    runs = paragraph.runs
    if not runs:
        # No runs to inherit style from (shouldn't happen for a non-empty
        # block, but guard anyway) — add a plain run.
        paragraph.add_run(new_text)
        return
    runs[0].text = new_text
    for r in runs[1:]:
        r.text = ""


def apply_text_edits(*, docx_bytes: bytes, edits: dict[int, str]) -> tuple[bytes, int]:
    """Rewrite paragraphs at the given canonical indices with new text.

    `edits` maps a block `index` (from `extract_editable_blocks`) to its new
    text. Indices out of range are ignored; paragraphs whose text is unchanged
    are left untouched. Returns the new DOCX bytes and the number of
    paragraphs actually changed.
    """
    doc = Document(io.BytesIO(docx_bytes))
    paras = _canonical_paragraphs(doc)
    changed = 0
    for idx, new_text in edits.items():
        if idx < 0 or idx >= len(paras):
            continue
        para, _kind = paras[idx]
        if para.text == new_text:
            continue
        _set_paragraph_text(para, new_text)
        changed += 1

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue(), changed


# ── Validation ─────────────────────────────────────────────────────────────


async def validate_rendered(docx_bytes: bytes, *, template_kind: str | None = None) -> None:
    """Dry-render the (presumably rewritten) DOCX with sample context to
    catch broken templates before we persist them. Raises
    TemplateAnalyzerError on render failure with the underlying message."""
    try:
        await fill_docx_template(
            template_bytes=docx_bytes,
            context=PREVIEW_SAMPLE_CONTEXT,
            strict=True,
            template_kind=template_kind,
        )
    except TemplateVariableError as exc:
        raise TemplateAnalyzerError(
            f"After applying mappings, the template still references an unknown "
            f"variable: '{exc.variable_name}'. Edit the DOCX to remove it or pick "
            "a different variable in the mapping step."
        ) from exc
    except (PdfRenderError, PdfRenderUnavailable) as exc:
        raise TemplateAnalyzerError(
            f"Couldn't render the rewritten template: {exc}"
        ) from exc
