"""Autoflow engine — unit-level coverage for the moving parts that aren't
DB-bound.

We deliberately avoid DB / Supabase mocks here. Three things are worth
testing without a live database:

  1. Pydantic validators — bad shapes must fail before they reach SQL.
  2. The templating substitution used to thread prior step outputs into
     subsequent action configs.
  3. Cron evaluation in scheduled_autoflows_due_now — pure logic given a
     known "now".

Integration tests against a live Supabase + Inngest deploy will live in
``tests/integration/`` once we have a staging stack to run them against.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

os.environ.setdefault("DEBUG", "false")

from app.models.autoflow import (
    MAX_ACTIONS_PER_AUTOFLOW,
    AutoflowAction,
    AutoflowActionType,
    AutoflowCreate,
    AutoflowTriggerConfig,
    AutoflowTriggerType,
)
from app.services.autoflow_actions import render_config


# ── Pydantic validation ──────────────────────────────────────────────────


def test_scheduled_trigger_requires_cron():
    with pytest.raises(ValueError, match="cron"):
        AutoflowCreate(
            name="Weekly digest",
            trigger_type=AutoflowTriggerType.SCHEDULED,
            trigger_config=AutoflowTriggerConfig(),  # no cron
            actions=[],
        )


def test_non_scheduled_trigger_rejects_cron():
    with pytest.raises(ValueError, match="only valid for trigger_type=scheduled"):
        AutoflowCreate(
            name="On upload",
            trigger_type=AutoflowTriggerType.DOCUMENT_UPLOADED,
            trigger_config=AutoflowTriggerConfig(cron="0 9 * * *"),
            actions=[],
        )


def test_malformed_cron_rejected():
    with pytest.raises(ValueError, match="5 space-separated"):
        AutoflowCreate(
            name="Weekly",
            trigger_type=AutoflowTriggerType.SCHEDULED,
            trigger_config=AutoflowTriggerConfig(cron="0 9"),  # only 2 fields
            actions=[],
        )


def test_action_cap_enforced():
    with pytest.raises(ValueError, match="action cap"):
        AutoflowCreate(
            name="Too big",
            trigger_type=AutoflowTriggerType.DOCUMENT_UPLOADED,
            actions=[
                AutoflowAction(type=AutoflowActionType.NOTIFY_ADMIN, order=i, config={})
                for i in range(MAX_ACTIONS_PER_AUTOFLOW + 1)
            ],
        )


def test_hold_for_approval_cannot_be_terminal():
    with pytest.raises(ValueError, match="hold_for_approval cannot be the final"):
        AutoflowCreate(
            name="Bad gate",
            trigger_type=AutoflowTriggerType.DOCUMENT_UPLOADED,
            actions=[
                AutoflowAction(type=AutoflowActionType.GENERATE_OUTPUT, order=0, config={}),
                AutoflowAction(type=AutoflowActionType.HOLD_FOR_APPROVAL, order=1, config={}),
            ],
        )


def test_confidence_threshold_range():
    with pytest.raises(ValueError):
        AutoflowCreate(
            name="Out of range",
            trigger_type=AutoflowTriggerType.DOCUMENT_UPLOADED,
            confidence_threshold=1.5,
            actions=[],
        )


def test_minimal_valid_create_round_trips():
    spec = AutoflowCreate(
        name="Welcome onboard",
        trigger_type=AutoflowTriggerType.EMPLOYEE_JOINED,
        actions=[
            AutoflowAction(
                type=AutoflowActionType.SEND_EMAIL,
                order=0,
                config={"to": "hr@acme.com", "subject": "New hire", "body": "Welcome!"},
            )
        ],
    )
    dumped = spec.model_dump()
    assert dumped["trigger_type"] == "employee_joined"
    assert dumped["actions"][0]["type"] == "send_email"


# ── Templating ───────────────────────────────────────────────────────────


def test_render_config_pulls_step_output():
    out = render_config(
        {"body": "Hello {{step_0.output.text}}"},
        prior_outputs={"0": {"text": "world"}},
        trigger_payload={},
    )
    assert out == {"body": "Hello world"}


def test_render_config_walks_nested_paths():
    out = render_config(
        {"to": "{{trigger.user.email}}", "ref": "{{step_0.output.message.id}}"},
        prior_outputs={"0": {"message": {"id": "msg_123"}}},
        trigger_payload={"user": {"email": "a@b.com"}},
    )
    assert out == {"to": "a@b.com", "ref": "msg_123"}


def test_render_config_missing_path_collapses_to_empty():
    # Conscious choice: a missing reference doesn't blow up the action — the
    # handler decides whether an empty string is OK (most require the field).
    out = render_config(
        {"body": "Hello {{step_99.output.missing}}"},
        prior_outputs={},
        trigger_payload={},
    )
    assert out == {"body": "Hello "}


def test_render_config_preserves_non_string_values():
    out = render_config(
        {"count": 42, "tags": ["a", "{{trigger.t}}"]},
        prior_outputs={},
        trigger_payload={"t": "b"},
    )
    assert out == {"count": 42, "tags": ["a", "b"]}


def test_render_config_serializes_object_outputs():
    out = render_config(
        {"body": "Sources: {{step_0.output.sources}}"},
        prior_outputs={"0": {"sources": [{"id": "1"}]}},
        trigger_payload={},
    )
    assert out["body"].startswith("Sources: [{")
    assert '"id":"1"' in out["body"]


# ── Cron evaluation ─────────────────────────────────────────────────────


def test_scheduled_cron_due_at_matching_minute():
    """A cron string of '30 9 * * *' fires at 09:30 UTC.
    Anchoring the matcher's 'now' at that exact minute should pick it up."""
    from app.services.autoflow_service import scheduled_autoflows_due_now

    fake_rows = [
        {
            "id": "af-1",
            "org_id": "org-1",
            "trigger_type": "scheduled",
            "is_active": True,
            "trigger_config": {"cron": "30 9 * * *"},
            "last_fired_at": None,
        }
    ]

    with patch(
        "app.services.autoflow_service.get_service_client"
    ) as mock_svc:
        mock_svc.return_value.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = fake_rows
        # 09:30 UTC — should match
        now = datetime(2026, 6, 24, 9, 30, 0, tzinfo=UTC)
        import asyncio

        due = asyncio.run(scheduled_autoflows_due_now(now=now))
        assert len(due) == 1
        # 09:31 — same row should NOT match
        now2 = datetime(2026, 6, 24, 9, 31, 0, tzinfo=UTC)
        due2 = asyncio.run(scheduled_autoflows_due_now(now=now2))
        assert len(due2) == 0


def test_scheduled_cron_dedupes_via_last_fired():
    """A row whose last_fired_at is < 55s ago must NOT re-fire even if the
    cron matches the current minute."""
    from app.services.autoflow_service import scheduled_autoflows_due_now

    now = datetime(2026, 6, 24, 9, 30, 30, tzinfo=UTC)
    recent_fire = datetime(2026, 6, 24, 9, 30, 0, tzinfo=UTC).isoformat()

    fake_rows = [
        {
            "id": "af-1",
            "org_id": "org-1",
            "trigger_type": "scheduled",
            "is_active": True,
            "trigger_config": {"cron": "30 9 * * *"},
            "last_fired_at": recent_fire,
        }
    ]

    with patch(
        "app.services.autoflow_service.get_service_client"
    ) as mock_svc:
        mock_svc.return_value.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = fake_rows
        import asyncio

        due = asyncio.run(scheduled_autoflows_due_now(now=now))
        assert due == []


def test_scheduled_cron_skips_bad_cron_strings():
    """A malformed cron must log + skip the row, not crash the matcher."""
    from app.services.autoflow_service import scheduled_autoflows_due_now

    fake_rows = [
        {
            "id": "af-bad",
            "org_id": "org-1",
            "trigger_type": "scheduled",
            "is_active": True,
            "trigger_config": {"cron": "not a cron"},
            "last_fired_at": None,
        },
        {
            "id": "af-good",
            "org_id": "org-1",
            "trigger_type": "scheduled",
            "is_active": True,
            "trigger_config": {"cron": "* * * * *"},  # every minute
            "last_fired_at": None,
        },
    ]

    with patch(
        "app.services.autoflow_service.get_service_client"
    ) as mock_svc:
        mock_svc.return_value.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = fake_rows
        import asyncio

        due = asyncio.run(scheduled_autoflows_due_now(now=datetime(2026, 6, 24, 9, 0, 0, tzinfo=UTC)))
        ids = [d["id"] for d in due]
        assert ids == ["af-good"]


# ── Trigger payload filtering ────────────────────────────────────────────


def test_payload_matches_filters_equality_and_list_membership():
    from app.services.autoflow_service import _payload_matches_filters

    assert _payload_matches_filters({"collection_id": "abc"}, {"collection_id": "abc"})
    assert not _payload_matches_filters({"collection_id": "abc"}, {"collection_id": "xyz"})
    # list filter = membership
    assert _payload_matches_filters(
        {"file_type": "pdf"}, {"file_type": ["pdf", "docx"]}
    )
    assert not _payload_matches_filters({"file_type": "txt"}, {"file_type": ["pdf", "docx"]})
    # No filters = always match
    assert _payload_matches_filters({"anything": 1}, {})
    # Missing payload field = no match (fail-closed)
    assert not _payload_matches_filters({}, {"collection_id": "abc"})
