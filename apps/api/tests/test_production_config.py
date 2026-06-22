"""Tests for the production-config startup validator."""
from __future__ import annotations

import pytest

from app.config import (
    ProductionConfigError,
    Settings,
    validate_production_config,
)


def _make_settings(**overrides) -> Settings:
    defaults = dict(
        environment="production",
        supabase_url="https://example.supabase.co",
        supabase_anon_key="anon",
        supabase_service_role_key="service",
        gemini_api_key="g",
        oauth_state_secret="s",
        inngest_signing_key="signkey-test",
        upstash_redis_rest_url="https://example.upstash.io",
        upstash_redis_rest_token="upstash-token",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def test_dev_environment_skips_validation() -> None:
    settings = _make_settings(
        environment="development",
        gemini_api_key="",
        oauth_state_secret="",
        inngest_signing_key="",
        upstash_redis_rest_url="",
        upstash_redis_rest_token="",
    )
    validate_production_config(settings)  # no raise


def test_production_with_all_required_passes() -> None:
    validate_production_config(_make_settings())


@pytest.mark.parametrize(
    "missing_attr,expected_env_name",
    [
        ("gemini_api_key", "GEMINI_API_KEY"),
        ("oauth_state_secret", "OAUTH_STATE_SECRET"),
        ("inngest_signing_key", "INNGEST_SIGNING_KEY"),
        ("upstash_redis_rest_url", "UPSTASH_REDIS_REST_URL"),
        ("upstash_redis_rest_token", "UPSTASH_REDIS_REST_TOKEN"),
    ],
)
def test_production_raises_when_required_missing(
    missing_attr: str, expected_env_name: str
) -> None:
    settings = _make_settings(**{missing_attr: ""})
    with pytest.raises(ProductionConfigError) as excinfo:
        validate_production_config(settings)
    assert expected_env_name in str(excinfo.value)


def test_error_lists_every_missing_var_in_one_message() -> None:
    settings = _make_settings(gemini_api_key="", oauth_state_secret="")
    with pytest.raises(ProductionConfigError) as excinfo:
        validate_production_config(settings)
    msg = str(excinfo.value)
    assert "GEMINI_API_KEY" in msg
    assert "OAUTH_STATE_SECRET" in msg
