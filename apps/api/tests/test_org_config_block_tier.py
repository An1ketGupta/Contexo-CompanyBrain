"""Tests for the confidence block tier added in the Day 3-4 hardening pass.

We focus on the JSONB parser + the update helper's validation rules. The
write path is exercised against a tiny in-memory fake to avoid bringing in
Supabase for a unit test.
"""
from __future__ import annotations

import pytest

from app.services import org_config

# ── _parse_confidence ──────────────────────────────────────────────────────


def test_parse_missing_metadata_returns_default():
    out = org_config._parse_confidence(None)
    assert out == org_config.ConfidenceThresholds.default()
    assert out.block == 0.0


def test_parse_legacy_row_without_block_field():
    # Rows written before this migration only have high/medium; block should
    # default to 0.0 (off).
    out = org_config._parse_confidence({"confidence_thresholds": {"high": 0.8, "medium": 0.5}})
    assert out.high == 0.8
    assert out.medium == 0.5
    assert out.block == 0.0


def test_parse_full_row_carries_block():
    out = org_config._parse_confidence(
        {"confidence_thresholds": {"high": 0.8, "medium": 0.5, "block": 0.3}}
    )
    assert out.block == 0.3


def test_parse_invalid_block_falls_back_to_default():
    # block > medium → not well-ordered → drop block back to default rather
    # than poison the row.
    out = org_config._parse_confidence(
        {"confidence_thresholds": {"high": 0.8, "medium": 0.4, "block": 0.5}}
    )
    assert out.high == 0.8
    assert out.medium == 0.4
    assert out.block == 0.0


def test_parse_non_numeric_block_falls_back():
    out = org_config._parse_confidence(
        {"confidence_thresholds": {"high": 0.8, "medium": 0.5, "block": "garbage"}}
    )
    assert out.block == 0.0


def test_parse_negative_block_rejected():
    out = org_config._parse_confidence(
        {"confidence_thresholds": {"high": 0.8, "medium": 0.5, "block": -0.1}}
    )
    assert out.block == 0.0


# ── update_confidence_thresholds validation ────────────────────────────────


async def test_update_rejects_block_above_medium(monkeypatch: pytest.MonkeyPatch) -> None:
    # Even without touching the DB, the validation guard should fire.
    with pytest.raises(ValueError, match="Block threshold"):
        await org_config.update_confidence_thresholds(
            org_id="any", high=0.8, medium=0.5, block=0.7
        )


async def test_update_rejects_medium_above_high(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="0 <= medium <= high"):
        await org_config.update_confidence_thresholds(
            org_id="any", high=0.4, medium=0.6
        )


async def test_update_preserves_existing_block_when_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    """If a caller PUTs only high/medium, the existing `block` on disk must
    not be silently cleared — that would re-enable risky writes."""
    captured: dict[str, object] = {}

    class _FakeQuery:
        def __init__(self, data=None) -> None:
            self._data = data

        def select(self, *_a, **_kw): return self
        def eq(self, *_a, **_kw): return self
        def maybe_single(self): return self
        def execute(self):
            class R:
                data = self._data if isinstance(self._data, dict) else None
            R.data = self._data if isinstance(self._data, dict) else None
            return R

        def update(self, payload):
            captured["update"] = payload
            return self

    class _FakeTable:
        def __init__(self, name: str) -> None:
            self._name = name
            self._data = {
                "metadata": {
                    "confidence_thresholds": {"high": 0.7, "medium": 0.4, "block": 0.25}
                }
            }

        def select(self, *_a, **_kw):
            return _FakeQuery(self._data)

        def update(self, payload):
            captured["update"] = payload
            return _FakeQuery({})

    class _FakeSvc:
        def table(self, name):
            return _FakeTable(name)

    monkeypatch.setattr(org_config, "get_service_client", lambda: _FakeSvc())
    org_config.invalidate_all()

    saved = await org_config.update_confidence_thresholds(
        org_id="org", high=0.9, medium=0.5
    )
    assert saved.block == 0.25
    assert captured["update"]["metadata"]["confidence_thresholds"]["block"] == 0.25
