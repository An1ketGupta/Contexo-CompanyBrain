"""Unit coverage for the LLM JSON parsers and recipient validation.

DB-bound flows (generate/schedule/cancel) need a live Supabase to test
end-to-end. The parsers and validators below have all the failure modes
that bite at runtime, so we cover them here.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("DEBUG", "false")

from app.services.sequences import _parse_steps_json, _validate_email
from app.services.internal_comms import _parse_drafts_json, _normalise_recipients


# ── Sequence JSON parsing ────────────────────────────────────────────────


def test_parse_steps_json_plain():
    text = """
    {"steps": [
        {"subject": "Hi", "body": "Intro"},
        {"subject": "FYI", "body": "Value-add"},
        {"subject": "Last note", "body": "Final nudge"}
    ]}
    """
    parsed = _parse_steps_json(text, expected=3)
    assert [p["subject"] for p in parsed] == ["Hi", "FYI", "Last note"]


def test_parse_steps_json_strips_code_fence():
    text = """Sure! Here's the plan:
```json
{"steps": [{"subject": "S", "body": "B"}, {"subject": "S2", "body": "B2"}]}
```
"""
    parsed = _parse_steps_json(text, expected=2)
    assert parsed[0]["subject"] == "S"
    assert parsed[1]["body"] == "B2"


def test_parse_steps_json_truncates_overlength_fields():
    long_body = "x" * 60000
    text = '{"steps": [{"subject": "S", "body": "%s"}]}' % long_body
    parsed = _parse_steps_json(text, expected=1)
    assert len(parsed[0]["body"]) == 50000


def test_parse_steps_json_rejects_too_few_steps():
    text = '{"steps": [{"subject": "S", "body": "B"}]}'
    with pytest.raises(RuntimeError, match="invalid_shape"):
        _parse_steps_json(text, expected=3)


def test_parse_steps_json_rejects_missing_fields():
    text = '{"steps": [{"subject": "", "body": "B"}, {"subject": "X", "body": "Y"}]}'
    with pytest.raises(RuntimeError, match="missing_step_fields"):
        _parse_steps_json(text, expected=2)


def test_parse_steps_json_rejects_garbage():
    with pytest.raises(RuntimeError, match="invalid_json"):
        _parse_steps_json("not json at all", expected=1)


# ── Email validation ─────────────────────────────────────────────────────


def test_validate_email_rejects_garbage():
    with pytest.raises(ValueError):
        _validate_email("not-an-email")
    with pytest.raises(ValueError):
        _validate_email("@nodomain.com")
    with pytest.raises(ValueError):
        _validate_email("")


def test_validate_email_accepts_normal_address():
    assert _validate_email("alex@example.com") == "alex@example.com"


def test_validate_email_strips_whitespace():
    assert _validate_email("  alex@example.com  ") == "alex@example.com"


# ── Internal Comms JSON parsing ──────────────────────────────────────────


def test_parse_drafts_json_plain():
    text = """{
        "email_subject": "Q3 recap",
        "email_body": "## Q3\\n\\nWe shipped...",
        "slack_body": "*Q3 recap*: we shipped...",
        "notion_title": "Q3 Recap",
        "notion_body": "# Q3\\n\\nWe shipped..."
    }"""
    parsed = _parse_drafts_json(text)
    assert parsed["email_subject"] == "Q3 recap"
    assert parsed["slack_body"].startswith("*Q3 recap*")


def test_parse_drafts_json_handles_code_fence():
    text = "```\n" + (
        '{"email_subject": "S", "email_body": "B", '
        '"slack_body": "SB", "notion_title": "NT", "notion_body": "NB"}'
    ) + "\n```"
    parsed = _parse_drafts_json(text)
    assert parsed["notion_title"] == "NT"


def test_parse_drafts_json_rejects_missing_field():
    text = '{"email_subject": "S", "email_body": "B", "slack_body": "X"}'
    with pytest.raises(RuntimeError, match="invalid_shape"):
        _parse_drafts_json(text)


def test_parse_drafts_json_rejects_garbage():
    with pytest.raises(RuntimeError, match="invalid_json"):
        _parse_drafts_json("not json")


# ── Recipient normalisation ──────────────────────────────────────────────


def test_normalise_recipients_dedupes_and_filters():
    recipients = [
        "alice@acme.com",
        "alice@acme.com",  # dup
        "bob@acme.com",
        "  charlie@acme.com  ",
        "",
        "invalid",
        None,  # type: ignore[list-item]
    ]
    out = _normalise_recipients(recipients)
    assert out == ["alice@acme.com", "bob@acme.com", "charlie@acme.com"]


def test_normalise_recipients_empty_returns_empty():
    assert _normalise_recipients(None) == []
    assert _normalise_recipients([]) == []
