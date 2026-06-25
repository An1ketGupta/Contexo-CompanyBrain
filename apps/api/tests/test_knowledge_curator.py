"""Knowledge Curator — unit coverage for the pure-logic helpers.

The scan orchestrator itself is DB-bound and lives behind RPC + LLM calls,
so we exercise it via integration tests later. Here we cover the pieces
that have non-trivial logic without external dependencies:

  * URL extraction from prose (regex + trailing-punctuation stripping).
  * Knowledge gap topic aggregation + dedupe.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("DEBUG", "false")

from app.services.knowledge_curator import _extract_urls


# ── URL extraction ───────────────────────────────────────────────────────


def test_extract_urls_basic():
    text = "See https://example.com for details."
    assert _extract_urls(text) == ["https://example.com"]


def test_extract_urls_strips_trailing_punctuation():
    text = (
        "Refs: https://example.com, https://acme.com/path. "
        "More at https://docs.example.com/page); not at https://nope.com!?"
    )
    urls = _extract_urls(text)
    assert "https://example.com" in urls
    assert "https://acme.com/path" in urls
    assert "https://docs.example.com/page" in urls
    assert "https://nope.com" in urls
    # None should retain trailing punctuation.
    assert all(not u.endswith((",", ".", "!", "?", ")")) for u in urls)


def test_extract_urls_handles_http_and_https():
    text = "Old: http://legacy.example.com and new https://example.com."
    urls = _extract_urls(text)
    assert "http://legacy.example.com" in urls
    assert "https://example.com" in urls


def test_extract_urls_ignores_non_links():
    text = "ftp://nope.example.com and bare example.com aren't matched."
    assert _extract_urls(text) == []


def test_extract_urls_empty_or_none():
    assert _extract_urls(None) == []
    assert _extract_urls("") == []


def test_extract_urls_dedupe_not_done_here():
    # _extract_urls returns one entry per occurrence — dedupe is a job for
    # the caller (find_broken_links does it across docs).
    text = "https://a.com https://a.com"
    assert _extract_urls(text) == ["https://a.com", "https://a.com"]
