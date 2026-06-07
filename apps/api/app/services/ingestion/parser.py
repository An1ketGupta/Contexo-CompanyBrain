"""Document parsers — produce paragraph-level RawSegments.

PDF: PyMuPDF 'dict' mode for block-level layout + font-size heading detection.
DOCX: python-docx style names ('Heading 1', 'Title', ...) drive section tracking.
MD:   ATX-style headings (#, ##, ###) anchor sections.
TXT:  blank-line-separated paragraphs.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Iterator
from io import BytesIO

import fitz  # PyMuPDF
from docx import Document as DocxDocument

from .types import RawSegment

log = logging.getLogger(__name__)


class ParseError(Exception):
    """Raised when a document cannot be parsed."""


class EmptyDocumentError(ParseError):
    """No extractable text — likely a scanned/image PDF or empty file."""


# Heading detection thresholds. Tuned for typical business documents.
_HEADING_FONT_RATIO = 1.18    # block's max font > body_size * ratio → heading
_HEADING_MAX_CHARS = 120      # bold/short blocks above this length are paragraphs, not titles
_BODY_SIZE_SAMPLE_PAGES = 6


def parse_document(file_bytes: bytes, file_type: str) -> list[RawSegment]:
    """Dispatch to the right parser and validate the output."""
    file_type = file_type.lower()
    if file_type == "pdf":
        segments = list(_parse_pdf(file_bytes))
    elif file_type == "docx":
        segments = list(_parse_docx(file_bytes))
    elif file_type == "md":
        segments = list(_parse_markdown(_decode_bytes(file_bytes)))
    elif file_type == "txt":
        segments = list(_parse_text(_decode_bytes(file_bytes)))
    else:
        raise ParseError(f"Unsupported file type: {file_type}")

    if not segments or not any(s.content.strip() for s in segments):
        raise EmptyDocumentError(
            "No extractable text found. The document may be a scanned/image-only PDF "
            "or contain no recognizable content."
        )
    return segments


# ── PDF ───────────────────────────────────────────────────────────────────────

def _parse_pdf(file_bytes: bytes) -> Iterator[RawSegment]:
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as exc:
        raise ParseError(f"Failed to open PDF: {exc}") from exc

    try:
        body_size = _detect_body_font_size(doc)
        current_heading: str | None = None

        for page in doc:
            page_dict = page.get_text("dict")
            for block in page_dict.get("blocks", []):
                if block.get("type") != 0:  # 0 = text, 1 = image
                    continue

                block_text, max_size, any_bold, line_count = _flatten_block(block)
                if not block_text:
                    continue

                if _is_heading(block_text, max_size, any_bold, line_count, body_size):
                    current_heading = block_text
                    continue

                yield RawSegment(
                    content=block_text,
                    page_number=page.number + 1,
                    section_heading=current_heading,
                    metadata={"font_size": round(max_size, 1)} if max_size else {},
                )
    finally:
        doc.close()


def _flatten_block(block: dict) -> tuple[str, float, bool, int]:
    lines: list[str] = []
    max_size = 0.0
    any_bold = False
    for line in block.get("lines", []):
        spans = line.get("spans", [])
        if not spans:
            continue
        line_text = "".join(s.get("text", "") for s in spans).strip()
        if line_text:
            lines.append(line_text)
        for s in spans:
            size = s.get("size", 0) or 0
            if size > max_size:
                max_size = size
            font_name = s.get("font") or ""
            if "Bold" in font_name or "Black" in font_name:
                any_bold = True
    return " ".join(lines).strip(), max_size, any_bold, len(lines)


def _is_heading(text: str, max_size: float, any_bold: bool, line_count: int, body_size: float) -> bool:
    if len(text) > _HEADING_MAX_CHARS:
        return False
    # Trailing-period paragraphs almost never headings, even if large.
    if text.endswith(".") and len(text) > 60:
        return False
    if body_size and max_size > body_size * _HEADING_FONT_RATIO:
        return True
    if any_bold and line_count <= 2 and len(text) < 80:
        return True
    return False


def _detect_body_font_size(doc) -> float:
    """Body font = the size with the most total character count in the first N pages."""
    sizes: Counter[float] = Counter()
    for page in doc[: min(_BODY_SIZE_SAMPLE_PAGES, len(doc))]:
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    size = round(span.get("size", 0) or 0, 1)
                    text_len = len(span.get("text", ""))
                    if size > 0 and text_len > 0:
                        sizes[size] += text_len
    if not sizes:
        return 11.0
    return sizes.most_common(1)[0][0]


# ── DOCX ──────────────────────────────────────────────────────────────────────

def _parse_docx(file_bytes: bytes) -> Iterator[RawSegment]:
    try:
        doc = DocxDocument(BytesIO(file_bytes))
    except Exception as exc:
        raise ParseError(f"Failed to open DOCX: {exc}") from exc

    current_heading: str | None = None

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style_name = (para.style.name or "").lower() if para.style else ""
        if style_name.startswith("heading") or style_name == "title" or style_name == "subtitle":
            current_heading = text
            continue
        yield RawSegment(content=text, section_heading=current_heading)

    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if not cells:
                continue
            yield RawSegment(
                content=" | ".join(cells),
                section_heading=current_heading,
                metadata={"source": "table"},
            )


# ── Markdown ──────────────────────────────────────────────────────────────────

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def _parse_markdown(text: str) -> Iterator[RawSegment]:
    current_heading: str | None = None
    buffer: list[str] = []

    def drain() -> RawSegment | None:
        joined = "\n".join(buffer).strip()
        buffer.clear()
        if not joined:
            return None
        return RawSegment(content=joined, section_heading=current_heading)

    for line in text.splitlines():
        m = _MD_HEADING_RE.match(line)
        if m:
            seg = drain()
            if seg:
                yield seg
            current_heading = m.group(2).strip()
            continue
        if not line.strip():
            seg = drain()
            if seg:
                yield seg
            continue
        buffer.append(line)

    seg = drain()
    if seg:
        yield seg


# ── Plain text ────────────────────────────────────────────────────────────────

def _parse_text(text: str) -> Iterator[RawSegment]:
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if para:
            yield RawSegment(content=para)


# ── Bytes → str with fallback ─────────────────────────────────────────────────

def _decode_bytes(file_bytes: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return file_bytes.decode("utf-8", errors="replace")
