"""Zero-LLM-cost table-of-contents extraction for PDF and DOCX.

Strategy:
    PDF  → PyMuPDF's built-in bookmark TOC if present, otherwise font-size
           heading inference (same idea as the parser, lifted into its own
           pass so it can be run independently of chunking).
    DOCX → python-docx style names ("Heading 1/2/3", "Title", "Subtitle").
    Other → returns [] (TOC only makes sense for documents with structural headings).

Output goes into `documents.metadata.toc` as a JSON array; the frontend renders
indented entries with page numbers.

This module is intentionally side-effect free — call `extract_toc(bytes, type)`
and persist the result yourself. The Inngest wrapper lives in inngest/functions.py.
"""
from __future__ import annotations

import io
import logging
from collections import Counter
from dataclasses import asdict, dataclass

import fitz  # PyMuPDF — already in pyproject.toml
from docx import Document as DocxDocument

log = logging.getLogger(__name__)

# Cap total entries — a 50-page contract with deep nesting could otherwise
# produce 200+ rows that dwarf the document panel. 60 is roughly two screens.
_MAX_ENTRIES = 60
# Heading levels we surface. Beyond H3 the TOC stops being useful at a glance.
_MAX_LEVEL = 3
# Headings tend to fit on one line, so a short character cap filters out body
# paragraphs that happen to be set in a larger font.
_HEADING_MAX_CHARS = 140
# Font ratio threshold: a line whose dominant size is > body * ratio is treated
# as a heading. Matches the parser's heading detection (one source of truth).
_HEADING_FONT_RATIO = 1.18
_BODY_SIZE_SAMPLE_PAGES = 6

_DOCX_HEADING_STYLES: dict[str, int] = {
    "heading 1": 1,
    "heading 2": 2,
    "heading 3": 3,
    "title": 1,
    "subtitle": 2,
}


@dataclass(frozen=True)
class TocEntry:
    """One row in the rendered table of contents.

    `page` is 1-indexed for PDFs (matches user expectation and PyMuPDF's
    `get_toc()` output). For DOCX it's None — Word reflows pages on render
    so the source file's page numbers are not knowable from python-docx alone.
    """
    level: int
    title: str
    page: int | None


def extract_toc(file_bytes: bytes, file_type: str) -> list[TocEntry]:
    """Dispatch by file type. Returns [] for unsupported types or empty files."""
    ft = (file_type or "").lower()
    if ft == "pdf":
        return _extract_pdf(file_bytes)
    if ft == "docx":
        return _extract_docx(file_bytes)
    return []


def to_json(entries: list[TocEntry]) -> list[dict]:
    """Persist-friendly form for the documents.metadata.toc column."""
    return [asdict(e) for e in entries]


# ── PDF ─────────────────────────────────────────────────────────────────────

def _extract_pdf(file_bytes: bytes) -> list[TocEntry]:
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as exc:
        log.info("toc: failed to open PDF: %s", exc)
        return []

    try:
        # Path 1 — author-supplied bookmarks. Almost always present in
        # professionally produced PDFs (contracts, books, brand guides).
        entries = _pdf_bookmark_toc(doc)
        if entries:
            return entries[:_MAX_ENTRIES]
        # Path 2 — infer from font sizes. Catches Word-exported PDFs that
        # didn't generate bookmarks but still have a clear heading hierarchy.
        return _pdf_font_inferred_toc(doc)[:_MAX_ENTRIES]
    finally:
        doc.close()


def _pdf_bookmark_toc(doc) -> list[TocEntry]:
    try:
        raw = doc.get_toc(simple=True) or []
    except Exception:
        return []
    entries: list[TocEntry] = []
    for row in raw:
        # PyMuPDF returns [level, title, page] for simple=True. Defensive
        # indexing in case the SDK signature drifts.
        if len(row) < 3:
            continue
        level = int(row[0])
        title = str(row[1] or "").strip()
        page = row[2]
        if not title or level > _MAX_LEVEL or len(title) > _HEADING_MAX_CHARS:
            continue
        try:
            page_int = int(page) if page else None
        except (TypeError, ValueError):
            page_int = None
        entries.append(TocEntry(level=level, title=title, page=page_int))
    return entries


def _pdf_font_inferred_toc(doc) -> list[TocEntry]:
    """Headings = lines whose font size exceeds the body font size.

    Distinct heading levels are derived from the *unique* size bands found above
    body — capped at 3 so we don't surface a noisy 7-level hierarchy from a
    document that styled each section header slightly differently.
    """
    sizes_count: Counter[int] = Counter()
    for page in doc[: min(_BODY_SIZE_SAMPLE_PAGES, len(doc))]:
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    size = round(span.get("size", 0) or 0)
                    text_len = len(span.get("text", ""))
                    if size > 0 and text_len > 0:
                        sizes_count[size] += text_len
    if not sizes_count:
        return []

    body_size = sizes_count.most_common(1)[0][0]
    # All sizes that exceed the heading ratio, ordered largest → smallest.
    heading_sizes = sorted(
        (s for s in sizes_count if s > body_size * _HEADING_FONT_RATIO),
        reverse=True,
    )[:_MAX_LEVEL]
    if not heading_sizes:
        return []
    size_to_level: dict[int, int] = {s: i + 1 for i, s in enumerate(heading_sizes)}

    entries: list[TocEntry] = []
    seen: set[tuple[int, str]] = set()  # dedupe (level, title) across pages

    for page_num, page in enumerate(doc, start=1):
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                line_text_parts: list[str] = []
                line_max_size = 0
                for span in line.get("spans", []):
                    txt = (span.get("text") or "").strip()
                    if txt:
                        line_text_parts.append(txt)
                    size = round(span.get("size", 0) or 0)
                    if size > line_max_size:
                        line_max_size = size
                title = " ".join(line_text_parts).strip()
                if not title:
                    continue
                if len(title) > _HEADING_MAX_CHARS:
                    continue
                # Trailing-period paragraphs almost never headings even if large.
                if title.endswith(".") and len(title) > 60:
                    continue
                level = size_to_level.get(line_max_size)
                if not level:
                    continue
                key = (level, title.lower())
                if key in seen:
                    continue
                seen.add(key)
                entries.append(TocEntry(level=level, title=title, page=page_num))
                if len(entries) >= _MAX_ENTRIES:
                    return entries
    return entries


# ── DOCX ────────────────────────────────────────────────────────────────────

def _extract_docx(file_bytes: bytes) -> list[TocEntry]:
    try:
        doc = DocxDocument(io.BytesIO(file_bytes))
    except Exception as exc:
        log.info("toc: failed to open DOCX: %s", exc)
        return []

    entries: list[TocEntry] = []
    seen: set[tuple[int, str]] = set()
    for para in doc.paragraphs:
        style = (para.style.name or "").strip().lower() if para.style else ""
        level = _DOCX_HEADING_STYLES.get(style)
        if not level:
            continue
        text = (para.text or "").strip()
        if not text or len(text) > _HEADING_MAX_CHARS:
            continue
        key = (level, text.lower())
        if key in seen:
            continue
        seen.add(key)
        entries.append(TocEntry(level=level, title=text, page=None))
        if len(entries) >= _MAX_ENTRIES:
            break
    return entries
