"""Sales outreach agent: the rules that decide whether mail leaves the building.

Everything here is about routing, not about prose quality. The drafting call
is stubbed — what's under test is which of the four outcomes a given
(trust mode, message kind, draft content, retrieval result) lands on, because
those are the rules a customer is trusting when they turn autonomous mode on.

The three hard overrides get a test each, and each is asserted under
`mode='autonomous'` specifically: an override that only holds in assisted mode
would be worthless.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

import app.inngest  # noqa: F401  (import-order cycle, see test_onboarding_step_toggles)
from app.services import sales_outreach as so
from app.services.agents.sales_outreach_agent import SalesOutreachAgent
from app.services.llm.task_chain import TaskResult
from tests.unit.fake_supabase import FakeSupabase

ORG = str(uuid.uuid4())
LEAD = str(uuid.uuid4())

# A source list strong enough to clear _MIN_AUTONOMOUS_CONFIDENCE (0.65):
# top similarity 0.9 * 0.7 + full breadth 1.0 * 0.3 = 0.93.
STRONG_SOURCES = [
    {"document_id": "d1", "document_name": "Case study", "similarity": 0.9},
    {"document_id": "d2", "document_name": "Product FAQ", "similarity": 0.8},
    {"document_id": "d3", "document_name": "Pricing page", "similarity": 0.75},
]


def _db(
    *,
    mode: str,
    enabled: bool = True,
    lead_overrides: dict | None = None,
    messages: list[dict] | None = None,
) -> FakeSupabase:
    return FakeSupabase({
        "sales_settings": [{
            "org_id": ORG, "enabled": enabled, "mode": mode,
            "sender_user_id": str(uuid.uuid4()), "tone": None,
            "follow_up_delay_days": 3, "max_follow_ups": 3, "daily_send_cap": 20,
            "escalation_channel_id": None, "escalation_channel_name": None,
        }],
        "sales_leads": [{
            "id": LEAD, "org_id": ORG, "company_name": "Acme Logistics",
            "contact_email": "jo@acme.test", "contact_name": "Jo",
            "contact_title": "Head of Ops", "domain": "acme.test",
            "context_note": None, "status": "new", "follow_up_count": 0,
            "first_contacted_at": None, "next_follow_up_at": None,
            **(lead_overrides or {}),
        }],
        "sales_messages": list(messages or []),
        "agent_runs": [],
    })


@pytest.fixture
def harness(monkeypatch):
    """Wires the agent to an in-memory DB and records what it tried to send.

    Returns a `run(kind, draft_text, sources, **db_kwargs)` callable that
    returns (outcome, sent_calls).
    """
    sent: list[dict[str, Any]] = []

    async def _fake_send_email(**kwargs):
        sent.append(kwargs)
        return {"message_id": "m1", "thread_id": "t1"}

    async def _fake_resolve_sender(*, org_id, settings):
        return {"access_token": "tok", "email_address": "rep@us.test"}

    monkeypatch.setattr("app.services.agents.sales_outreach_agent.gmail.send_email", _fake_send_email)
    monkeypatch.setattr(so, "resolve_sender", _fake_resolve_sender)

    def _run(kind: str, *, text: str, sources: list[dict], **db_kwargs):
        db = _db(**db_kwargs)
        sent.clear()

        monkeypatch.setattr("app.database.get_service_client", lambda: db)
        monkeypatch.setattr("app.services.agents.base_agent.get_service_client", lambda: db)
        monkeypatch.setattr("app.services.agents.sales_outreach_agent.get_service_client", lambda: db)

        async def _fake_draft(**kwargs):
            return TaskResult(text=text, sources=sources, tool_calls_made=1)

        monkeypatch.setattr(
            "app.services.agents.sales_outreach_agent.execute_task_blocking", _fake_draft
        )

        agent = SalesOutreachAgent(org_id=ORG, lead_id=LEAD, kind=kind)
        import asyncio

        result = asyncio.run(agent.run_safely())
        return result, sent, db

    return _run


# ── Hard overrides: none of these may be lifted by trust mode ──────────────


def test_first_touch_never_sends_itself_even_in_autonomous_mode(harness):
    """A cold email is the org's first impression and cannot be unsent. It
    goes to a human no matter how confident or well-grounded the draft is."""
    result, sent, db = harness(
        "first_touch",
        text="Subject: Quick question\n\nWe help ops teams like yours.",
        sources=STRONG_SOURCES,
        mode="autonomous",
    )
    assert result["outcome"] == "pending_review"
    assert sent == []
    assert db.tables["sales_leads"][0]["status"] == "pending_review"


def test_pricing_content_never_sends_itself_even_in_autonomous_mode(harness):
    """A wrong number in a quote isn't a tone problem a confidence score can
    catch, and it can't be walked back once it's in someone's inbox."""
    result, sent, _ = harness(
        "follow_up",
        text="Following up — our pricing starts at $49 per seat per month.",
        sources=STRONG_SOURCES,
        mode="autonomous",
        lead_overrides={"status": "awaiting_reply", "first_contacted_at": "2026-07-01T00:00:00Z"},
    )
    assert result["outcome"] == "pending_review"
    assert sent == []


def test_ungrounded_draft_escalates_rather_than_sending(harness):
    """Zero retrieved sources means the model wrote from nothing. That's the
    one case where the lead is flagged rather than merely queued."""
    result, sent, db = harness(
        "follow_up",
        text="Just checking in on my last note.",
        sources=[],
        mode="autonomous",
        lead_overrides={"status": "awaiting_reply", "first_contacted_at": "2026-07-01T00:00:00Z"},
    )
    assert result["outcome"] == "escalated_for_review"
    assert sent == []
    lead = db.tables["sales_leads"][0]
    assert lead["status"] == "escalated"
    assert lead["escalation_reason"] == "no_knowledge_base_match"


# ── Trust modes ────────────────────────────────────────────────────────────


def test_shadow_mode_drafts_but_never_queues_or_sends(harness):
    """Shadow exists to benchmark output before anyone relies on it, so it
    must not send AND must not put work in the review queue."""
    result, sent, db = harness(
        "follow_up",
        text="A useful nudge with a relevant customer story.",
        sources=STRONG_SOURCES,
        mode="shadow",
        lead_overrides={"status": "awaiting_reply", "first_contacted_at": "2026-07-01T00:00:00Z"},
    )
    assert result["outcome"] == "shadow_drafted"
    assert sent == []
    assert db.tables["sales_leads"][0]["status"] == "shadow_drafted"
    # The draft is still recorded — that's the entire point of shadow mode.
    drafts = [m for m in db.tables["sales_messages"] if m["author_type"] == "agent_draft"]
    assert len(drafts) == 1 and drafts[0]["status"] == "draft"


def test_shadow_mode_stays_silent_even_on_a_bad_draft(harness):
    """Regression: an ungrounded draft used to escalate the lead (and ping
    Slack) before the shadow check ran, paging a team that hadn't yet agreed
    to be paged by this agent. Shadow means silent, including on failure."""
    result, sent, db = harness(
        "follow_up",
        text="A guess with nothing behind it.",
        sources=[],
        mode="shadow",
        lead_overrides={"status": "awaiting_reply", "first_contacted_at": "2026-07-01T00:00:00Z"},
    )
    assert result["outcome"] == "shadow_drafted"
    assert result["held_reason"] == "no_knowledge_base_match"
    assert sent == []
    lead = db.tables["sales_leads"][0]
    assert lead["status"] == "shadow_drafted"
    assert lead.get("escalation_reason") is None


def test_assisted_mode_queues_a_clean_follow_up_for_review(harness):
    """The default. Even a well-grounded, price-free follow-up waits."""
    result, sent, db = harness(
        "follow_up",
        text="A useful nudge with a relevant customer story.",
        sources=STRONG_SOURCES,
        mode="assisted",
        lead_overrides={"status": "awaiting_reply", "first_contacted_at": "2026-07-01T00:00:00Z"},
    )
    assert result["outcome"] == "pending_review"
    assert sent == []


def test_autonomous_mode_sends_a_clean_grounded_follow_up(harness):
    """The one path where mail leaves without a human: a follow-up on an
    existing thread, well-grounded, no money mentioned."""
    result, sent, db = harness(
        "follow_up",
        text="A useful nudge with a relevant customer story.",
        sources=STRONG_SOURCES,
        mode="autonomous",
        lead_overrides={"status": "awaiting_reply", "first_contacted_at": "2026-07-01T00:00:00Z"},
    )
    assert result["outcome"] == "sent_autonomously"
    assert len(sent) == 1
    assert sent[0]["to"] == "jo@acme.test"
    lead = db.tables["sales_leads"][0]
    assert lead["status"] == "awaiting_reply"
    assert lead["follow_up_count"] == 1
    # The cadence clock is rearmed so the cron can pick it up again later.
    assert lead["next_follow_up_at"] is not None


def test_weakly_grounded_draft_is_not_autonomous(harness):
    """One thin match clears "has sources" but not the confidence bar."""
    result, sent, _ = harness(
        "follow_up",
        text="A vague nudge.",
        sources=[{"document_id": "d1", "document_name": "Notes", "similarity": 0.3}],
        mode="autonomous",
        lead_overrides={"status": "awaiting_reply", "first_contacted_at": "2026-07-01T00:00:00Z"},
    )
    assert result["outcome"] == "pending_review"
    assert sent == []


# ── Gating ─────────────────────────────────────────────────────────────────


def test_disabled_org_does_nothing_at_all(harness):
    result, sent, db = harness(
        "first_touch", text="anything", sources=STRONG_SOURCES,
        mode="assisted", enabled=False,
    )
    assert result["reason"] == "feature_not_enabled"
    assert sent == []
    assert db.tables["sales_messages"] == []


def test_closed_lead_is_never_reopened(harness):
    """A stray cron fire or replayed event must not restart outreach on
    someone the team already marked done."""
    result, sent, _ = harness(
        "follow_up", text="anything", sources=STRONG_SOURCES,
        mode="autonomous", lead_overrides={"status": "lost"},
    )
    assert result["reason"] == "lead_closed"
    assert sent == []


def test_replayed_import_does_not_send_a_second_cold_email(harness):
    """Inngest retries and duplicate import clicks both replay the same event.
    Someone who already heard from us must not hear from us again."""
    result, sent, db = harness(
        "first_touch",
        text="Subject: Hi\n\nSecond first-touch, which should never exist.",
        sources=STRONG_SOURCES,
        mode="assisted",
        lead_overrides={"status": "awaiting_reply", "first_contacted_at": "2026-07-01T00:00:00Z"},
        # A cold email that already went out on this thread.
        messages=[{
            "id": "m0", "lead_id": LEAD, "org_id": ORG, "direction": "outbound",
            "author_type": "agent_draft", "body": "First note.", "status": "sent",
            "created_at": "2026-07-01T00:00:00Z",
        }],
    )
    assert result["reason"] == "already_contacted"
    assert sent == []
    # No second draft was written.
    assert len(db.tables["sales_messages"]) == 1


def test_rejected_draft_is_not_treated_as_already_contacted(harness):
    """A draft the reviewer threw away never reached the prospect, so the
    replay guard must not mistake it for a sent email and skip forever."""
    result, sent, db = harness(
        "first_touch",
        text="Subject: Hi\n\nA fresh attempt.",
        sources=STRONG_SOURCES,
        mode="assisted",
        messages=[{
            "id": "m0", "lead_id": LEAD, "org_id": ORG, "direction": "outbound",
            "author_type": "agent_draft", "body": "Rejected note.", "status": "rejected",
            "created_at": "2026-07-01T00:00:00Z",
        }],
    )
    assert result["outcome"] == "pending_review"
    assert sent == []


# ── Pure helpers ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "our pricing starts at $49",
        "happy to offer a discount",
        "the annual plan works out cheaper",
        "I'll send the contract over",
        "20 USD per user",
        "let's discuss payment terms",
        "here's a quote for you",
    ],
)
def test_money_talk_is_detected(text):
    assert so.mentions_pricing(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "worth a quick chat next week?",
        "we helped a logistics team cut onboarding time",
        "does Tuesday work?",
    ],
)
def test_ordinary_outreach_is_not_flagged_as_money_talk(text):
    assert so.mentions_pricing(text) is False


def test_subject_is_split_off_the_draft():
    subject, body = so.split_subject_body(
        "Subject: Scaling ops at Acme\n\nHi Jo,\n\nSaw you're hiring.",
        fallback_subject="fallback",
    )
    assert subject == "Scaling ops at Acme"
    assert body.startswith("Hi Jo,")


def test_draft_without_a_subject_line_still_yields_a_usable_body():
    """The model is asked for the header but not trusted to produce it — a
    formatting miss must not cost us the whole draft."""
    subject, body = so.split_subject_body("Hi Jo, saw you're hiring.", fallback_subject="fallback")
    assert subject == "fallback"
    assert body == "Hi Jo, saw you're hiring."


def test_re_prefix_does_not_stack_on_a_long_thread():
    assert so.reply_subject("Re: Scaling ops") == "Re: Scaling ops"
    assert so.reply_subject("Scaling ops") == "Re: Scaling ops"


def test_confidence_is_zero_without_sources():
    assert so.score_confidence([]) == 0.0
