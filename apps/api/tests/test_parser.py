"""Deterministic parser tests — no external services."""
from __future__ import annotations

import pytest

from app.services.ingestion.parser import (
    EmptyDocumentError,
    ParseError,
    parse_document,
)


def test_txt_splits_on_blank_lines():
    text = b"Para one.\n\nPara two with more.\n\nPara three."
    segments = parse_document(text, "txt")
    assert len(segments) == 3
    assert segments[0].content == "Para one."
    assert segments[2].content == "Para three."
    assert all(s.section_heading is None for s in segments)
    assert all(s.page_number is None for s in segments)


def test_md_tracks_atx_headings():
    text = (
        b"# Title\n\nIntro line.\n\n"
        b"## Refunds\n\nWe refund within 30 days.\n\n"
        b"## Hiring\n\nWe hire on Mondays."
    )
    segments = parse_document(text, "md")
    contents = [(s.content, s.section_heading) for s in segments]
    assert ("Intro line.", "Title") in contents
    assert ("We refund within 30 days.", "Refunds") in contents
    assert ("We hire on Mondays.", "Hiring") in contents


def test_md_handles_no_blank_line_before_heading():
    text = b"Some intro.\n## Section A\nFirst para of A."
    segments = parse_document(text, "md")
    bodies = [s.content for s in segments if s.section_heading == "Section A"]
    assert "First para of A." in bodies


def test_unsupported_file_type_raises():
    with pytest.raises(ParseError):
        parse_document(b"whatever", "xlsx")


def test_empty_document_raises():
    with pytest.raises(EmptyDocumentError):
        parse_document(b"   \n\n   ", "txt")


def test_md_with_only_headings_raises():
    with pytest.raises(EmptyDocumentError):
        parse_document(b"# A\n\n## B\n\n### C", "md")
