"""Agent registry validator tests (Agent Day 14).

Pure validators in `app.services.agent_registry` — input shape checks,
email/date coercion, idempotency-key derivation. The async resolvers
(`resolve_approver_user_id`, `resolve_document_id_by_name`) talk to
Supabase and are exercised in integration tests; the synchronous surface
is what protects us against bad payloads reaching the dispatchers.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("DEBUG", "false")

from app.services.agent_registry import (
    AGENT_REGISTRY,
    AgentInputError,
    agent_registry_public_view,
    normalize_idempotency_key,
    run_id_from_idempotency_key,
    validate_agent_input,
)


# ── Registry shape invariants ──────────────────────────────────────────────


def test_registry_advertises_all_four_agent_types():
    assert set(AGENT_REGISTRY.keys()) == {
        "onboarding",
        "policy_propagation",
        "support_response",
        "weekly_digest",
    }


def test_public_view_includes_required_fields():
    view = agent_registry_public_view()
    assert len(view) == len(AGENT_REGISTRY)
    for entry in view:
        assert "agent_type" in entry
        assert "description" in entry
        assert "estimated_seconds" in entry
        schema = entry["input_schema"]
        assert isinstance(schema["required"], list)
        assert isinstance(schema["optional"], list)


# ── validate_agent_input — common path ─────────────────────────────────────


def test_validate_rejects_unknown_agent_type():
    with pytest.raises(AgentInputError, match="Unknown agent type"):
        validate_agent_input("not_a_real_agent", {"any": "thing"})


def test_validate_requires_dict_input():
    with pytest.raises(AgentInputError, match="input must be an object"):
        validate_agent_input("onboarding", "not a dict")  # type: ignore[arg-type]


def test_validate_strips_unknown_keys():
    out = validate_agent_input(
        "onboarding",
        {
            "name": "Sarah",
            "email": "sarah@acme.com",
            "role": "Designer",
            "start_date": "2026-07-01",
            "favourite_colour": "blue",  # unknown
            "salary": 123_000,  # unknown
        },
    )
    assert "favourite_colour" not in out
    assert "salary" not in out


def test_validate_lowercases_email_fields_on_onboarding():
    out = validate_agent_input(
        "onboarding",
        {
            "name": "S",
            "email": "Sarah@Acme.com",
            "role": "Designer",
            "start_date": "2026-07-01",
            "manager_email": "JOHN@ACME.com",
        },
    )
    assert out["email"] == "sarah@acme.com"
    assert out["manager_email"] == "john@acme.com"


def test_validate_rejects_malformed_email_on_onboarding():
    with pytest.raises(AgentInputError, match="not a valid email"):
        validate_agent_input(
            "onboarding",
            {
                "name": "S",
                "email": "not-an-email",
                "role": "Designer",
                "start_date": "2026-07-01",
            },
        )


def test_validate_rejects_non_iso_date():
    with pytest.raises(AgentInputError, match="ISO-8601"):
        validate_agent_input(
            "onboarding",
            {
                "name": "S",
                "email": "s@a.com",
                "role": "Designer",
                "start_date": "July 1, 2026",
            },
        )


def test_validate_accepts_iso_date_with_time_component():
    out = validate_agent_input(
        "onboarding",
        {
            "name": "S",
            "email": "s@a.com",
            "role": "Designer",
            "start_date": "2026-07-01T09:00:00Z",
        },
    )
    assert out["start_date"].startswith("2026-07-01")


# ── policy_propagation: document_id OR document_name ───────────────────────


def test_validate_policy_propagation_requires_id_or_name():
    with pytest.raises(AgentInputError, match="document_id"):
        validate_agent_input("policy_propagation", {})


def test_validate_policy_propagation_accepts_document_id_alone():
    out = validate_agent_input(
        "policy_propagation", {"document_id": "abc-123"},
    )
    assert out["document_id"] == "abc-123"


def test_validate_policy_propagation_accepts_document_name_alone():
    out = validate_agent_input(
        "policy_propagation", {"document_name": "Refund Policy"},
    )
    assert out["document_name"] == "Refund Policy"


# ── support_response ───────────────────────────────────────────────────────


def test_validate_support_response_required_fields():
    with pytest.raises(AgentInputError, match="from_email"):
        validate_agent_input(
            "support_response", {"subject": "x", "body": "y"},
        )


def test_validate_support_response_lowercases_from_email():
    out = validate_agent_input(
        "support_response",
        {
            "from_email": "Customer@Example.COM",
            "subject": "Refund",
            "body": "Please refund",
        },
    )
    assert out["from_email"] == "customer@example.com"


# ── weekly_digest ──────────────────────────────────────────────────────────


def test_validate_weekly_digest_no_required_fields():
    out = validate_agent_input("weekly_digest", {})
    assert out == {}


def test_validate_weekly_digest_validates_send_to_email():
    with pytest.raises(AgentInputError, match="not a valid email"):
        validate_agent_input("weekly_digest", {"send_to_email": "not-an-email"})


# ── Idempotency-Key derivation ─────────────────────────────────────────────


def test_normalize_idempotency_key_strips_and_caps():
    assert normalize_idempotency_key("  hello  ") == "hello"
    assert normalize_idempotency_key("") is None
    assert normalize_idempotency_key("   ") is None
    assert normalize_idempotency_key(None) is None
    long = "x" * 200
    out = normalize_idempotency_key(long)
    assert out is not None and len(out) == 128


def test_run_id_from_idempotency_key_is_stable():
    """Same (api_key, agent, key) → identical UUID. This is what makes
    BambooHR retries collapse into one agent_runs row."""
    a = run_id_from_idempotency_key(
        api_key_id="key-1", agent_type="onboarding", idem_key="evt-42",
    )
    b = run_id_from_idempotency_key(
        api_key_id="key-1", agent_type="onboarding", idem_key="evt-42",
    )
    assert a == b


def test_run_id_from_idempotency_key_differs_on_any_axis():
    base = run_id_from_idempotency_key(
        api_key_id="key-1", agent_type="onboarding", idem_key="evt-42",
    )
    assert base != run_id_from_idempotency_key(
        api_key_id="key-2", agent_type="onboarding", idem_key="evt-42",
    )
    assert base != run_id_from_idempotency_key(
        api_key_id="key-1", agent_type="support_response", idem_key="evt-42",
    )
    assert base != run_id_from_idempotency_key(
        api_key_id="key-1", agent_type="onboarding", idem_key="evt-43",
    )
