"""CustomerSupportAgent — grounded draft-and-route agent for inbound support mail.

Triggered by `support/ticket-inbound`, fired from the email classifier
(`email_classify_functions.py`) when an inbound message is labeled
`support`. Fresh design — does not reuse the retired SupportResponse
agent's single global auto-send flag or its denormalized draft-on-ticket
model. Steps:

  1. dedupe_or_create_ticket  Thread by sha256(from_email + normalized
                              subject) against any non-terminal ticket.
                              Reopens a resolved thread if the customer
                              writes back. Skips entirely (no ticket) if
                              the org hasn't enabled the feature.
  2. triage                  One structured LLM call -> sub_category,
                              priority, sentiment.
  3. draft_response           `execute_task_blocking` — same bounded,
                              multi-round hybrid-search tool loop chat
                              uses. No bespoke retrieval code.
  4. score_confidence         Heuristic from the retrieved sources; zero
                              sources always forces human review.
  5. route                    Reads support_settings for the org's trust
                              mode (shadow | assisted | autonomous) and
                              applies two hard, non-configurable overrides:
                              negative sentiment and zero sources always
                              escalate to a human, regardless of mode.
  6. send                     Only reached for autonomous-eligible replies.
                              Sends from the org's support mailbox (so the
                              reply comes from the address the customer wrote
                              to). Orgs with no connected mailbox get a
                              draft-only ticket.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any

from app.database import get_service_client
from app.observability import get_logger
from app.services.agents.base_agent import BaseAgent
from app.services.integrations import gmail, support_mailbox
from app.services.integrations import slack as slack_service
from app.services.llm import Message, get_llm_client
from app.services.llm.task_chain import execute_task_blocking

log = get_logger(__name__)

# Below this, the draft is treated as ungrounded even if sources exist
# (e.g. one weak/irrelevant chunk). Tunable without a migration.
_MIN_AUTONOMOUS_CONFIDENCE = 0.65

_SUBJECT_PREFIX_RE = re.compile(r"^\s*(re|fwd?|fw)\s*:\s*", re.IGNORECASE)

_TRIAGE_PROMPT = """Classify this customer support email. Reply with ONLY a
single line in this exact format:

  subcategory=<short-lowercase-slug>;priority=<p0|p1|p2|p3>;sentiment=<negative|neutral|positive>

subcategory: a short slug for the topic (e.g. billing, login, refund,
how-to, bug, account, feature-request). priority: p0 = production-down /
data-loss / security, p1 = blocking the customer's work, p2 = normal
question, p3 = minor/cosmetic. sentiment: the customer's tone.

Subject: {subject}
From: {from_email}
Body:
{body}
"""

_TRIAGE_LINE_RE = re.compile(
    r"subcategory\s*=\s*([a-z0-9\-]+)\s*;\s*priority\s*=\s*(p[0-3])\s*;\s*sentiment\s*=\s*(negative|neutral|positive)",
    re.IGNORECASE,
)

_DRAFT_PROMPT = """You are drafting a customer support email reply on behalf \
of this company. Use search_company_knowledge to find the company's actual \
policy or product information before answering -- never guess at specifics \
like refund windows, prices, or account behavior.

Customer email:
Subject: {subject}
From: {from_email}

{body}

Write a complete, ready-to-send reply. Be concise and professional. If the \
knowledge base doesn't have enough information to answer confidently, say so \
plainly instead of guessing.
"""


class CustomerSupportAgent(BaseAgent):
    agent_type = "customer_support"

    def __init__(self, *, org_id: str, ticket_input: dict[str, Any]) -> None:
        super().__init__(
            org_id=org_id,
            input_data=dict(ticket_input),
            triggered_by="webhook",
        )
        self.subject: str = (ticket_input.get("subject") or "").strip() or "(no subject)"
        self.body: str = ticket_input.get("body") or ""
        self.from_email: str = (ticket_input.get("from_email") or "").strip().lower()
        self.from_name: str = _display_name(ticket_input.get("from_raw"), self.from_email)

    # ── Main flow ──────────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        svc = get_service_client()

        settings = await self._load_settings(svc)
        if not settings or not settings.get("enabled"):
            await self.log_step(
                "dedupe_or_create_ticket", "skipped",
                {"reason": "feature_not_enabled"},
            )
            return {"status": "skipped", "reason": "feature_not_enabled"}

        # ── Step 1: thread into a ticket ────────────────────────────────
        await self.log_step("dedupe_or_create_ticket", "started")
        ticket_id = await self._dedupe_or_create_ticket(svc)
        # Only append the customer's message if it isn't already the latest
        # inbound on this thread. A genuine follow-up email appends; an admin
        # hitting Regenerate (which replays the same event) does not.
        appended = await self._append_inbound_if_new(svc, ticket_id)
        await self.log_step(
            "dedupe_or_create_ticket", "completed",
            {"ticket_id": ticket_id, "inbound_appended": appended},
        )

        # ── Step 2: triage ───────────────────────────────────────────────
        await self.log_step("triage", "started")
        triage = await self._triage()
        await self.log_step("triage", "completed", triage)
        await asyncio.to_thread(
            lambda: svc.table("support_tickets").update({
                "category": triage["subcategory"],
                "priority": triage["priority"],
                "sentiment": triage["sentiment"],
                "current_agent_run_id": self.run_id,
            }).eq("id", ticket_id).execute()
        )

        # ── Step 3: grounded draft ───────────────────────────────────────
        await self.log_step("draft_response", "started")
        try:
            result = await execute_task_blocking(
                user_message=_DRAFT_PROMPT.format(
                    subject=self.subject, from_email=self.from_email, body=self.body,
                ),
                org_id=self.org_id,
                db_client=svc,
            )
        except Exception as exc:
            await self.log_step("draft_response", "failed", error=str(exc))
            await self._escalate(svc, ticket_id, reason=f"draft_failed:{exc}")
            raise
        await self.log_step(
            "draft_response", "completed",
            {"sources": len(result.sources), "tool_calls": result.tool_calls_made},
        )

        # ── Step 4: confidence ────────────────────────────────────────────
        confidence = _score_confidence(result.sources)
        self.add_confidence(confidence)
        await self.log_step("score_confidence", "completed", {"confidence": confidence})

        draft_message_id = await self._insert_message(
            svc, ticket_id=ticket_id, direction="outbound",
            author_type="agent_draft", body=result.text,
            confidence=confidence, status="draft",
        )

        # ── Step 5: route ─────────────────────────────────────────────────
        route = await self._route(
            svc, ticket_id=ticket_id, settings=settings, triage=triage,
            confidence=confidence, sources=result.sources,
            draft_message_id=draft_message_id, draft_body=result.text,
        )
        await self.log_step("route", "completed", route)

        return {
            "ticket_id": ticket_id,
            "confidence": confidence,
            "outcome": route["outcome"],
        }

    # ── Step implementations ────────────────────────────────────────────

    async def _load_settings(self, svc: Any) -> dict[str, Any] | None:
        row = await asyncio.to_thread(
            lambda: svc.table("support_settings")
            .select("*").eq("org_id", self.org_id).maybe_single().execute()
        )
        return row.data if row else None

    async def _dedupe_or_create_ticket(self, svc: Any) -> str:
        thread_key = _thread_key(self.from_email, self.subject)
        existing = await asyncio.to_thread(
            lambda: svc.table("support_tickets")
            .select("id, status")
            .eq("org_id", self.org_id)
            .eq("thread_key", thread_key)
            .not_.in_("status", ["resolved", "spam"])
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = existing.data or []
        if rows:
            return rows[0]["id"]

        inserted = await asyncio.to_thread(
            lambda: svc.table("support_tickets").insert({
                "org_id": self.org_id,
                "channel": "email",
                "thread_key": thread_key,
                "from_email": self.from_email,
                "from_name": self.from_name,
                "subject": self.subject,
                "status": "open",
            }).execute()
        )
        return inserted.data[0]["id"]

    async def _append_inbound_if_new(self, svc: Any, ticket_id: str) -> bool:
        """Insert the inbound customer message unless it duplicates the most
        recent one on this ticket. Returns True if a row was inserted."""
        latest = await asyncio.to_thread(
            lambda: svc.table("support_messages")
            .select("body")
            .eq("ticket_id", ticket_id).eq("direction", "inbound")
            .order("created_at", desc=True).limit(1).execute()
        )
        rows = latest.data or []
        if rows and (rows[0].get("body") or "") == self.body:
            return False
        await self._insert_message(
            svc, ticket_id=ticket_id, direction="inbound",
            author_type="customer", body=self.body,
        )
        return True

    async def _insert_message(
        self, svc: Any, *, ticket_id: str, direction: str, author_type: str,
        body: str, confidence: float | None = None, status: str | None = None,
    ) -> str:
        row = {
            "ticket_id": ticket_id,
            "org_id": self.org_id,
            "direction": direction,
            "author_type": author_type,
            "body": body,
        }
        if confidence is not None:
            row["confidence"] = confidence
        if status is not None:
            row["status"] = status
        inserted = await asyncio.to_thread(
            lambda: svc.table("support_messages").insert(row).execute()
        )
        return inserted.data[0]["id"]

    async def _triage(self) -> dict[str, Any]:
        client = get_llm_client()
        try:
            resp = await client.complete(
                [Message(role="user", content=_TRIAGE_PROMPT.format(
                    subject=self.subject[:200], from_email=self.from_email,
                    body=self.body[:1800],
                ))],
                tools=(),
                temperature=0.0,
            )
            m = _TRIAGE_LINE_RE.search((resp.text or "").strip().splitlines()[0]
                                        if (resp.text or "").strip() else "")
            if m:
                return {
                    "subcategory": m.group(1).lower(),
                    "priority": m.group(2).lower(),
                    "sentiment": m.group(3).lower(),
                }
        except Exception as exc:
            log.warning("support_triage_failed org=%s err=%s", self.org_id, exc)
        # Fail-safe default: unclassified, normal priority, neutral tone —
        # never blocks the draft step on a triage hiccup.
        return {"subcategory": "unclassified", "priority": "p2", "sentiment": "neutral"}

    async def _route(
        self, svc: Any, *, ticket_id: str, settings: dict[str, Any],
        triage: dict[str, Any], confidence: float, sources: list[dict],
        draft_message_id: str, draft_body: str,
    ) -> dict[str, Any]:
        mode = settings.get("mode") or "assisted"
        negative = triage["sentiment"] == "negative"
        ungrounded = len(sources) == 0

        # Hard overrides — no support_settings value can bypass these.
        if negative or ungrounded:
            reason = "negative_sentiment" if negative else "no_knowledge_base_match"
            await self._mark_pending_review(svc, ticket_id, notify=settings, escalation_reason=reason)
            return {"outcome": "escalated_for_review", "reason": reason}

        eligible_autonomous = (
            mode == "autonomous"
            and triage["subcategory"] in (settings.get("autonomous_categories") or [])
            and confidence >= _MIN_AUTONOMOUS_CONFIDENCE
        )
        if eligible_autonomous:
            sent = await self._send_draft(
                svc, ticket_id=ticket_id, settings=settings,
                draft_message_id=draft_message_id, body=draft_body,
            )
            if sent:
                return {"outcome": "sent_autonomously"}
            # Sending wasn't possible (no connected mailbox / send failed) —
            # fall through to the same human-review path assisted mode uses.

        if mode == "shadow":
            # Draft is recorded for quality measurement but the ticket stays
            # `open` — shadow mode exists to benchmark the agent before anyone
            # relies on it, so it must not fill up the review queue or ping
            # anyone. The draft is still readable on the ticket.
            return {"outcome": "shadow_drafted", "mode": mode}

        await self._mark_pending_review(svc, ticket_id, notify=settings)
        return {"outcome": "pending_review", "mode": mode}

    async def _mark_pending_review(
        self, svc: Any, ticket_id: str, *,
        notify: dict[str, Any] | None, escalation_reason: str | None = None,
    ) -> None:
        update: dict[str, Any] = {"status": "pending_review" if not escalation_reason else "escalated"}
        if escalation_reason:
            update["escalation_reason"] = escalation_reason
            from datetime import UTC, datetime
            update["escalated_at"] = datetime.now(UTC).isoformat()
        await asyncio.to_thread(
            lambda: svc.table("support_tickets").update(update).eq("id", ticket_id).execute()
        )
        if notify and notify.get("escalation_channel_id"):
            try:
                label = "needs review" if not escalation_reason else f"escalated ({escalation_reason})"
                await slack_service.post_message(
                    org_id=self.org_id,
                    channel_id=notify["escalation_channel_id"],
                    text=f":email: Support ticket {label} — *{self.subject}* from {self.from_email}",
                )
            except Exception as exc:
                log.warning("support_slack_notify_failed org=%s err=%s", self.org_id, exc)

    async def _escalate(self, svc: Any, ticket_id: str, *, reason: str) -> None:
        await self._mark_pending_review(svc, ticket_id, notify=None, escalation_reason=reason)

    async def _resolve_sender(self, settings: dict[str, Any]) -> dict[str, Any] | None:
        """Credentials for the reply's From address.

        Sends from the org's support mailbox so the reply comes from the
        address the customer wrote to, keeping the thread intact.
        Returns None (draft-only) if no mailbox is connected.
        """
        creds = await support_mailbox.get_credentials(org_id=self.org_id)
        if creds and gmail.has_send_scope(creds.get("scopes")):
            return creds
        return None

    async def _send_draft(
        self, svc: Any, *, ticket_id: str, settings: dict[str, Any],
        draft_message_id: str, body: str,
    ) -> bool:
        creds = await self._resolve_sender(settings)
        if not creds:
            return False

        from datetime import UTC, datetime

        try:
            sent = await gmail.send_email(
                access_token=creds["access_token"],
                sender=creds["email_address"],
                to=self.from_email,
                subject=(f"Re: {self.subject}" if not self.subject.lower().startswith("re:") else self.subject),
                body=body,
            )
        except Exception as exc:
            log.warning("support_autonomous_send_failed org=%s err=%s", self.org_id, exc)
            return False

        now = datetime.now(UTC).isoformat()
        await asyncio.to_thread(
            lambda: svc.table("support_messages").update({
                "status": "sent", "sent_via": "gmail",
                "provider_message_id": sent.get("message_id"),
            }).eq("id", draft_message_id).execute()
        )
        await asyncio.to_thread(
            lambda: svc.table("support_tickets").update({
                "status": "awaiting_customer", "first_response_at": now,
            }).eq("id", ticket_id).execute()
        )
        return True


# ── Pure helpers ─────────────────────────────────────────────────────────


def _thread_key(from_email: str, subject: str) -> str:
    normalized = _SUBJECT_PREFIX_RE.sub("", subject or "")
    normalized = _SUBJECT_PREFIX_RE.sub("", normalized).strip().lower()
    return hashlib.sha256(f"{from_email}\n{normalized}".encode()).hexdigest()[:32]


def _display_name(from_raw: str | None, from_email: str) -> str | None:
    """Pull "Jane Doe" out of `"Jane Doe" <jane@acme.com>`. Returns None when
    the envelope carried no name (the part before `<` is empty, or is just the
    address repeated)."""
    if not from_raw:
        return None
    name = from_raw.split("<", 1)[0].strip().strip('"')
    if not name or name.lower() == from_email:
        return None
    return name


def _score_confidence(sources: list[dict]) -> float:
    """Heuristic, not a model output: zero sources -> 0.0 (forces review
    upstream). Otherwise a blend of the strongest match and how many
    chunks corroborate it. Tune the weights once real ticket volume lets
    us compare this against human edit-distance on the drafts."""
    if not sources:
        return 0.0
    top = max((s.get("similarity") or s.get("vector_similarity") or 0.0) for s in sources)
    breadth = min(len(sources) / 3, 1.0)
    return round(min(1.0, top * 0.7 + breadth * 0.3), 3)
