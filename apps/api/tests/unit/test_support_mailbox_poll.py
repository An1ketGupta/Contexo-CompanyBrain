"""Tests for the support-inbox reader (services/integrations/gmail.py) and the
scope split between the per-user send flow and the org-level support mailbox.

The History-API cursor is the part that can silently lose or duplicate
customer email, so most of these pin down cursor behaviour: bootstrap on first
poll, resume-where-you-stopped on a truncated batch, and re-bootstrap when
Google has aged out the cursor. Gmail HTTP calls are stubbed at
`_gmail_get` — no network, no DB.
"""
from __future__ import annotations

import base64
from typing import Any

import pytest

from app.services.integrations import gmail as gmail_svc
from app.services.integrations import support_mailbox as support_mailbox_svc


def _b64(text: str) -> str:
    """Gmail returns base64url with padding stripped."""
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _message(
    *,
    message_id: str = "m1",
    from_raw: str = "Jane Doe <jane@example.com>",
    subject: str = "Can't log in",
    plain: str | None = "I reset my password but still can't log in.",
    html: str | None = None,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    """A users.messages.get?format=full response."""
    parts: list[dict[str, Any]] = []
    if plain is not None:
        parts.append({"mimeType": "text/plain", "body": {"data": _b64(plain)}})
    if html is not None:
        parts.append({"mimeType": "text/html", "body": {"data": _b64(html)}})
    return {
        "id": message_id,
        "labelIds": labels if labels is not None else ["INBOX", "UNREAD"],
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": from_raw},
                {"name": "Subject", "value": subject},
                {"name": "To", "value": "support@acme.com"},
            ],
            "parts": parts,
        },
    }


def _history_page(
    *,
    entries: list[tuple[str, str]],
    next_page_token: str | None = None,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    """entries = [(history_record_id, message_id), ...]"""
    page: dict[str, Any] = {
        "history": [
            {
                "id": record_id,
                "messagesAdded": [
                    {
                        "message": {
                            "id": message_id,
                            "labelIds": labels if labels is not None else ["INBOX"],
                        }
                    }
                ],
            }
            for record_id, message_id in entries
        ]
    }
    if next_page_token:
        page["nextPageToken"] = next_page_token
    return page


class FakeGmail:
    """Stands in for `_gmail_get`, recording every call it serves."""

    def __init__(
        self,
        *,
        profile_history_id: str = "9999",
        history_pages: list[dict[str, Any]] | None = None,
        messages: dict[str, dict[str, Any]] | None = None,
        history_raises: Exception | None = None,
        message_raises: dict[str, Exception] | None = None,
    ) -> None:
        self.profile_history_id = profile_history_id
        self.history_pages = history_pages or []
        self.messages = messages or {}
        self.history_raises = history_raises
        self.message_raises = message_raises or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._history_index = 0

    async def __call__(
        self, *, access_token: str, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.calls.append((path, params or {}))
        if path == "/profile":
            return {"historyId": self.profile_history_id}
        if path == "/history":
            if self.history_raises:
                raise self.history_raises
            page = self.history_pages[self._history_index]
            self._history_index += 1
            return page
        if path.startswith("/messages/"):
            message_id = path.rsplit("/", 1)[-1]
            if message_id in self.message_raises:
                raise self.message_raises[message_id]
            return self.messages[message_id]
        raise AssertionError(f"unexpected Gmail path: {path}")

    def paths(self) -> list[str]:
        return [path for path, _ in self.calls]


@pytest.fixture()
def fake_gmail(monkeypatch):
    """Installs a FakeGmail as the module's HTTP layer."""

    def _install(**kwargs: Any) -> FakeGmail:
        fake = FakeGmail(**kwargs)
        monkeypatch.setattr(gmail_svc, "_gmail_get", fake)
        return fake

    return _install


# ── Scope gating ───────────────────────────────────────────────────────────


class TestScopeChecks:
    def test_read_scope_absent_on_send_only_connection(self):
        assert gmail_svc.has_read_scope([gmail_svc.GMAIL_SEND_SCOPE]) is False

    def test_read_scope_present_when_granted(self):
        assert (
            gmail_svc.has_read_scope(
                [gmail_svc.GMAIL_SEND_SCOPE, gmail_svc.GMAIL_READONLY_SCOPE]
            )
            is True
        )

    def test_read_scope_handles_missing_scopes(self):
        assert gmail_svc.has_read_scope(None) is False
        assert gmail_svc.has_read_scope([]) is False

    def test_per_user_flow_stays_send_only(self):
        # Reading an inbox is the support mailbox's job. Asking every teammate
        # for read access to their personal mail would be indefensible.
        assert gmail_svc.GMAIL_SEND_SCOPE in gmail_svc._SCOPES
        assert gmail_svc.GMAIL_READONLY_SCOPE not in gmail_svc._SCOPES

    def test_support_mailbox_requests_both_scopes(self):
        # It has to read (to poll) and send (so replies come from the address
        # the customer wrote to).
        assert gmail_svc.GMAIL_READONLY_SCOPE in support_mailbox_svc._SCOPES
        assert gmail_svc.GMAIL_SEND_SCOPE in support_mailbox_svc._SCOPES


# ── Body extraction ────────────────────────────────────────────────────────


class TestExtractBody:
    def test_prefers_text_plain(self):
        payload = _message(plain="plain wins", html="<p>html loses</p>")["payload"]
        assert gmail_svc._extract_body(payload) == "plain wins"

    def test_falls_back_to_stripped_html(self):
        payload = _message(plain=None, html="<p>Hello <b>there</b></p>")["payload"]
        body = gmail_svc._extract_body(payload)
        assert "Hello" in body and "there" in body
        assert "<" not in body

    def test_walks_nested_multipart(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": _b64("nested body")}}
                    ],
                }
            ],
        }
        assert gmail_svc._extract_body(payload) == "nested body"

    def test_ignores_attachments(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64("real body")}},
                {
                    "mimeType": "text/plain",
                    "filename": "log.txt",
                    "body": {"data": _b64("attachment text")},
                },
            ],
        }
        assert gmail_svc._extract_body(payload) == "real body"

    def test_handles_single_part_message(self):
        payload = {"mimeType": "text/plain", "body": {"data": _b64("just this")}}
        assert gmail_svc._extract_body(payload) == "just this"

    def test_empty_payload_is_empty_string(self):
        assert gmail_svc._extract_body({}) == ""

    def test_decodes_unpadded_base64url(self):
        # Gmail strips '=' padding; a naive b64decode would raise here.
        payload = {"mimeType": "text/plain", "body": {"data": _b64("abcde")}}
        assert gmail_svc._extract_body(payload) == "abcde"


class TestHeaderLookup:
    def test_case_insensitive(self):
        headers = [{"name": "SUBJECT", "value": "hi"}]
        assert gmail_svc._header(headers, "Subject") == "hi"

    def test_missing_header_is_empty(self):
        assert gmail_svc._header([], "Subject") == ""
        assert gmail_svc._header(None, "From") == ""


# ── Ticketability filter ───────────────────────────────────────────────────


class TestIsTicketable:
    def test_accepts_ordinary_customer_mail(self):
        envelope = {"from_email": "jane@example.com", "body": "help me"}
        assert (
            gmail_svc._is_ticketable(envelope, mailbox_email="support@acme.com") is True
        )

    def test_rejects_mail_from_the_mailbox_itself(self):
        # Our own outbound reply landing back in the inbox must not open a
        # ticket about itself.
        envelope = {"from_email": "support@acme.com", "body": "our reply"}
        assert (
            gmail_svc._is_ticketable(envelope, mailbox_email="Support@Acme.com") is False
        )

    def test_rejects_empty_body(self):
        envelope = {"from_email": "jane@example.com", "body": "   "}
        assert (
            gmail_svc._is_ticketable(envelope, mailbox_email="support@acme.com") is False
        )

    def test_rejects_missing_sender(self):
        envelope = {"from_email": "", "body": "help me"}
        assert (
            gmail_svc._is_ticketable(envelope, mailbox_email="support@acme.com") is False
        )


# ── Cursor behaviour ───────────────────────────────────────────────────────


class TestBootstrap:
    async def test_first_poll_records_cursor_and_ingests_nothing(self, fake_gmail):
        """Connecting an inbox must not turn its back catalogue into tickets."""
        fake = fake_gmail(profile_history_id="5000")

        envelopes, cursor = await gmail_svc.list_new_inbox_messages(
            access_token="tok", mailbox_email="support@acme.com", since_history_id=None
        )

        assert envelopes == []
        assert cursor == "5000"
        assert fake.paths() == ["/profile"]

    async def test_expired_cursor_rebootstraps(self, fake_gmail):
        """A cursor older than Gmail's history retention 404s. We reset rather
        than crash-loop, forfeiting the gap instead of replaying everything."""
        fake = fake_gmail(
            profile_history_id="8000",
            history_raises=gmail_svc.GmailNotFound("gmail 404 on /history"),
        )

        envelopes, cursor = await gmail_svc.list_new_inbox_messages(
            access_token="tok", mailbox_email="support@acme.com", since_history_id="1"
        )

        assert envelopes == []
        assert cursor == "8000"
        assert "/history" in fake.paths()
        assert "/profile" in fake.paths()


class TestDeltaRead:
    async def test_returns_envelope_for_new_message(self, fake_gmail):
        fake_gmail(
            history_pages=[_history_page(entries=[("1010", "m1")])],
            messages={"m1": _message(message_id="m1")},
        )

        envelopes, cursor = await gmail_svc.list_new_inbox_messages(
            access_token="tok",
            mailbox_email="support@acme.com",
            since_history_id="1000",
        )

        assert cursor == "1010"
        assert len(envelopes) == 1
        assert envelopes[0]["from_email"] == "jane@example.com"
        assert envelopes[0]["from_raw"] == "Jane Doe <jane@example.com>"
        assert envelopes[0]["subject"] == "Can't log in"
        assert "reset my password" in envelopes[0]["body"]

    async def test_walks_paginated_history(self, fake_gmail):
        fake_gmail(
            history_pages=[
                _history_page(entries=[("1010", "m1")], next_page_token="p2"),
                _history_page(entries=[("1020", "m2")]),
            ],
            messages={
                "m1": _message(message_id="m1"),
                "m2": _message(message_id="m2", subject="Refund please"),
            },
        )

        envelopes, cursor = await gmail_svc.list_new_inbox_messages(
            access_token="tok",
            mailbox_email="support@acme.com",
            since_history_id="1000",
        )

        assert [e["message_id"] for e in envelopes] == ["m1", "m2"]
        assert cursor == "1020"

    async def test_stops_at_page_cap(self, fake_gmail):
        """A mailbox with a huge backlog must not spin one org's poll forever."""
        pages = [
            _history_page(entries=[(f"10{i}0", f"m{i}")], next_page_token=f"p{i + 1}")
            for i in range(gmail_svc._MAX_HISTORY_PAGES + 3)
        ]
        fake = fake_gmail(
            history_pages=pages,
            messages={f"m{i}": _message(message_id=f"m{i}") for i in range(20)},
        )

        await gmail_svc.list_new_inbox_messages(
            access_token="tok",
            mailbox_email="support@acme.com",
            since_history_id="1000",
        )

        assert fake.paths().count("/history") == gmail_svc._MAX_HISTORY_PAGES

    async def test_dedupes_message_repeated_across_records(self, fake_gmail):
        fake_gmail(
            history_pages=[_history_page(entries=[("1010", "m1"), ("1020", "m1")])],
            messages={"m1": _message(message_id="m1")},
        )

        envelopes, _ = await gmail_svc.list_new_inbox_messages(
            access_token="tok",
            mailbox_email="support@acme.com",
            since_history_id="1000",
        )

        assert len(envelopes) == 1

    async def test_skips_excluded_labels_from_history(self, fake_gmail):
        fake = fake_gmail(
            history_pages=[
                _history_page(entries=[("1010", "m1")], labels=["SENT"]),
            ],
            messages={},
        )

        envelopes, cursor = await gmail_svc.list_new_inbox_messages(
            access_token="tok",
            mailbox_email="support@acme.com",
            since_history_id="1000",
        )

        assert envelopes == []
        # Never even fetched — the label check happens on the history record.
        assert not any(p.startswith("/messages/") for p in fake.paths())
        assert cursor == "1000"

    async def test_skips_spam_discovered_on_fetch(self, fake_gmail):
        """Labels can change between the history read and the fetch."""
        fake_gmail(
            history_pages=[_history_page(entries=[("1010", "m1")])],
            messages={"m1": _message(message_id="m1", labels=["SPAM"])},
        )

        envelopes, cursor = await gmail_svc.list_new_inbox_messages(
            access_token="tok",
            mailbox_email="support@acme.com",
            since_history_id="1000",
        )

        assert envelopes == []
        # Cursor still advances — we saw the message and decided against it.
        assert cursor == "1010"

    async def test_skips_message_deleted_before_fetch(self, fake_gmail):
        fake_gmail(
            history_pages=[_history_page(entries=[("1010", "m1"), ("1020", "m2")])],
            messages={"m2": _message(message_id="m2")},
            message_raises={"m1": gmail_svc.GmailNotFound("gone")},
        )

        envelopes, cursor = await gmail_svc.list_new_inbox_messages(
            access_token="tok",
            mailbox_email="support@acme.com",
            since_history_id="1000",
        )

        assert [e["message_id"] for e in envelopes] == ["m2"]
        assert cursor == "1020"

    async def test_drops_own_outbound_reply(self, fake_gmail):
        fake_gmail(
            history_pages=[_history_page(entries=[("1010", "m1")])],
            messages={
                "m1": _message(
                    message_id="m1", from_raw="Support <support@acme.com>"
                )
            },
        )

        envelopes, _ = await gmail_svc.list_new_inbox_messages(
            access_token="tok",
            mailbox_email="support@acme.com",
            since_history_id="1000",
        )

        assert envelopes == []


class TestCursorAdvancesOnlyPastProcessedMail:
    async def test_batch_over_cap_resumes_next_tick(self, fake_gmail, monkeypatch):
        """The cap must defer the remainder, not drop it — so the cursor stops
        at the last message actually handled."""
        monkeypatch.setattr(gmail_svc, "_MAX_MESSAGES_PER_POLL", 2)
        fake_gmail(
            history_pages=[
                _history_page(
                    entries=[("1010", "m1"), ("1020", "m2"), ("1030", "m3")]
                )
            ],
            messages={f"m{i}": _message(message_id=f"m{i}") for i in (1, 2, 3)},
        )

        envelopes, cursor = await gmail_svc.list_new_inbox_messages(
            access_token="tok",
            mailbox_email="support@acme.com",
            since_history_id="1000",
        )

        assert [e["message_id"] for e in envelopes] == ["m1", "m2"]
        # m3 is untouched, so the cursor must not move past m2.
        assert cursor == "1020"

    async def test_transient_fetch_failure_holds_cursor(self, fake_gmail):
        """An API hiccup mid-batch must leave the failed message re-readable,
        not silently skip it."""
        fake_gmail(
            history_pages=[
                _history_page(entries=[("1010", "m1"), ("1020", "m2"), ("1030", "m3")])
            ],
            messages={
                "m1": _message(message_id="m1"),
                "m3": _message(message_id="m3"),
            },
            message_raises={"m2": RuntimeError("gmail 500")},
        )

        envelopes, cursor = await gmail_svc.list_new_inbox_messages(
            access_token="tok",
            mailbox_email="support@acme.com",
            since_history_id="1000",
        )

        assert [e["message_id"] for e in envelopes] == ["m1"]
        # Stops at m1 so m2 (and m3) are retried on the next tick.
        assert cursor == "1010"

    async def test_no_new_mail_leaves_cursor_untouched(self, fake_gmail):
        fake_gmail(history_pages=[{}])

        envelopes, cursor = await gmail_svc.list_new_inbox_messages(
            access_token="tok",
            mailbox_email="support@acme.com",
            since_history_id="1000",
        )

        assert envelopes == []
        assert cursor == "1000"


class TestHistoryRequestShape:
    async def test_requests_only_inbox_message_additions(self, fake_gmail):
        """Asking Gmail to filter server-side is what keeps a quiet mailbox at
        one cheap request per tick."""
        fake = fake_gmail(history_pages=[{}])

        await gmail_svc.list_new_inbox_messages(
            access_token="tok",
            mailbox_email="support@acme.com",
            since_history_id="1000",
        )

        _, params = next(c for c in fake.calls if c[0] == "/history")
        assert params["startHistoryId"] == "1000"
        assert params["historyTypes"] == "messageAdded"
        assert params["labelId"] == "INBOX"
