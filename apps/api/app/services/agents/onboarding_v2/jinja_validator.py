"""Decide whether a template really is a hand-authored Jinja template.

This module exists because of a specific production failure. The old detector
asked "does this document contain something shaped like `{{ ... }}`?" with a
regex whose character class included `\\s`, so `{{ Signing Date }}` — two bare
words, which Jinja cannot parse — matched. The document was classified as
"already templated", skipped blank detection entirely, and went straight to
docxtpl, which failed at *render* time with:

    docxtpl fill failed: expected token 'end of print statement', got 'signing'

HR saw that message with no indication of which placeholder was wrong or how to
fix it, and the failure surfaced long after the moment they could have fixed it.

So the question is asked properly here instead: every `{{ ... }}` span is
handed to Jinja's own parser. Only a document whose tags ALL parse is trusted as
a Jinja template. A document with a malformed tag is not trusted — it falls
through to blank detection, where the malformed span becomes an ordinary
reviewable fill-point, and HR gets a plain-English explanation at analyze time.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from docx import Document
from jinja2 import Environment
from jinja2.exceptions import TemplateSyntaxError
from jinja2.meta import find_undeclared_variables

from .docx_positions import canonical_paragraphs, paragraph_run_text
from .template_vars import get_variable_names

# Any `{{ ... }}` span, however malformed inside. Deliberately permissive —
# the point is to catch everything that LOOKS like a tag to a human and then
# let the real parser judge it.
JINJA_SPAN_RE = re.compile(r"\{\{.*?\}\}", re.DOTALL)

# Two or more bare words with nothing but whitespace between them — by far the
# most common way a hand-typed tag is invalid, and worth its own hint.
_MULTI_WORD_RE = re.compile(r"^\s*[A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)+\s*$")

# Strategies. Mirror documents.fill_strategy plus one analysis-only outcome.
STRATEGY_JINJA = "jinja"
STRATEGY_SLOTS = "slots"
STRATEGY_MIXED_INVALID = "mixed_invalid"


@dataclass
class JinjaScan:
    """What the document's `{{ }}` tags (if any) turned out to be.

    `strategy` is the routing decision:
      * `slots` — no tags at all; the document needs blank detection.
      * `jinja` — every tag parses; honour it as a hand-authored template.
      * `mixed_invalid` — at least one tag is malformed. NOT trusted as a
        template: the caller runs blank detection and surfaces `errors`.
    """

    strategy: str
    tags: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    unknown_variables: list[str] = field(default_factory=list)

    @property
    def has_valid_placeholders(self) -> bool:
        return self.strategy == STRATEGY_JINJA


def extract_jinja_tags(docx_bytes: bytes) -> list[str]:
    """Every `{{ ... }}` span in the document, body + tables + headers/footers.

    Reads joined run text (via `paragraph_run_text`) rather than
    `Paragraph.text`, which matters here: Word routinely splits a hand-typed
    tag across runs when part of it gets styled or spell-checked, and only the
    joined view sees `{{signing` + `_date}}` as one tag.
    """
    doc = Document(io.BytesIO(docx_bytes))
    tags: list[str] = []
    for paragraph, _kind in canonical_paragraphs(doc):
        text = paragraph_run_text(paragraph)
        if "{{" in text:
            tags.extend(m.group(0) for m in JINJA_SPAN_RE.finditer(text))
    return tags


def _describe_bad_tag(tag: str, exc: TemplateSyntaxError) -> str:
    """Turn a Jinja parser error into something HR can act on."""
    inner = tag.strip()
    if inner.startswith("{{"):
        inner = inner[2:]
    if inner.endswith("}}"):
        inner = inner[:-2]
    inner = inner.strip()

    if _MULTI_WORD_RE.match(inner):
        suggestion = "_".join(inner.split()).lower()
        return (
            f"'{tag}' is not a valid placeholder — a placeholder name cannot "
            f"contain spaces. Did you mean {{{{ {suggestion} }}}}?"
        )
    return f"'{tag}' is not a valid placeholder ({exc.message})."


def validate_jinja_tags(tags: list[str]) -> tuple[list[str], list[str]]:
    """Parse each tag with Jinja itself.

    Returns `(errors, referenced_variables)`. A tag is checked in isolation,
    which is exactly what we want: we are validating placeholder syntax, not
    whole-document control flow.
    """
    env = Environment()
    errors: list[str] = []
    referenced: list[str] = []
    for tag in tags:
        try:
            ast = env.parse(tag)
        except TemplateSyntaxError as exc:
            errors.append(_describe_bad_tag(tag, exc))
            continue
        referenced.extend(sorted(find_undeclared_variables(ast)))
    return errors, referenced


def classify_template(docx_bytes: bytes) -> JinjaScan:
    """Route a template to the pipeline that can actually render it.

    Note what happens with a valid tag that names a variable we don't supply
    (`{{ employee_pf_number }}`): the document still classifies as `jinja`,
    because it IS a template — but the unknown name is reported now, at analyze
    time, instead of blowing up as a `TemplateVariableError` when a real
    candidate's document is being generated.
    """
    tags = extract_jinja_tags(docx_bytes)
    if not tags:
        return JinjaScan(strategy=STRATEGY_SLOTS)

    errors, referenced = validate_jinja_tags(tags)
    known = set(get_variable_names())
    unknown = sorted({v for v in referenced if v not in known})

    if errors:
        # Not a template we can trust. The caller treats it as an untemplated
        # document and runs blank detection; `errors` explains why to HR.
        return JinjaScan(
            strategy=STRATEGY_MIXED_INVALID,
            tags=tags,
            errors=errors,
            unknown_variables=unknown,
        )

    return JinjaScan(
        strategy=STRATEGY_JINJA,
        tags=tags,
        errors=[],
        unknown_variables=unknown,
    )
