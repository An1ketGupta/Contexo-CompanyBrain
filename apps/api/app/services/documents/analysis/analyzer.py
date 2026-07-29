"""The LLM stage of document understanding.

Given a document and the fill-points `detector.py` already located, this module
asks a model four questions:

  1. What kind of document is this?
  2. What should each detected fill-point be called, and what type is it?
  3. Which of them are required?
  4. Are there values a pattern could never find — an unlabelled name in
     `Dear Rahul,` — and if so, what is the exact text?

What it never does is write document text or author a position. For (4) the
model returns a *substring* and a paragraph index; this module then locates that
substring with `str.find` against the real paragraph and **drops the proposal
outright if it is not present verbatim**. A model that hallucinates a value
produces nothing, not a slot pointing at the wrong characters.

Differences from the analyzer this replaces
-------------------------------------------
  * **Open vocabulary.** The old classifier was handed a fixed list of 20
    variable names and instructed not to invent others; anything outside it was
    silently dropped (`template_analyzer.py:277-284`). Half the fields in a
    normal appointment letter — employee ID, notice period, work mode,
    permanent address — were therefore undetectable by construction. The model
    now proposes names, and the *mapping* layer decides where values come from.

  * **Numeric confidence.** `high|medium|low` cannot express a threshold. A
    float can, so an org can move the auto-confirm line without a migration.

  * **Document type is an output, not an input.** The old code was told the
    `template_kind` and never questioned it.

  * **Types and requiredness.** A variable is now a typed thing the validation
    engine can check, not just a name.
"""
from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from docx import Document

from app.services.documents.analysis.detector import (
    KIND_LLM_LITERAL,
    FillCandidate,
)
from app.services.documents.constants import (
    ACTION_REPLACE_SPAN,
    DATA_TYPE_TEXT,
    DATA_TYPES,
)
from app.services.documents.docx_positions import (
    canonical_paragraphs,
    paragraph_run_text,
)
from app.services.llm.client import LLMError, get_llm_client
from app.services.llm.types import Message

log = logging.getLogger(__name__)

# `doc_template_variables.internal_name` has a CHECK for this exact shape.
# Enforced here so a malformed model output becomes a usable name rather than a
# constraint violation three layers down.
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# Ceilings on what goes into the prompt. A mis-uploaded 80-page policy manual
# must degrade to a truncated analysis, not a five-figure token bill.
_MAX_PROMPT_PARAGRAPHS = 220
_MAX_PARAGRAPH_CHARS = 300
_MAX_CANDIDATES_IN_PROMPT = 250


class TemplateAnalysisError(RuntimeError):
    """The analyzer could not produce usable output.

    The router turns this into a 422 with a readable message, and the template
    stays in `analysis_status='failed'` — which is a fully supported state: HR
    can define every variable by hand from there. A failed analysis must never
    be a dead end.
    """


@dataclass(frozen=True)
class AnalyzedVariable:
    """A proposed field definition."""

    internal_name: str
    display_name: str
    description: str = ""
    data_type: str = DATA_TYPE_TEXT
    is_required: bool = True
    example_value: str = ""
    confidence: float | None = None


@dataclass(frozen=True)
class AnalyzedSlot:
    """A fill-point bound to a variable.

    `candidate_id` is None for a slot discovered by the literal pass, which has
    no pre-located candidate behind it — its anchor is carried directly.
    """

    paragraph_index: int
    start_offset: int
    end_offset: int
    original_text: str
    context_before: str
    context_after: str
    paragraph_kind: str
    action: str
    detection_kind: str
    internal_name: str | None
    confidence: float | None
    candidate_id: int | None = None


@dataclass
class TemplateAnalysis:
    """Everything one analysis pass concluded."""

    document_type_key: str | None = None
    document_type_confidence: float | None = None
    variables: list[AnalyzedVariable] = field(default_factory=list)
    slots: list[AnalyzedSlot] = field(default_factory=list)
    # Proposals dropped because the quoted text was not present verbatim. Kept
    # for observability: a rising count here means the prompt or model changed
    # and literal detection is silently degrading.
    rejected_literals: list[str] = field(default_factory=list)
    truncated: bool = False


# ── Prompt construction ────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You analyse business and legal document templates (offer letters, NDAs, employment agreements, and similar).

You are given:
1. A list of ALLOWED document type keys.
2. A list of ALLOWED data types.
3. The document, as numbered paragraphs.
4. A numbered list of FILL-POINTS already located in that document. Each shows
   its surrounding text with the fill-point itself wrapped in ⟦ ⟧, plus the
   label detected next to it. Some fill-points are empty (a blank to complete);
   others already contain a real value from a sample, which a generated
   document will overwrite.

Your job:
  A. Identify the document type.
  B. Define a VARIABLE for every distinct piece of information that changes
     from one person to the next. Give each a snake_case internal_name, a
     human display name, a data type, and whether it is required.
  C. Assign every fill-point to one of your variables. Two fill-points holding
     the same information (a name in the header and again in the signature
     block) MUST share one variable.
  D. Find values that carry candidate-specific information but were NOT already
     located — most often an unlabelled name, as in "Dear Rahul,". Report each
     as the EXACT substring, copied character-for-character from the paragraph
     it appears in, together with that paragraph's number.

Critical rules:
  * NEVER rewrite, reword, summarise, or reformat any document text. You are
    reading it, not editing it.
  * Text that is the same for every person — company name in boilerplate, legal
    clauses, policy wording, headings — is NOT a variable. Do not report it.
  * For part D, the "text" field must appear VERBATIM in the paragraph you name.
    If you are not certain of the exact characters, omit it entirely. A missing
    proposal is fine; an inexact one is discarded anyway.
  * confidence is a number between 0 and 1.
  * internal_name must match ^[a-z][a-z0-9_]*$.

Output STRICT JSON only, no prose and no markdown fences:

{
  "document_type": "<one of the allowed keys, or null>",
  "document_type_confidence": <0..1>,
  "variables": [
    {
      "internal_name": "candidate_name",
      "display_name": "Candidate Name",
      "description": "Full name of the person being onboarded.",
      "data_type": "text",
      "required": true,
      "example_value": "Rahul"
    }
  ],
  "assignments": [
    { "fill_point_id": <int>, "internal_name": "candidate_name", "confidence": 0.97 }
  ],
  "literals": [
    { "paragraph": <int>, "text": "Rahul", "internal_name": "candidate_name", "confidence": 0.95 }
  ]
}

Return ONLY the JSON object."""


def _build_paragraph_block(
    paragraphs: list[tuple[int, str]],
) -> tuple[str, bool]:
    """Render numbered paragraphs for the literal pass. Returns (text, truncated)."""
    lines = ["Document paragraphs:"]
    truncated = False
    shown = 0
    for idx, text in paragraphs:
        if not text.strip():
            continue
        if shown >= _MAX_PROMPT_PARAGRAPHS:
            truncated = True
            break
        body = text.strip()
        if len(body) > _MAX_PARAGRAPH_CHARS:
            body = body[:_MAX_PARAGRAPH_CHARS] + "…"
            truncated = True
        lines.append(f"  [{idx}] {body}")
        shown += 1
    return "\n".join(lines), truncated


def _build_candidate_block(candidates: list[FillCandidate]) -> tuple[str, bool]:
    """Render located fill-points, one line each, with the span in ⟦ ⟧."""
    lines = [
        "Fill-points (assign EVERY one, by fill_point_id):",
    ]
    truncated = len(candidates) > _MAX_CANDIDATES_IN_PROMPT
    for c in candidates[:_MAX_CANDIDATES_IN_PROMPT]:
        snippet = f"{c.context_before}⟦{c.original_text}⟧{c.context_after}"
        snippet = " ".join(snippet.split())
        state = "holds-a-value" if c.is_occupied else "empty"
        label = f' label="{c.label_hint}"' if c.label_hint else ""
        lines.append(
            f'  #{c.candidate_id} [{c.paragraph_kind}/{state}]{label}: "{snippet}"'
        )
    return "\n".join(lines), truncated


# ── Response parsing ───────────────────────────────────────────────────────


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Tolerate fenced or bare JSON. Returns None if nothing valid parses."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _slugify_name(raw: Any) -> str | None:
    """Coerce a proposed name into the shape the DB CHECK requires.

    Returns None when nothing usable survives, which the caller treats as "the
    model did not name this field" rather than inventing a placeholder name.
    """
    if not isinstance(raw, str):
        return None
    name = raw.strip().lower()
    name = re.sub(r"[^a-z0-9]+", "_", name).strip("_")
    if not name:
        return None
    if name[0].isdigit():
        name = f"f_{name}"
    return name if _NAME_RE.match(name) else None


def _clamp_confidence(raw: Any) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    # Tolerate models that answer in percent.
    if value > 1.0:
        value = value / 100.0
    return max(0.0, min(1.0, value))


def _coerce_data_type(raw: Any) -> str:
    if isinstance(raw, str) and raw.strip().lower() in DATA_TYPES:
        return raw.strip().lower()
    return DATA_TYPE_TEXT


# ── Literal location ───────────────────────────────────────────────────────


def _locate_literal(
    *,
    text: str,
    quoted: str,
    taken: list[tuple[int, int]],
) -> tuple[int, int] | None:
    """Find `quoted` inside `text`, avoiding spans already claimed.

    This is the anti-hallucination boundary. The model supplies the substring;
    the offsets come from here or the proposal is discarded. A near-miss —
    different whitespace, a smart quote, a reworded fragment — finds nothing and
    is dropped, which is exactly the desired behaviour for a legal document.
    """
    if not quoted:
        return None

    start = text.find(quoted)
    while start >= 0:
        end = start + len(quoted)
        if not any(s < end and start < e for s, e in taken):
            return start, end
        start = text.find(quoted, start + 1)
    return None


# ── Entry point ────────────────────────────────────────────────────────────


async def analyze_template(
    *,
    docx_bytes: bytes,
    candidates: list[FillCandidate],
    allowed_type_keys: list[str],
) -> TemplateAnalysis:
    """Run one batched analysis pass over a template.

    One LLM call per template — never per generated document. The model sees the
    document text and the pre-located fill-points; it returns type, variables,
    assignments, and literal proposals, all of which are validated here before
    any of it becomes a database row.

    Raises `TemplateAnalysisError` when the model is unreachable or returns
    nothing parseable. Callers should record `analysis_status='failed'` and let
    HR define variables manually — a failed analysis is a supported state, not
    a dead end.
    """
    doc = Document(io.BytesIO(docx_bytes))
    canonical = canonical_paragraphs(doc)
    para_texts = [paragraph_run_text(p) for p, _kind in canonical]

    paragraph_block, para_truncated = _build_paragraph_block(list(enumerate(para_texts)))
    candidate_block, cand_truncated = _build_candidate_block(candidates)

    user_prompt = (
        f"Allowed document type keys: {', '.join(allowed_type_keys) or '(none)'}\n"
        f"Allowed data types: {', '.join(DATA_TYPES)}\n\n"
        f"{paragraph_block}\n\n"
        f"{candidate_block}\n\n"
        "Return the JSON object now."
    )

    client = get_llm_client()
    try:
        response = await client.complete(
            messages=[Message(role="user", content=user_prompt)],
            tools=(),
            temperature=0.0,
            replace_system_prompt=True,
            system_extra=_SYSTEM_PROMPT,
        )
    except LLMError as exc:
        raise TemplateAnalysisError(
            f"Document analysis is unavailable right now ({exc}). "
            "You can define the fields manually and try analysis again later."
        ) from exc

    payload = _extract_json_object(response.text or "")
    if payload is None:
        log.warning(
            "documents.analysis_unparseable raw=%r", (response.text or "")[:500]
        )
        raise TemplateAnalysisError(
            "Document analysis returned output we couldn't read. "
            "You can define the fields manually and try analysis again later."
        )

    return _build_analysis(
        payload=payload,
        candidates=candidates,
        para_texts=para_texts,
        canonical_kinds=[kind for _p, kind in canonical],
        allowed_type_keys=allowed_type_keys,
        truncated=para_truncated or cand_truncated,
    )


def _build_analysis(
    *,
    payload: dict[str, Any],
    candidates: list[FillCandidate],
    para_texts: list[str],
    canonical_kinds: list[str],
    allowed_type_keys: list[str],
    truncated: bool,
) -> TemplateAnalysis:
    """Validate a parsed model response into a `TemplateAnalysis`.

    Split out from `analyze_template` so the whole validation surface — the part
    that decides what a model is allowed to influence — is testable without an
    LLM.
    """
    analysis = TemplateAnalysis(truncated=truncated)

    # ── Document type ──────────────────────────────────────────────────────
    raw_type = payload.get("document_type")
    if isinstance(raw_type, str) and raw_type.strip() in allowed_type_keys:
        analysis.document_type_key = raw_type.strip()
        analysis.document_type_confidence = _clamp_confidence(
            payload.get("document_type_confidence")
        )

    # ── Variables ──────────────────────────────────────────────────────────
    variables: dict[str, AnalyzedVariable] = {}
    for entry in payload.get("variables") or []:
        if not isinstance(entry, dict):
            continue
        name = _slugify_name(entry.get("internal_name"))
        if not name or name in variables:
            continue
        display = entry.get("display_name")
        variables[name] = AnalyzedVariable(
            internal_name=name,
            display_name=(
                display.strip()
                if isinstance(display, str) and display.strip()
                else name.replace("_", " ").title()
            ),
            description=(
                entry["description"].strip()
                if isinstance(entry.get("description"), str)
                else ""
            ),
            data_type=_coerce_data_type(entry.get("data_type")),
            is_required=bool(entry.get("required", True)),
            example_value=(
                entry["example_value"].strip()
                if isinstance(entry.get("example_value"), str)
                else ""
            ),
        )

    # ── Assignments onto pre-located fill-points ───────────────────────────
    by_id = {c.candidate_id: c for c in candidates}
    assigned: dict[int, tuple[str | None, float | None]] = {}
    for entry in payload.get("assignments") or []:
        if not isinstance(entry, dict):
            continue
        try:
            cid = int(entry.get("fill_point_id"))
        except (TypeError, ValueError):
            continue
        if cid not in by_id or cid in assigned:
            continue
        name = _slugify_name(entry.get("internal_name"))
        # A name the model never defined is not usable — the slot stays
        # unmapped and HR picks a value, rather than us inventing a variable.
        if name is not None and name not in variables:
            log.info("documents.analysis_unknown_variable name=%s", name)
            name = None
        assigned[cid] = (name, _clamp_confidence(entry.get("confidence")))

    # Every located fill-point becomes a slot, assigned or not. A fill-point the
    # model didn't understand is precisely the one that must not go invisible.
    for c in candidates:
        name, confidence = assigned.get(c.candidate_id, (None, None))
        analysis.slots.append(
            AnalyzedSlot(
                paragraph_index=c.paragraph_index,
                start_offset=c.start_offset,
                end_offset=c.end_offset,
                original_text=c.original_text,
                context_before=c.context_before,
                context_after=c.context_after,
                paragraph_kind=c.paragraph_kind,
                action=c.action,
                detection_kind=c.detection_kind,
                internal_name=name,
                confidence=confidence,
                candidate_id=c.candidate_id,
            )
        )

    # ── Literal proposals, located by exact search ─────────────────────────
    taken: dict[int, list[tuple[int, int]]] = {}
    for c in candidates:
        taken.setdefault(c.paragraph_index, []).append((c.start_offset, c.end_offset))

    for entry in payload.get("literals") or []:
        if not isinstance(entry, dict):
            continue
        quoted = entry.get("text")
        if not isinstance(quoted, str) or not quoted.strip():
            continue
        quoted = quoted.strip()
        try:
            para_idx = int(entry.get("paragraph"))
        except (TypeError, ValueError):
            analysis.rejected_literals.append(quoted)
            continue
        if not (0 <= para_idx < len(para_texts)):
            analysis.rejected_literals.append(quoted)
            continue

        span = _locate_literal(
            text=para_texts[para_idx],
            quoted=quoted,
            taken=taken.setdefault(para_idx, []),
        )
        if span is None:
            # Not present verbatim, or every occurrence is already claimed.
            analysis.rejected_literals.append(quoted)
            continue

        name = _slugify_name(entry.get("internal_name"))
        if name is not None and name not in variables:
            name = None

        start, end = span
        taken[para_idx].append(span)
        text = para_texts[para_idx]
        analysis.slots.append(
            AnalyzedSlot(
                paragraph_index=para_idx,
                start_offset=start,
                end_offset=end,
                original_text=quoted,
                context_before=text[max(0, start - 60):start],
                context_after=text[end:end + 60],
                paragraph_kind=(
                    canonical_kinds[para_idx]
                    if para_idx < len(canonical_kinds)
                    else "body"
                ),
                action=ACTION_REPLACE_SPAN,
                detection_kind=KIND_LLM_LITERAL,
                internal_name=name,
                confidence=_clamp_confidence(entry.get("confidence")),
                candidate_id=None,
            )
        )

    # Only keep variables something actually points at. A variable with no slot
    # would show up in the builder as a field HR must supply that appears
    # nowhere in the document.
    used = {s.internal_name for s in analysis.slots if s.internal_name}
    analysis.variables = [v for n, v in variables.items() if n in used]

    analysis.slots.sort(key=lambda s: (s.paragraph_index, s.start_offset))
    if analysis.rejected_literals:
        log.info(
            "documents.analysis_rejected_literals count=%d sample=%r",
            len(analysis.rejected_literals),
            analysis.rejected_literals[:3],
        )
    return analysis
