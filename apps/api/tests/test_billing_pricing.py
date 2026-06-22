"""Unit tests for app.services.billing.pricing.

These run hermetically: the Supabase service-role client is monkeypatched
to return a static `pricing_tiers` row set instead of hitting the network.
The test file also exercises the TTL cache (force-reload via
invalidate_cache, mode-switch reload).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.services.billing import pricing

# ── Fixtures + stubs ───────────────────────────────────────────────────────


@dataclass(slots=True)
class _StubResponse:
    data: list[dict[str, Any]]


class _StubQuery:
    """Mimics the supabase-py builder chain enough for pricing.py.

    Real client returns a builder you can chain .select().eq().eq().execute()
    on. We swallow every chain method and return ourselves so the only
    bit that matters — `.execute()` — yields the stub rows.
    """

    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def select(self, *_args, **_kwargs) -> "_StubQuery":
        return self

    def eq(self, *_args, **_kwargs) -> "_StubQuery":
        return self

    def execute(self) -> _StubResponse:
        return _StubResponse(self._rows)


class _StubClient:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows
        self.table_calls: list[str] = []

    def table(self, name: str) -> _StubQuery:
        self.table_calls.append(name)
        return _StubQuery(self._rows)


def _seed_rows(mode: str = "test") -> list[dict[str, Any]]:
    """Three plans × two intervals = 6 active rows in the requested mode."""
    out: list[dict[str, Any]] = []
    pricings = [
        ("starter", "month", "price_starter_month_test", 4900, 100, 500, 10),
        ("starter", "year",  "price_starter_year_test",  47000, 100, 500, 10),
        ("team",    "month", "price_team_month_test",    12900, 1000, 3000, 30),
        ("team",    "year",  "price_team_year_test",    124000, 1000, 3000, 30),
        # Business has NULL quotas (= unlimited).
        ("business","month", "price_business_month_test", 29900, None, None, None),
        ("business","year",  "price_business_year_test", 287000, None, None, None),
    ]
    for plan, interval, price_id, amount, qdocs, qq, qseats in pricings:
        out.append({
            "plan": plan,
            "interval": interval,
            "stripe_price_id": price_id,
            "stripe_product_id": f"prod_{plan}_{mode}",
            "unit_amount_cents": amount,
            "currency": "usd",
            "quota_documents": qdocs,
            "quota_queries_monthly": qq,
            "quota_seats": qseats,
            "is_active": True,
        })
    return out


@pytest.fixture(autouse=True)
def _reset_pricing_cache():
    """Every test starts with a cold cache so ordering can't leak state."""
    pricing.invalidate_cache()
    yield
    pricing.invalidate_cache()


@pytest.fixture
def stub_db(monkeypatch: pytest.MonkeyPatch):
    rows = _seed_rows("test")
    client = _StubClient(rows)
    monkeypatch.setattr(
        "app.services.billing.pricing.get_service_client",
        lambda: client,
    )
    return client


@pytest.fixture
def settings_test_mode(monkeypatch: pytest.MonkeyPatch):
    class _S:
        stripe_mode = "test"

    monkeypatch.setattr(
        "app.services.billing.pricing.get_settings",
        lambda: _S(),
    )


# ── Tests ──────────────────────────────────────────────────────────────────


def test_plan_for_price_known_id(stub_db, settings_test_mode):
    assert pricing.plan_for_price("price_starter_month_test") == "starter"
    assert pricing.plan_for_price("price_team_year_test") == "team"
    assert pricing.plan_for_price("price_business_month_test") == "business"


def test_plan_for_price_unknown_returns_none(stub_db, settings_test_mode):
    assert pricing.plan_for_price("price_does_not_exist") is None


def test_plan_for_price_empty_input_returns_none(stub_db, settings_test_mode):
    # Don't even hit the DB on falsy inputs.
    assert pricing.plan_for_price("") is None


def test_list_active_tiers_sorted(stub_db, settings_test_mode):
    tiers = pricing.list_active_tiers()
    assert len(tiers) == 6
    # Sorted by (plan, interval): business < starter < team alphabetically;
    # within each plan, month < year alphabetically.
    plans_intervals = [(t.plan, t.interval) for t in tiers]
    assert plans_intervals == [
        ("business", "month"),
        ("business", "year"),
        ("starter", "month"),
        ("starter", "year"),
        ("team", "month"),
        ("team", "year"),
    ]


def test_business_quota_is_unlimited(stub_db, settings_test_mode):
    q = pricing.quota_for_plan("business")
    assert pricing.is_unlimited(q.documents)
    assert pricing.is_unlimited(q.queries_monthly)
    assert pricing.is_unlimited(q.seats)


def test_starter_quota_concrete(stub_db, settings_test_mode):
    q = pricing.quota_for_plan("starter")
    assert q.documents == 100
    assert q.queries_monthly == 500
    assert q.seats == 10


def test_free_plan_quota_is_zero(stub_db, settings_test_mode):
    # The free plan isn't in pricing_tiers — service falls back to a
    # zero-quota record so quota checks default to closed.
    q = pricing.quota_for_plan("free")
    assert q.plan == "free"
    assert q.documents == 0
    assert q.queries_monthly == 0
    assert q.seats == 1


def test_unknown_plan_falls_back_to_free(stub_db, settings_test_mode):
    q = pricing.quota_for_plan("enterprise-imaginary")
    assert q.plan == "free"


def test_cache_avoids_redundant_db_calls(stub_db, settings_test_mode):
    pricing.plan_for_price("price_starter_month_test")
    pricing.plan_for_price("price_team_month_test")
    pricing.list_active_tiers()
    # Three accesses, one DB hit (the first one).
    assert stub_db.table_calls == ["pricing_tiers"]


def test_invalidate_cache_forces_reload(stub_db, settings_test_mode):
    pricing.plan_for_price("price_starter_month_test")
    pricing.invalidate_cache()
    pricing.plan_for_price("price_starter_month_test")
    assert stub_db.table_calls == ["pricing_tiers", "pricing_tiers"]


def test_mode_switch_triggers_reload(monkeypatch: pytest.MonkeyPatch):
    """Switching stripe_mode (test→live) must reload the cache.

    Otherwise a hot process would serve the wrong tier set after a
    cutover-time settings reload.
    """
    rows_test = _seed_rows("test")
    rows_live = [
        {
            "plan": "starter",
            "interval": "month",
            "stripe_price_id": "price_starter_month_LIVE",
            "stripe_product_id": "prod_starter_live",
            "unit_amount_cents": 4900,
            "currency": "usd",
            "quota_documents": 100,
            "quota_queries_monthly": 500,
            "quota_seats": 10,
            "is_active": True,
        }
    ]
    current_rows = {"r": rows_test}

    class _Client:
        def __init__(self):
            self.calls = 0

        def table(self, _):
            self.calls += 1
            return _StubQuery(current_rows["r"])

    client = _Client()
    monkeypatch.setattr(
        "app.services.billing.pricing.get_service_client",
        lambda: client,
    )

    class _S:
        stripe_mode = "test"

    s = _S()
    monkeypatch.setattr("app.services.billing.pricing.get_settings", lambda: s)

    pricing.invalidate_cache()
    assert pricing.plan_for_price("price_starter_month_test") == "starter"
    assert pricing.plan_for_price("price_starter_month_LIVE") is None

    # Flip mode mid-process — cache MUST reload.
    s.stripe_mode = "live"
    current_rows["r"] = rows_live

    assert pricing.plan_for_price("price_starter_month_LIVE") == "starter"
    # And the test-mode id is no longer recognized.
    assert pricing.plan_for_price("price_starter_month_test") is None
