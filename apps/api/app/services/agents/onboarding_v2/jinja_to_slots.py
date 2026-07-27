"""Convert a `{{ tag }}` template into positional slots.

This is what makes retiring docxtpl possible at all.

Every template that predates the slots pipeline has real `{{ }}` tags baked into
its stored `.docx` — written there by the old `apply_mappings` step, or typed by
hand. Migration 097 refused to reclassify them for a good reason: flipping such a
template to `slots` with no slot rows would render it completely unfilled, and do
it silently.

But a baked tag is not an obstacle to the slots model — it is the ideal input for
it. `{{ candidate_name }}` already states both halves of a fill-point: *where*
(its character span) and *what* (the variable named inside it). So conversion is
mechanical and lossless, needs no LLM, and needs nothing from HR: the tag span
becomes a `replace_span` slot whose variable is the tag's own name.

Conversion is refused, not guessed, when a tag can't be represented as a slot:

  * a filter or expression (`{{ ctc | upper }}`) — slots substitute a value, they
    don't evaluate anything;
  * a name outside the variable vocabulary — it would fail at render time anyway,
    but as a slot it would fail *after* we'd already rewritten the template's
    strategy;
  * malformed syntax (`{{ Signing Date }}`) — that document needs real blank
    detection, not conversion.

A template with any unconvertible tag is reported and left alone. Half-converting
one would produce a document where some fields fill and others render as literal
`{{ }}` text in a signed contract.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from docx import Document

from .blank_detector import ACTION_REPLACE_SPAN, BlankCandidate
from .docx_positions import canonical_paragraphs, paragraph_run_text
from .jinja_validator import JINJA_SPAN_RE, find_unbalanced_braces
from .template_vars import get_variable_names

# A tag that is exactly one bare variable name — the only shape a positional slot
# can represent. Anything else (filters, attribute access, literals, arithmetic)
# needs an expression evaluator, which is the thing we are removing.
_BARE_VARIABLE_RE = re.compile(r"^\{\{\s*([A-Za-z_]\w*)\s*\}\}$")


@dataclass
class ConversionPlan:
    """What converting one template would produce.

    `convertible` is empty when `blockers` is non-empty: a partial conversion is
    never offered, because a template where half the fields fill and half render
    as literal `{{ }}` text is worse than one that still works on the old path.
    """

    convertible: list[BlankCandidate] = field(default_factory=list)
    variables: dict[int, str] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    tag_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.blockers and bool(self.convertible)


def plan_conversion(docx_bytes: bytes) -> ConversionPlan:
    """Work out whether a `{{ tag }}` template can become a slots template.

    Read-only: inspects the bytes and returns a plan. Nothing is written here, so
    a caller can dry-run the whole estate before touching a single template.
    """
    known = set(get_variable_names())
    doc = Document(io.BytesIO(docx_bytes))

    candidates: list[BlankCandidate] = []
    variables: dict[int, str] = {}
    # An unclosed `{{` is not a convertible tag and not safely ignorable: the
    # literal text would survive into the rendered document. Surface it so the
    # template gets fixed rather than converted around.
    blockers: list[str] = list(find_unbalanced_braces(docx_bytes))
    tag_count = 0
    cid = 0

    for idx, (paragraph, kind) in enumerate(canonical_paragraphs(doc)):
        text = paragraph_run_text(paragraph)
        if "{{" not in text:
            continue
        for match in JINJA_SPAN_RE.finditer(text):
            tag = match.group(0)
            tag_count += 1
            bare = _BARE_VARIABLE_RE.match(tag)
            if not bare:
                blockers.append(
                    f"{tag!r} isn't a plain placeholder — a filter or expression "
                    "can't be represented as a field."
                )
                continue
            name = bare.group(1)
            if name not in known:
                blockers.append(
                    f"{tag!r} names '{name}', which isn't one of the values we "
                    "supply."
                )
                continue
            start, end = match.start(), match.end()
            candidates.append(
                BlankCandidate(
                    candidate_id=cid,
                    paragraph_index=idx,
                    start_offset=start,
                    end_offset=end,
                    matched_text=tag,
                    context_before=text[max(0, start - 60):start],
                    context_after=text[end:end + 60],
                    paragraph_kind=kind,
                    action=ACTION_REPLACE_SPAN,
                )
            )
            variables[cid] = name
            cid += 1

    if blockers:
        # All-or-nothing, deliberately.
        return ConversionPlan(
            convertible=[], variables={}, blockers=blockers, tag_count=tag_count
        )

    return ConversionPlan(
        convertible=candidates, variables=variables, blockers=[], tag_count=tag_count
    )
