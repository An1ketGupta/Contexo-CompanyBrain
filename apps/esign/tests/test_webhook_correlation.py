"""Regression tests for correlating a Documenso webhook back to our envelope.

The bug behind them: Documenso's webhook payload is still shaped around the
pre-v2 Document model, so `payload.id` is the legacy *numeric document* id
(16) while `payload.envelopeId` holds the v2 string id we actually persist in
`documenso_envelope_id`. The receiver read `id` first, matched nothing, and
answered `unknown_envelope` — with a 200, so Documenso recorded every delivery
as SUCCESS and never retried. Envelopes sat at 'sent' with HR shown as Pending
in the dashboard even though Documenso had them SIGNED.

The payload below is a trimmed copy of a real DOCUMENT_SIGNED delivery from the
local instance, keeping the keys the receiver reads.
"""
from __future__ import annotations

import pytest

from app.routers import webhooks

DOCUMENSO_ENVELOPE_ID = "envelope_yabkumsyadmxckrk"
LEGACY_DOCUMENT_ID = 16
HR_EMAIL = "guptaaniket600.ag@gmail.com"
CANDIDATE_EMAIL = "priya.sharma@example.in"


def _signed_payload() -> dict:
    return {
        "event": "DOCUMENT_SIGNED",
        "payload": {
            "id": LEGACY_DOCUMENT_ID,
            "envelopeId": DOCUMENSO_ENVELOPE_ID,
            "externalId": None,
            "title": "LOI — Aniket Gupta",
            "status": "PENDING",
            "recipients": [
                {
                    "id": 32,
                    "email": CANDIDATE_EMAIL,
                    "name": "Priya Sharma",
                    "signingStatus": "NOT_SIGNED",
                    "signingOrder": 2,
                },
                {
                    "id": 31,
                    "email": HR_EMAIL,
                    "name": "Aniket Gupta",
                    "signingStatus": "SIGNED",
                    "signingOrder": 1,
                },
            ],
        },
    }


def test_envelope_id_wins_over_legacy_numeric_id() -> None:
    ids = webhooks._correlation_ids(_signed_payload())
    assert ids[0] == DOCUMENSO_ENVELOPE_ID
    assert str(LEGACY_DOCUMENT_ID) in ids  # kept as a fallback, just not first


def test_correlation_ids_are_strings_and_deduped() -> None:
    ids = webhooks._correlation_ids(
        {"payload": {"id": "abc", "envelopeId": "abc", "documentId": 7}}
    )
    assert ids == ["abc", "7"]


def test_no_correlatable_id_yields_empty() -> None:
    assert webhooks._correlation_ids({"payload": {"title": "a template event"}}) == []


def test_recipients_read_from_either_casing() -> None:
    lower = webhooks._payload_recipients(_signed_payload())
    upper = webhooks._payload_recipients(
        {"payload": {"Recipient": [{"email": HR_EMAIL, "signingStatus": "SIGNED"}]}}
    )
    assert {r["email"] for r in lower} == {HR_EMAIL, CANDIDATE_EMAIL}
    assert upper[0]["email"] == HR_EMAIL


# ── Handler-level: the delivery that used to be dropped now advances HR ──────


class _FakeQuery:
    """Minimal stand-in for the supabase-py builder chain used by the receiver."""

    def __init__(self, store: dict, table: str) -> None:
        self._store = store
        self._table = table
        self._in: tuple[str, list[str]] | None = None
        self._update: dict | None = None

    def select(self, *_a, **_k) -> "_FakeQuery":
        return self

    def eq(self, *_a, **_k) -> "_FakeQuery":
        return self

    def in_(self, column: str, values: list[str]) -> "_FakeQuery":
        self._in = (column, values)
        return self

    def limit(self, *_a, **_k) -> "_FakeQuery":
        return self

    def update(self, values: dict) -> "_FakeQuery":
        self._update = values
        return self

    def insert(self, values: dict) -> "_FakeQuery":
        self._store.setdefault("inserted", []).append(values)
        return self

    def execute(self):
        if self._update is not None:
            self._store["row"].update(self._update)
            return type("R", (), {"data": [self._store["row"]]})()
        if self._in is not None:
            column, values = self._in
            row = self._store["row"]
            data = [row] if str(row.get(column)) in values else []
            return type("R", (), {"data": data})()
        return type("R", (), {"data": []})()


class _FakeClient:
    def __init__(self, store: dict) -> None:
        self._store = store

    def table(self, name: str) -> _FakeQuery:
        return _FakeQuery(self._store, name)


@pytest.fixture
def envelope_row() -> dict:
    return {
        "envelope_id": "397f2679-982d-4753-8504-3bc60bf5bf85",
        "org_id": "23db4170-7bce-429c-b70b-504ff6367753",
        "run_id": "20846b20-dfad-4ccb-ab8c-0d56188b5fe1",
        "provider": "documenso",
        "status": "sent",
        "documenso_envelope_id": DOCUMENSO_ENVELOPE_ID,
        "document_kinds": ["loi"],
        "signers": [
            {
                "role": "hr",
                "name": "Aniket Gupta",
                "email": HR_EMAIL,
                "status": "pending",
                "routing_order": 1,
                "public_token": "8b1c2d3e-0000-4000-8000-000000000001",
            },
            {
                "role": "candidate",
                "name": "Priya Sharma",
                "email": CANDIDATE_EMAIL,
                "status": "pending",
                "routing_order": 2,
                "public_token": "8b1c2d3e-0000-4000-8000-000000000002",
            },
        ],
        "events": [
            {
                "event": "envelope_created",
                "pdf_path": "orgs/x/onboarding/y/loi_hr_edit_r1.pdf",
                "completion_event": "onboarding_v2/loi_signed_uploaded",
            }
        ],
    }


async def test_hr_signature_marks_signer_complete_and_hands_off(
    monkeypatch: pytest.MonkeyPatch, envelope_row: dict
) -> None:
    store = {"row": envelope_row}
    sent: list[tuple[str, dict]] = []

    async def _capture(name: str, data: dict) -> None:
        sent.append((name, data))

    monkeypatch.setattr(webhooks, "get_service_client", lambda: _FakeClient(store))
    monkeypatch.setattr(webhooks, "send_event", _capture)

    class _Req:
        async def body(self) -> bytes:
            return b"{}"

        async def json(self) -> dict:
            return _signed_payload()

        headers = {"x-documenso-secret": "s3cret"}

    monkeypatch.setattr(webhooks, "_verify", lambda *_a, **_k: None)

    result = await webhooks.documenso_webhook(_Req())

    assert result == {"status": "in_progress"}
    statuses = {s["role"]: s["status"] for s in store["row"]["signers"]}
    assert statuses == {"hr": "completed", "candidate": "pending"}

    # HR finished and the candidate is next → apps/api is told to email them.
    assert [name for name, _ in sent] == ["esign/signer_turn"]
    assert sent[0][1]["signer_email"] == CANDIDATE_EMAIL
    assert sent[0][1]["signing_url"].endswith(
        "/sign/8b1c2d3e-0000-4000-8000-000000000002"
    )


async def test_unknown_envelope_still_acks(monkeypatch: pytest.MonkeyPatch, envelope_row: dict) -> None:
    envelope_row["documenso_envelope_id"] = "envelope_somethingelse"
    monkeypatch.setattr(webhooks, "get_service_client", lambda: _FakeClient({"row": envelope_row}))
    monkeypatch.setattr(webhooks, "_verify", lambda *_a, **_k: None)

    class _Req:
        async def body(self) -> bytes:
            return b"{}"

        async def json(self) -> dict:
            return _signed_payload()

        headers: dict[str, str] = {}

    assert await webhooks.documenso_webhook(_Req()) == {"status": "unknown_envelope"}
