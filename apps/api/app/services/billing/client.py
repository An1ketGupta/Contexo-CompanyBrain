"""Lazy-initialized Stripe SDK client.

Importing `stripe` at module import time is fine cost-wise, but the actual
SDK call surface depends on `stripe.api_key` being set. We centralize that
in `get_stripe_client()` so:

  * Any code path that needs Stripe fails closed with a clear error when
    the deploy hasn't been wired up yet (vs. a confusing 401 from Stripe).
  * The pinned `api_version` lives in one place — bumping it is a single
    config change.
  * Tests can monkeypatch `_get_stripe_module()` to inject a stub without
    having to swap `stripe.api_key` global state.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.config import get_settings


class StripeNotConfigured(RuntimeError):
    """Raised when Stripe is invoked without STRIPE_SECRET_KEY set.

    Surface this as a clean 503 from billing routers rather than letting
    a stripe.error.AuthenticationError bubble up — the operator-facing
    message is more actionable.
    """


def _get_stripe_module() -> Any:
    # Lazy import so test environments that don't pip-install `stripe`
    # (because they never exercise the billing surface) keep working.
    import stripe  # noqa: PLC0415

    return stripe


@lru_cache(maxsize=1)
def get_stripe_client() -> Any:
    """Return the configured `stripe` module.

    Lru-cached so the api_key / api_version setup runs exactly once per
    process. Settings are read once at first call — a settings change
    requires a process restart, same as everywhere else in the app.
    """
    settings = get_settings()
    if not settings.stripe_secret_key:
        raise StripeNotConfigured(
            "STRIPE_SECRET_KEY is not set. Stripe-dependent endpoints are "
            "disabled. Set the env var and redeploy to enable billing."
        )

    stripe = _get_stripe_module()
    stripe.api_key = settings.stripe_secret_key
    # Pin the API version explicitly. Stripe defaults to whatever version
    # was current when the account was created; locking it here means a
    # Stripe-side breaking change can't surprise us between deploys.
    stripe.api_version = settings.stripe_api_version
    return stripe


def reset_stripe_client_cache() -> None:
    """Clear the lru_cache. Tests use this to swap configs between cases.

    Intentionally not part of the public __init__ — only test code should
    touch it.
    """
    get_stripe_client.cache_clear()
