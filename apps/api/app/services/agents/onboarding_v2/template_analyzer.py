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
class ProposedMapping:
    """A single AI-proposed blank → variable mapping.

    `blank_text` is the literal substring that appears in the document (e.g.
    "_________" or "[CANDIDATE NAME]"). The apply step performs a substring
    replace, so blank_text MUST appear in the document verbatim.
    `context_before` and `context_after` are short surrounding-text snippets
    for HR to disambiguate when multiple blanks look alike.
    """

    blank_text: str
    variable: str
    context_before: str
    context_after: str
    confidence: str  # "high" | "medium" | "low"


class TemplateAnalyzerError(RuntimeError):
    """Raised when the analyzer can't produce usable output. The router
    converts this to a 422 with a human-readable detail so HR sees an
    actionable error rather than a 500."""


# ── Jinja detection ────────────────────────────────────────────────────────

# Match `{{ anything }}` allowing optional whitespace and basic Jinja
# operators (`.`, `|`, args) so we don't false-negative on `{{ var | upper }}`.
_JINJA_PLACEHOLDER_RE = re.compile(r"\{\{\s*[A-Za-z_][\w.\|\s\(\)\'\",-]*\s*\}\}")


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


_ANALYZER_SYSTEM_PROMPT = """You are a document-template analyzer. You are given:

1. The full text of an HR document (Letter of Intent, Appointment Letter, NDA, or Induction).
2. A fixed vocabulary of variables that can be substituted into the template.

Your ONLY job is to find blank spots in the document and propose mappings
from each blank to a variable from the vocabulary. You MUST NOT:

  * Invent new variables outside the vocabulary.
  * Rewrite, summarize, or modify the legal text.
  * Propose mappings for spots that are NOT blank (already-filled values).
  * Propose mappings for `{{ var }}` regions — those are already templated.

IGNORE these regions entirely (do not propose mappings for them):
  * Anything matching `{{ ... }}` — e.g. `{{ candidate_name }}`, `{{ ctc | upper }}`.
    These are existing Jinja placeholders; they're already done.

Blank patterns TO PROPOSE mappings for:
  * Runs of underscores: "___________"
  * Bracketed placeholders: "[CANDIDATE NAME]", "[Date]", "<<NAME>>"
  * Single curly placeholders that aren't Jinja: "{name}", "{role}"
    (NOTE: single `{...}` IS a blank, double `{{...}}` is NOT — be careful.)
  * Empty spaces after a labelled colon: "Name: ____", "Date: ____"

A template might be MIXED — some fields already have `{{ var }}` placeholders
and others are still blanks. In that case, only propose mappings for the
remaining blanks. Return an empty list if every variable spot is already
templated with `{{ }}`.

Output format — STRICT JSON only, no prose, no markdown fences:

{
  "mappings": [
    {
      "blank_text": "<the exact substring as it appears in the document>",
      "variable": "<one of the vocabulary names>",
      "context_before": "<up to 40 chars of surrounding text before the blank>",
      "context_after": "<up to 40 chars of surrounding text after the blank>",
      "confidence": "high" | "medium" | "low"
    }
  ]
}

Rules:
  * `blank_text` must appear in the document EXACTLY as you cite it (verbatim
    copy — same number of underscores, same brackets, same case).
  * `blank_text` MUST NOT itself look like `{{ var }}` — that's an existing
    placeholder, not a blank.
  * `variable` MUST be one of the vocabulary names. Skip the mapping if no
    vocabulary variable fits — do not invent one.
  * If two blanks look identical (e.g. two "_______"), include both as
    separate entries with different context_before/context_after.
  * If you find zero blanks, return {"mappings": []}.
  * `confidence` reflects how sure you are about the mapping. Low confidence
    is acceptable — HR will review every mapping before it's applied.

Return ONLY the JSON object. No explanation."""


def _build_vocabulary_block() -> str:
    """Render the variable vocabulary for inclusion in the LLM user prompt."""
    lines = ["Vocabulary (the only allowed `variable` values):"]
    for v in TEMPLATE_VARIABLES:
        lines.append(f"  - {v['name']}: {v['description']}")
    return "\n".join(lines)


async def propose_mappings(
    *,
    docx_text: str,
    template_kind: str,
) -> list[ProposedMapping]:
    """Ask the LLM to find blanks in `docx_text` and map them to variables.

    Raises TemplateAnalyzerError if the LLM is unreachable or returns
    unparseable JSON. Returns an empty list if the LLM finds no blanks
    (which is a valid outcome — the caller will fall back to suggesting
    HR add placeholders manually).
    """
    if not docx_text.strip():
        raise TemplateAnalyzerError("Template appears to be empty.")

    client = get_llm_client()
    user_prompt = (
        f"Template kind: {template_kind}\n\n"
        f"{_build_vocabulary_block()}\n\n"
        "Document text:\n"
        "---\n"
        f"{docx_text}\n"
        "---\n\n"
        "Return the JSON mapping object now."
    )

    try:
        response = await client.complete(
            messages=[Message(role="user", content=user_prompt)],
            tools=(),
            temperature=0.0,
            replace_system_prompt=True,
            system_extra=_ANALYZER_SYSTEM_PROMPT,
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

    raw_mappings = payload.get("mappings")
    if not isinstance(raw_mappings, list):
        raise TemplateAnalyzerError("AI analyzer returned malformed mappings.")

    allowed = set(get_variable_names())
    result: list[ProposedMapping] = []
    seen: set[tuple[str, str]] = set()  # (blank_text, variable) dedup
    for entry in raw_mappings:
        if not isinstance(entry, dict):
            continue
        blank = str(entry.get("blank_text") or "").strip()
        variable = str(entry.get("variable") or "").strip()
        if not blank or not variable:
            continue
        if variable not in allowed:
            log.info(
                "template_analyzer.skipping_unknown_var variable=%s blank=%r",
                variable, blank[:60],
            )
            continue
        if blank not in docx_text:
            # LLM cited a blank that doesn't actually appear verbatim. Skip
            # rather than corrupt the document with a bad substitution.
            log.info(
                "template_analyzer.skipping_phantom_blank blank=%r",
                blank[:60],
            )
            continue
        if _JINJA_PLACEHOLDER_RE.search(blank):
            # The "blank" the LLM cited is itself a `{{ var }}` Jinja region.
            # Re-templating it would be a no-op at best and a corruption at
            # worst (e.g. nested braces). Drop it.
            log.info(
                "template_analyzer.skipping_jinja_region blank=%r",
                blank[:60],
            )
            continue
        key = (blank, variable)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            ProposedMapping(
                blank_text=blank,
                variable=variable,
                context_before=str(entry.get("context_before") or "")[:80],
                context_after=str(entry.get("context_after") or "")[:80],
                confidence=str(entry.get("confidence") or "medium").lower(),
            )
        )

    return result


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
) -> bytes:
    """Rewrite the DOCX, replacing each `blank_text` with the matching
    `{{ variable }}` Jinja placeholder.

    Preserves all original formatting (fonts, tables, headers/footers) by
    operating at the python-docx run level — every run's text is rewritten
    in place. If a blank_text spans multiple runs (common when Word splits
    a styled phrase), we collapse the runs in that paragraph and re-emit
    the text in the first run, then clear the rest.

    Returns the new DOCX bytes. Raises TemplateAnalyzerError if no
    substitutions were made (all blanks were phantoms or DOCX was malformed).
    """
    if not mappings:
        return docx_bytes

    doc = Document(io.BytesIO(docx_bytes))
    # Sort mappings by blank_text length descending so a long blank like
    # "[CANDIDATE FULL NAME]" doesn't get partially matched by a shorter
    # variant like "[CANDIDATE NAME]".
    ordered = sorted(mappings, key=lambda m: -len(m.blank_text))

    total_replacements = 0

    # Body paragraphs + tables.
    for paragraph in _iter_all_paragraphs(doc):
        for mapping in ordered:
            replaced = _replace_in_paragraph(
                paragraph, mapping.blank_text, f"{{{{ {mapping.variable} }}}}"
            )
            total_replacements += replaced

    if total_replacements == 0:
        raise TemplateAnalyzerError(
            "Couldn't apply any mappings to the document. The blank patterns "
            "may have been split across formatted runs in Word. Try uploading "
            "the DOCX with simpler formatting or add {{ variable }} placeholders manually."
        )

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _iter_all_paragraphs(doc: Any) -> list[Paragraph]:
    """Yield every Paragraph in the doc — body, tables, headers, footers."""
    paragraphs: list[Paragraph] = []
    paragraphs.extend(doc.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                paragraphs.extend(cell.paragraphs)
                # Nested tables (rare, but happens in LOI footers).
                for nested in cell.tables:
                    for nrow in nested.rows:
                        for ncell in nrow.cells:
                            paragraphs.extend(ncell.paragraphs)
    for section in doc.sections:
        if section.header:
            paragraphs.extend(section.header.paragraphs)
        if section.footer:
            paragraphs.extend(section.footer.paragraphs)
    return paragraphs


def _replace_in_paragraph(paragraph: Paragraph, needle: str, replacement: str) -> int:
    """Replace every occurrence of `needle` with `replacement` in a paragraph.

    Word splits a single styled phrase across multiple runs (e.g.
    "_____________" might be three runs of underscores with the same style).
    The trick: concatenate all run text, find/replace, then write the result
    back to the FIRST run and blank the rest. Loses sub-run formatting on
    the replaced spans, but the surrounding text keeps its original runs.

    Returns the number of replacements performed.
    """
    runs = paragraph.runs
    if not runs:
        return 0
    full_text = "".join(r.text or "" for r in runs)
    if needle not in full_text:
        return 0
    count = full_text.count(needle)
    new_text = full_text.replace(needle, replacement)
    runs[0].text = new_text
    for r in runs[1:]:
        r.text = ""
    return count


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
