"""Drive the admin analytics endpoint with seeded analytics_events and
assert the feedback_score arithmetic and filters behave correctly.

We stub get_service_client with a chain-aware fake and skip auth via
_require_admin patch. Every other table returns empty so the endpoint
reaches the return dict and we read stats.feedback_score.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest


class _FakeResult:
    def __init__(self, data: list[dict[str, Any]] | None = None, count: int | None = None) -> None:
        self.data = data or []
        self.count = count


class _FakeQuery:
    def __init__(self, data: list[dict[str, Any]] | None = None, count: int | None = None) -> None:
        self._data = data or []
        self._count = count

    def select(self, *a, **kw): return self
    def eq(self, *a, **kw): return self
    def gte(self, *a, **kw): return self
    def lte(self, *a, **kw): return self
    def in_(self, *a, **kw): return self
    def order(self, *a, **kw): return self
    def limit(self, *a, **kw): return self
    def maybe_single(self): return self
    def single(self): return self
    def insert(self, *a, **kw): return self
    def upsert(self, *a, **kw): return self
    def update(self, *a, **kw): return self
    def delete(self): return self

    def execute(self) -> _FakeResult:
        return _FakeResult(self._data, self._count)


class _FakeAuthAdmin:
    def get_user_by_id(self, uid: str):
        class U:
            class user:
                email = None
        return U()


class _FakeAuth:
    admin = _FakeAuthAdmin()


class _FakeClient:
    def __init__(self, tables: dict[str, dict[str, Any]]) -> None:
        self._tables = tables
        self.auth = _FakeAuth()

    def table(self, name: str) -> _FakeQuery:
        spec = self._tables.get(name) or {}
        return _FakeQuery(data=spec.get("data", []), count=spec.get("count"))


async def _call_endpoint(monkeypatch, tables: dict[str, dict[str, Any]], period: str = "30d") -> dict:
    from app.routers import admin as admin_router

    async def fake_require_admin(_current_user: dict) -> str:
        return "org-1"

    monkeypatch.setattr(admin_router, "_require_admin", fake_require_admin)
    monkeypatch.setattr(admin_router, "get_service_client", lambda: _FakeClient(tables))
    monkeypatch.setattr(admin_router, "get_user_client", lambda *_a, **_k: _FakeClient(tables))

    return await admin_router.get_admin_analytics(
        period=period,
        current_user={"user_id": "u-1", "org_id": "org-1", "token": "fake"},
    )


def _ev(feedback: str | None, days_ago: int = 1) -> dict[str, Any]:
    ts = datetime.now(timezone.utc) - timedelta(days=days_ago)
    meta = {"feedback": feedback} if feedback is not None else {}
    return {"created_at": ts.isoformat(), "metadata": meta}


# ── tests ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_feedback_score_mixed(monkeypatch):
    events = [_ev("positive")] * 3 + [_ev("negative")] * 1
    result = await _call_endpoint(monkeypatch, {"analytics_events": {"data": events}})
    assert result["stats"]["feedback_score"] == 75  # 3/4 = 75


@pytest.mark.asyncio
async def test_feedback_score_all_positive(monkeypatch):
    events = [_ev("positive")] * 5
    result = await _call_endpoint(monkeypatch, {"analytics_events": {"data": events}})
    assert result["stats"]["feedback_score"] == 100


@pytest.mark.asyncio
async def test_feedback_score_all_negative(monkeypatch):
    events = [_ev("negative")] * 5
    result = await _call_endpoint(monkeypatch, {"analytics_events": {"data": events}})
    assert result["stats"]["feedback_score"] == 0


@pytest.mark.asyncio
async def test_feedback_score_none_when_empty(monkeypatch):
    result = await _call_endpoint(monkeypatch, {"analytics_events": {"data": []}})
    assert result["stats"]["feedback_score"] is None


@pytest.mark.asyncio
async def test_feedback_ignores_malformed_metadata(monkeypatch):
    events = [
        _ev("positive"),
        _ev("positive"),
        {"created_at": datetime.now(timezone.utc).isoformat(), "metadata": None},
        {"created_at": datetime.now(timezone.utc).isoformat(), "metadata": {"feedback": "meh"}},
        {"created_at": datetime.now(timezone.utc).isoformat(), "metadata": {}},
        _ev("negative"),
    ]
    result = await _call_endpoint(monkeypatch, {"analytics_events": {"data": events}})
    assert result["stats"]["feedback_score"] == 67  # 2/3 = 66.67 → 67


@pytest.mark.asyncio
async def test_feedback_rounding_boundary(monkeypatch):
    events = [_ev("positive")] * 1 + [_ev("negative")] * 2
    result = await _call_endpoint(monkeypatch, {"analytics_events": {"data": events}})
    assert result["stats"]["feedback_score"] == 33  # 1/3 = 33.33 → 33
