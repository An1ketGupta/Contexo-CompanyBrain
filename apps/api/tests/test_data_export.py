"""Unit tests for the GDPR data export builders.

Exercises:
  * Personal export builds a valid ZIP with the expected file list.
  * Empty-state user (no conversations, no documents) still produces a
    valid archive — caught the "first-day signup downloads their data"
    regression case from the Day 12 hardening checklist.
  * Sensitive columns (access_token, refresh_token, webhook_secret) are
    NEVER selected from the integrations table. This is the single
    most important invariant in the module — a regression here would
    leak OAuth credentials into every user's downloaded archive.
  * Org export bundles the cross-user data the admin needs and omits
    Stripe customer/subscription IDs from billing.json.
"""
from __future__ import annotations

import io
import json
import zipfile
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ── Mock Supabase client plumbing ─────────────────────────────────────────────


class _FakeQuery:
    """Mimics the fluent Supabase REST builder enough that the export
    module can ``.select(...).eq(...).order(...).limit(...).execute()``
    against it without us caring which combination it picks.

    The constructor takes the rows the eventual ``.execute()`` should
    return; every other builder method is a self-return no-op."""

    def __init__(self, rows: list[dict[str, Any]] | None) -> None:
        self._rows = rows or []
        self.last_select: str | None = None

    def select(self, columns: str = "*") -> "_FakeQuery":
        self.last_select = columns
        return self

    def eq(self, *_args: Any, **_kwargs: Any) -> "_FakeQuery":
        return self

    def in_(self, *_args: Any, **_kwargs: Any) -> "_FakeQuery":
        return self

    def order(self, *_args: Any, **_kwargs: Any) -> "_FakeQuery":
        return self

    def limit(self, *_args: Any, **_kwargs: Any) -> "_FakeQuery":
        return self

    def maybe_single(self) -> "_FakeQuery":
        return self

    def execute(self) -> MagicMock:
        result = MagicMock()
        result.data = list(self._rows)
        return result


class _FakeClient:
    """Routes ``.table("foo")`` calls to whichever rowset the test set up."""

    def __init__(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        self._tables = tables
        self.select_log: list[tuple[str, str]] = []

    def table(self, name: str) -> _FakeQuery:
        rows = self._tables.get(name, [])
        query = _FakeQuery(rows)

        # Spy on the column list so tests can assert the integrations
        # query never names an access_token-shaped column.
        original_select = query.select

        def _record_select(columns: str = "*") -> _FakeQuery:
            self.select_log.append((name, columns))
            return original_select(columns)

        query.select = _record_select  # type: ignore[method-assign]
        return query


# ── Personal export ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_user_export_builds_valid_zip_with_expected_files() -> None:
    from app.services.data_export import build_user_export

    tables: dict[str, list[dict[str, Any]]] = {
        "users": [
            {
                "id": "user-1",
                "org_id": "org-1",
                "role": "member",
                "display_name": "Test User",
                "role_title": "Engineer",
                "competitor_names": ["AcmeCo"],
                "activity_private": False,
                "created_at": "2026-06-01T00:00:00Z",
            }
        ],
        "conversations": [
            {"id": "conv-1", "user_id": "user-1", "title": "Test convo"}
        ],
        "messages": [
            {
                "id": "msg-1",
                "conversation_id": "conv-1",
                "role": "user",
                "content": "hello",
                "feedback": "positive",
                "created_at": "2026-06-01T00:01:00Z",
            }
        ],
        "query_logs": [{"id": "q-1", "query_text": "what is X"}],
        "documents": [{"id": "doc-1", "name": "handbook.pdf"}],
        "integrations": [
            {
                "provider": "drive",
                "scope_user_id": "user-1",
                "scopes": ["drive.file"],
                "metadata": {"email": "u@example.com"},
                "last_synced_at": "2026-06-01T00:00:00Z",
                "created_at": "2026-05-01T00:00:00Z",
                "updated_at": "2026-06-01T00:00:00Z",
            }
        ],
        "gmail_integrations": [
            {
                "email_address": "u@example.com",
                "has_send_scope": True,
                "created_at": "2026-05-01T00:00:00Z",
                "updated_at": "2026-06-01T00:00:00Z",
            }
        ],
        "notifications": [],
        "compliance_acknowledgements": [],
    }

    fake = _FakeClient(tables)
    with patch("app.services.data_export.get_user_client", return_value=fake):
        payload = await build_user_export(
            user_id="user-1",
            org_id="org-1",
            token="fake-jwt",
            email="u@example.com",
        )

    # Archive must be a real ZIP, not a half-written buffer.
    buf = io.BytesIO(payload)
    with zipfile.ZipFile(buf, "r") as zf:
        names = set(zf.namelist())
        assert "README.txt" in names
        assert "profile.json" in names
        assert "conversations.json" in names
        assert "messages.json" in names
        assert "query_logs.json" in names
        assert "documents_metadata.json" in names
        assert "integrations.json" in names
        assert "feedback.json" in names
        assert "notifications.json" in names
        assert "compliance_acknowledgements.json" in names
        assert "competitor_watchlist.json" in names

        profile = json.loads(zf.read("profile.json"))
        assert profile["id"] == "user-1"
        assert profile["email"] == "u@example.com"

        feedback = json.loads(zf.read("feedback.json"))
        assert len(feedback) == 1
        assert feedback[0]["feedback"] == "positive"

        integrations = json.loads(zf.read("integrations.json"))
        assert integrations["credentials_excluded"] is True


@pytest.mark.asyncio
async def test_user_export_handles_empty_user() -> None:
    """A brand-new account with nothing in any table must still produce
    a valid ZIP rather than 500-ing. Edge case from the Day 12 checklist."""
    from app.services.data_export import build_user_export

    fake = _FakeClient({})
    with patch("app.services.data_export.get_user_client", return_value=fake):
        payload = await build_user_export(
            user_id="user-1",
            org_id="org-1",
            token="fake-jwt",
            email="empty@example.com",
        )

    buf = io.BytesIO(payload)
    with zipfile.ZipFile(buf, "r") as zf:
        # Every expected slice exists, even when empty.
        assert "messages.json" in zf.namelist()
        assert json.loads(zf.read("messages.json")) == []
        assert json.loads(zf.read("conversations.json")) == []
        # Profile is present with a null record rather than crashing.
        profile = json.loads(zf.read("profile.json"))
        assert profile["record"] is None


@pytest.mark.asyncio
async def test_user_export_never_selects_oauth_token_columns() -> None:
    """The single most important invariant of this module: we must
    never name access_token / refresh_token / webhook_secret in any
    SELECT against the integrations table. A regression here would
    leak OAuth credentials into every downloaded archive.
    """
    from app.services.data_export import build_user_export

    fake = _FakeClient({})
    with patch("app.services.data_export.get_user_client", return_value=fake):
        await build_user_export(
            user_id="user-1", org_id="org-1", token="fake-jwt", email=None
        )

    integration_selects = [
        cols for table, cols in fake.select_log if table == "integrations"
    ]
    assert integration_selects, "expected at least one integrations select"
    for cols in integration_selects:
        lowered = cols.lower()
        assert "access_token" not in lowered
        assert "refresh_token" not in lowered
        assert "webhook_secret" not in lowered


# ── Org export ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_org_export_omits_stripe_ids_from_billing_section() -> None:
    """The org-wide export goes to admins, but may be forwarded outside
    the company. Stripe customer/subscription IDs aren't secrets per se
    but are useful for impersonation reconnaissance; keep them out.

    Two invariants checked:
      1. The SELECT projection on `organizations` doesn't ask for Stripe
         columns. This is the authoritative defence — even if a future
         refactor added the column to `billing.json` by accident, the
         column wouldn't be in the source dict to begin with.
      2. `billing.json` only contains the four documented fields, not a
         wholesale dump of the org row.
    """
    from app.services.data_export import build_org_export

    # Only the columns the code actually selects. Realistic mock for the
    # `organizations` projection; Stripe IDs are kept out at the source.
    org_row = {
        "id": "org-1",
        "name": "Acme",
        "slug": "acme",
        "plan": "team",
        "plan_status": "active",
        "current_period_end": "2026-07-01T00:00:00Z",
        "cancel_at_period_end": False,
    }

    fake = _FakeClient({"organizations": [org_row]})
    with patch("app.services.data_export.get_service_client", return_value=fake):
        payload = await build_org_export(org_id="org-1", requested_by="user-1")

    # 1. SELECT projection must not name the Stripe columns.
    org_selects = [
        cols for table, cols in fake.select_log if table == "organizations"
    ]
    assert org_selects, "expected at least one organizations select"
    for cols in org_selects:
        assert "stripe_customer_id" not in cols
        assert "stripe_subscription_id" not in cols

    # 2. billing.json carries the documented fields and nothing else.
    with zipfile.ZipFile(io.BytesIO(payload), "r") as zf:
        billing = json.loads(zf.read("billing.json"))
        assert billing["plan"] == "team"
        assert set(billing.keys()) == {
            "plan",
            "plan_status",
            "current_period_end",
            "cancel_at_period_end",
        }
