"""Unit tests for app.services.billing.client.

The Stripe SDK is intentionally imported lazily, so these tests don't
require `stripe` to actually be installed — the StripeNotConfigured path
fires before any import would. The "configured" path uses a stub module
injected via monkeypatching `_get_stripe_module`.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.billing import client as billing_client


@pytest.fixture(autouse=True)
def _reset_lru_cache():
    billing_client.reset_stripe_client_cache()
    yield
    billing_client.reset_stripe_client_cache()


def _stub_settings(monkeypatch: pytest.MonkeyPatch, *, key: str, version: str = "2024-12-18.acacia"):
    s = SimpleNamespace(stripe_secret_key=key, stripe_api_version=version)
    monkeypatch.setattr("app.services.billing.client.get_settings", lambda: s)


def test_stripe_not_configured_raises(monkeypatch: pytest.MonkeyPatch):
    _stub_settings(monkeypatch, key="")
    with pytest.raises(billing_client.StripeNotConfigured):
        billing_client.get_stripe_client()


def test_configured_sets_api_key_and_version(monkeypatch: pytest.MonkeyPatch):
    _stub_settings(monkeypatch, key="sk_test_dummy", version="2024-12-18.acacia")

    stub_module = SimpleNamespace(api_key=None, api_version=None)
    monkeypatch.setattr(
        "app.services.billing.client._get_stripe_module",
        lambda: stub_module,
    )

    result = billing_client.get_stripe_client()
    assert result is stub_module
    assert stub_module.api_key == "sk_test_dummy"
    assert stub_module.api_version == "2024-12-18.acacia"


def test_get_stripe_client_is_cached(monkeypatch: pytest.MonkeyPatch):
    _stub_settings(monkeypatch, key="sk_test_dummy")

    call_count = {"n": 0}

    def _factory() -> SimpleNamespace:
        call_count["n"] += 1
        return SimpleNamespace(api_key=None, api_version=None)

    monkeypatch.setattr(
        "app.services.billing.client._get_stripe_module",
        _factory,
    )

    a = billing_client.get_stripe_client()
    b = billing_client.get_stripe_client()
    assert a is b
    # Module factory is only called once thanks to lru_cache.
    assert call_count["n"] == 1
