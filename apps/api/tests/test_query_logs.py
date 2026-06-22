"""V3 #91 — query_logs writer + retention safety.

Tests the input-trimming + validation contract without hitting the DB.
"""
import asyncio

import pytest

from app.services.query_logs import (
    _VALID_INTENTS,
    _log_query_inner,
    delete_old_query_logs,
)

# ── retention safety floor ───────────────────────────────────────────────


def test_retention_floor_blocks_dangerous_values():
    # < 7 days = almost always a config typo; we'd rather raise than wipe
    # months of users' history.
    with pytest.raises(ValueError):
        delete_old_query_logs(older_than_days=0)
    with pytest.raises(ValueError):
        delete_old_query_logs(older_than_days=3)


# ── input validation in the writer ──────────────────────────────────────
# The inner writer skips silently on bad input. It's wrapped in
# `log_query_async` which is fire-and-forget — we never want it to raise.


def test_writer_skips_silently_without_user_id(monkeypatch):
    called = {"insert": False}

    class _FakeTable:
        def insert(self, _row):
            called["insert"] = True
            return self
        def execute(self):
            return None

    class _FakeSvc:
        def table(self, _name):
            return _FakeTable()

    monkeypatch.setattr(
        "app.services.query_logs.get_service_client", lambda: _FakeSvc()
    )

    asyncio.run(_log_query_inner(
        user_id="",
        org_id="org-1",
        query_text="hello",
        intent="factual_qa",
        conversation_id=None,
        message_id=None,
        response_length=0,
        source_count=0,
        tool_calls=0,
        latency_ms=None,
        model_used=None,
    ))
    assert called["insert"] is False


def test_writer_skips_silently_on_empty_query(monkeypatch):
    called = {"insert": False}

    class _FakeTable:
        def insert(self, _row):
            called["insert"] = True
            return self
        def execute(self):
            return None

    class _FakeSvc:
        def table(self, _name):
            return _FakeTable()

    monkeypatch.setattr(
        "app.services.query_logs.get_service_client", lambda: _FakeSvc()
    )

    asyncio.run(_log_query_inner(
        user_id="u-1",
        org_id="org-1",
        query_text="    ",
        intent=None,
        conversation_id=None,
        message_id=None,
        response_length=0,
        source_count=0,
        tool_calls=0,
        latency_ms=None,
        model_used=None,
    ))
    assert called["insert"] is False


def test_writer_trims_query_and_validates_intent(monkeypatch):
    captured: dict = {}

    class _FakeTable:
        def insert(self, row):
            captured.update(row)
            return self
        def execute(self):
            return None

    class _FakeSvc:
        def table(self, _name):
            return _FakeTable()

    monkeypatch.setattr(
        "app.services.query_logs.get_service_client", lambda: _FakeSvc()
    )

    long_text = "x" * 5000
    asyncio.run(_log_query_inner(
        user_id="u-1",
        org_id="org-1",
        query_text=long_text,
        intent="bogus_intent",
        conversation_id=None,
        message_id=None,
        response_length=42,
        source_count=3,
        tool_calls=2,
        latency_ms=1500,
        model_used="gemini-3.1-flash-lite",
    ))
    assert len(captured["query_text"]) == 500          # trimmed
    assert captured["intent"] is None                   # bogus intent → NULL
    assert captured["response_length"] == 42
    assert captured["source_count"] == 3
    assert captured["latency_ms"] == 1500


def test_intent_set_covers_classifier_labels():
    # Sanity check that the in-module set isn't drifting from the chat router
    # contract — at minimum these labels should always be accepted.
    assert "factual_qa" in _VALID_INTENTS
    assert "task_generation" in _VALID_INTENTS
    assert "analysis" in _VALID_INTENTS
