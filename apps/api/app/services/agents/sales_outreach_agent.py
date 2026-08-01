"""SalesOutreachAgent — grounded draft-and-route agent for outbound sales mail.

The outbound mirror of CustomerSupportAgent: same bounded hybrid-search draft
loop, same confidence heuristic, same per-org trust mode. What differs is the
direction of the conversation and, because of that, the autonomy rules.

Triggered per lead by `sales/lead-outreach` with a `kind`:

  first_touch  The first cold email to a lead an admin imported.
  follow_up    A nudge when a sent email went unanswered (fired by the daily
               cadence cron, not by a human).
  reply        An answer to something the prospect wrote back, logged by an
               admin through the inbox UI.

Steps:

  1. load_lead        Skips silently if the feature is off for the org, the
                      lead is gone, the lead is already won/lost, or a
                      first_touch is being replayed onto a lead that has
                      already been contacted.
  2. draft            `execute_task_blocking` — the same bounded multi-round
                      hybrid-search tool loop chat uses. No bespoke retrieval,
                      and no external research: the only prospect-specific
                      input is what the importing admin typed in the CSV.
  3. score_confidence Heuristic over the retrieved sources; zero sources
                      always forces human review.
  4. route            Reads sales_settings for the org's trust mode
                      (shadow | assisted | autonomous) and applies three hard,
                      non-configurable overrides — cold first-touch, anything
                      mentioning price or contract terms, and ungrounded
                      drafts never send themselves, in any mode.
  5. send             Only reached by an autonomous-eligible follow-up. Sends
                      from the rep named in sales_settings.sender_user_id;
                      orgs with no connected mailbox are draft-only.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.database import get_service_client
from app.observability import get_logger
from app.services import sales_outreach as so
from app.services.agents.base_agent import BaseAgent
from app.services.integrations import gmail
from app.services.llm.task_chain import execute_task_blocking

log = get_logger(__name__)

# Below this, a draft is treated as too weakly grounded to send itself even
# when every other autonomy condition is met. Tunable without a migration.
_MIN_AUTONOMOUS_CONFIDENCE = 0.65

# Leads in these states are done being worked; a stray cron or replayed event
# must not restart outreach on them.
_TERMINAL_STATUSES = {"won", "lost"}

_VALID_KINDS = {"first_touch", "follow_up", "reply"}

# How much prior thread to feed back into a follow-up/reply prompt. Six
# messages is three exchanges — enough for continuity, short enough to leave
# the search results the bulk of the context budget.
_THREAD_CONTEXT_LIMIT = 6

_SHARED_RULES = """
Ground every specific claim in search_company_knowledge -- product
capabilities, customer stories, results, integrations. If the knowledge base
doesn't support a claim, leave it out rather than guessing. Never state
pricing, discounts, or contract terms.

Write plain text, no markdown, no placeholder brackets. Do not invent a
signature or sign-off name.
"""

_FIRST_TOUCH_PROMPT = """You are writing a first cold outreach email to a \
prospect on behalf of this company. Before writing, search the company's \
knowledge base to learn what it actually sells and which customer stories fit \
a prospect like this one.

Prospect:
  Company: {company_name}
  Contact: {contact_name}
  Title: {contact_title}
  Website: {domain}
  Notes from our team: {context_note}
{tone_line}
{rules}
Keep the body under 150 words. Lead with why you're writing to them \
specifically, not with a product pitch. End with a low-friction ask -- a short \
call, or simply whether it's worth a conversation.

Reply in exactly this format:
Subject: <one line, under 60 characters>

<email body>
"""

_FOLLOW_UP_PROMPT = """You are writing a short follow-up email to a prospect \
who has not replied yet. Search the company's knowledge base for a concrete, \
useful angle -- a relevant customer story or capability -- so this adds \
something rather than just asking again.

Prospect:
  Company: {company_name}
  Contact: {contact_name}
  Title: {contact_title}
  Notes from our team: {context_note}

Conversation so far:
{thread}
{tone_line}
{rules}
Keep it under 80 words. Do not repeat the previous email's pitch, do not \
guilt them for not replying, and do not offer a discount or any pricing \
concession to restart the conversation.

Write only the email body -- no subject line, it continues the same thread.
"""

_REPLY_PROMPT = """You are writing a reply to a prospect who responded to \
your outreach. Search the company's knowledge base to answer what they \
actually asked -- never guess at specifics.

Prospect:
  Company: {company_name}
  Contact: {contact_name}
  Title: {contact_title}

Conversation so far:
{thread}

Their latest message:
{inbound}
{tone_line}
{rules}
Answer their question directly and concisely. If they asked about pricing or \
contract terms, do not quote figures -- say a human will follow up with \
specifics. If the knowledge base doesn't cover what they asked, say so \
plainly instead of guessing.

Write only the email body -- no subject line, it continues the same thread.
"""


class SalesOutreachAgent(BaseAgent):
    agent_type = "sales_outreach"

    def __init__(self, *, org_id: str, lead_id: str, kind: str) -> None:
        super().__init__(
            org_id=org_id,
            input_data={"lead_id": lead_id, "kind": kind},
            triggered_by="webhook",
        )
        self.lead_id = lead_id
        self.kind = kind if kind in _VALID_KINDS else "first_touch"

    # ── Main flow ──────────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        svc = get_service_client()

        settings = await so.load_settings(svc, self.org_id)
        if not settings.get("enabled"):
            await self.log_step("load_lead", "skipped", {"reason": "feature_not_enabled"})
            return {"status": "skipped", "reason": "feature_not_enabled"}

        # ── Step 1: load and gate ───────────────────────────────────────
        await self.log_step("load_lead", "started")
        lead = await self._load_lead(svc)
        if not lead:
            await self.log_step("load_lead", "skipped", {"reason": "lead_not_found"})
            return {"status": "skipped", "reason": "lead_not_found"}

        if lead.get("status") in _TERMINAL_STATUSES:
            await self.log_step(
                "load_lead", "skipped",
                {"reason": "lead_closed", "status": lead.get("status")},
            )
            return {"status": "skipped", "reason": "lead_closed"}

        thread = await self._load_thread(svc)

        # A replayed import event must not produce a second cold email to
        # someone who has already heard from us.
        if self.kind == "first_touch" and any(m["direction"] == "outbound" for m in thread):
            await self.log_step("load_lead", "skipped", {"reason": "already_contacted"})
            return {"status": "skipped", "reason": "already_contacted"}

        await self.log_step(
            "load_lead", "completed",
            {"lead_id": self.lead_id, "kind": self.kind, "thread_len": len(thread)},
        )
        await asyncio.to_thread(
            lambda: svc.table("sales_leads")
            .update({"current_agent_run_id": self.run_id})
            .eq("id", self.lead_id).execute()
        )

        # ── Step 2: grounded draft ───────────────────────────────────────
        await self.log_step("draft", "started")
        try:
            result = await execute_task_blocking(
                user_message=self._build_prompt(lead, thread, settings),
                org_id=self.org_id,
                db_client=svc,
            )
        except Exception as exc:
            await self.log_step("draft", "failed", error=str(exc))
            await self._escalate(svc, settings, reason=f"draft_failed:{exc}")
            raise

        if result.error or not (result.text or "").strip():
            reason = f"draft_empty:{result.error or 'no_text'}"
            await self.log_step("draft", "failed", error=reason)
            await self._escalate(svc, settings, reason=reason)
            return {"status": "escalated", "reason": reason}

        await self.log_step(
            "draft", "completed",
            {"sources": len(result.sources), "tool_calls": result.tool_calls_made},
        )

        subject, body = self._resolve_subject_and_body(lead, thread, result.text)

        # ── Step 3: confidence ────────────────────────────────────────────
        confidence = so.score_confidence(result.sources)
        self.add_confidence(confidence)
        await self.log_step("score_confidence", "completed", {"confidence": confidence})

        draft_message_id = await self._insert_message(
            svc, direction="outbound", author_type="agent_draft", kind=self.kind,
            subject=subject, body=body, sources=result.sources,
            confidence=confidence, status="draft",
        )

        # ── Step 4: route ─────────────────────────────────────────────────
        route = await self._route(
            svc, lead=lead, settings=settings, confidence=confidence,
            sources=result.sources, draft_message_id=draft_message_id,
            subject=subject, body=body,
        )
        await self.log_step("route", "completed", route)

        # The whole route dict, not just the outcome: `held_reason` is the
        # answer to "why is this sitting in my queue?", and it's the first
        # thing anyone reads off the Inngest run.
        return {
            "lead_id": self.lead_id,
            "kind": self.kind,
            "confidence": confidence,
            **route,
        }

    # ── Step implementations ────────────────────────────────────────────

    async def _load_lead(self, svc: Any) -> dict[str, Any] | None:
        row = await asyncio.to_thread(
            lambda: svc.table("sales_leads")
            .select("*").eq("id", self.lead_id).eq("org_id", self.org_id)
            .maybe_single().execute()
        )
        return row.data if row and row.data else None

    async def _load_thread(self, svc: Any) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(
            lambda: svc.table("sales_messages")
            .select("direction, author_type, subject, body, status, created_at")
            .eq("lead_id", self.lead_id)
            .order("created_at")
            .execute()
        )
        # Drafts that were never approved aren't part of the conversation the
        # prospect has seen, so they must not be quoted back as context.
        return [
            r for r in (rows.data or [])
            if r["direction"] == "inbound" or r.get("status") in ("sent", "edited_and_sent")
        ]

    def _build_prompt(
        self, lead: dict[str, Any], thread: list[dict[str, Any]], settings: dict[str, Any],
    ) -> str:
        tone = (settings.get("tone") or "").strip()
        tone_line = f"\nWrite in this voice: {tone}\n" if tone else ""
        common = {
            "company_name": lead.get("company_name") or "(unknown)",
            "contact_name": lead.get("contact_name") or "(unknown)",
            "contact_title": lead.get("contact_title") or "(unknown)",
            "domain": lead.get("domain") or "(unknown)",
            "context_note": (lead.get("context_note") or "(none)")[:1000],
            "tone_line": tone_line,
            "rules": _SHARED_RULES,
        }

        if self.kind == "first_touch":
            return _FIRST_TOUCH_PROMPT.format(**common)

        rendered = self._render_thread(thread[-_THREAD_CONTEXT_LIMIT:])
        if self.kind == "reply":
            inbound = next(
                (m for m in reversed(thread) if m["direction"] == "inbound"), None
            )
            return _REPLY_PROMPT.format(
                **common,
                thread=rendered,
                inbound=(inbound or {}).get("body", "(no message body)")[:4000],
            )
        return _FOLLOW_UP_PROMPT.format(**common, thread=rendered)

    @staticmethod
    def _render_thread(messages: list[dict[str, Any]]) -> str:
        if not messages:
            return "(no prior messages)"
        parts = []
        for m in messages:
            who = "Them" if m["direction"] == "inbound" else "Us"
            parts.append(f"{who}: {(m.get('body') or '').strip()[:1500]}")
        return "\n\n".join(parts)

    def _resolve_subject_and_body(
        self, lead: dict[str, Any], thread: list[dict[str, Any]], raw: str,
    ) -> tuple[str, str]:
        """First touch opens a thread and needs its own subject line; every
        other kind continues one, so the subject is derived from the thread
        rather than asked of the model."""
        if self.kind == "first_touch":
            return so.split_subject_body(
                raw, fallback_subject=f"Quick question, {lead.get('company_name') or 'hello'}"
            )
        prior = next(
            (m.get("subject") for m in thread if m["direction"] == "outbound" and m.get("subject")),
            None,
        )
        return so.reply_subject(prior), (raw or "").strip()

    async def _insert_message(
        self, svc: Any, *, direction: str, author_type: str, kind: str | None,
        subject: str | None, body: str, sources: list[dict] | None = None,
        confidence: float | None = None, status: str | None = None,
    ) -> str:
        row: dict[str, Any] = {
            "lead_id": self.lead_id,
            "org_id": self.org_id,
            "direction": direction,
            "author_type": author_type,
            "kind": kind,
            "subject": subject,
            "body": body,
            "sources": sources or [],
        }
        if confidence is not None:
            row["confidence"] = confidence
        if status is not None:
            row["status"] = status
        inserted = await asyncio.to_thread(
            lambda: svc.table("sales_messages").insert(row).execute()
        )
        return inserted.data[0]["id"]

    async def _route(
        self, svc: Any, *, lead: dict[str, Any], settings: dict[str, Any],
        confidence: float, sources: list[dict], draft_message_id: str,
        subject: str, body: str,
    ) -> dict[str, Any]:
        mode = settings.get("mode") or "assisted"

        # ── Hard overrides. No sales_settings value can bypass these. ────
        #
        # A cold first email is the org's first impression and can't be
        # unsent; a draft with no knowledge-base support is a guess wearing a
        # confident tone; and money is money. Each of these routes to a human
        # regardless of trust mode.
        if self.kind == "first_touch":
            blocked_reason = "first_touch_requires_human"
        elif not sources:
            blocked_reason = "no_knowledge_base_match"
        elif so.mentions_pricing(subject, body):
            blocked_reason = "mentions_pricing"
        else:
            blocked_reason = None

        # Shadow is checked before the overrides, not after: it exists to
        # benchmark the agent before anyone relies on it, so it must not fill
        # the review queue or notify anyone — including when a draft is bad.
        # The draft and its confidence stay readable on the lead, which is the
        # whole point. Escalating here would page a team that hasn't yet
        # agreed to be paged by this agent.
        if mode == "shadow":
            await self._set_status(svc, "shadow_drafted")
            return {
                "outcome": "shadow_drafted",
                "mode": mode,
                **({"held_reason": blocked_reason} if blocked_reason else {}),
            }

        if blocked_reason == "no_knowledge_base_match":
            await self._escalate(svc, settings, reason=blocked_reason)
            return {"outcome": "escalated_for_review", "reason": blocked_reason}

        if mode == "autonomous" and not blocked_reason and confidence >= _MIN_AUTONOMOUS_CONFIDENCE:
            sent = await self._send(
                svc, lead=lead, settings=settings,
                draft_message_id=draft_message_id, subject=subject, body=body,
            )
            if sent:
                return {"outcome": "sent_autonomously"}
            # No connected mailbox, or the send failed — fall through to the
            # same review queue assisted mode uses rather than dropping it.

        await self._set_status(svc, "pending_review")
        await so.notify_escalation(
            org_id=self.org_id, settings=settings,
            text=(
                f":outbox_tray: Sales draft needs review — *{lead.get('company_name')}* "
                f"({lead.get('contact_email')})"
                + (f" · held: {blocked_reason.replace('_', ' ')}" if blocked_reason else "")
            ),
        )
        return {
            "outcome": "pending_review",
            "mode": mode,
            **({"held_reason": blocked_reason} if blocked_reason else {}),
        }

    async def _set_status(self, svc: Any, status: str) -> None:
        await asyncio.to_thread(
            lambda: svc.table("sales_leads").update({"status": status})
            .eq("id", self.lead_id).execute()
        )

    async def _escalate(self, svc: Any, settings: dict[str, Any], *, reason: str) -> None:
        await asyncio.to_thread(
            lambda: svc.table("sales_leads").update({
                "status": "escalated",
                "escalation_reason": reason[:200],
            }).eq("id", self.lead_id).execute()
        )
        await so.notify_escalation(
            org_id=self.org_id, settings=settings,
            text=f":warning: Sales agent couldn't draft for lead {self.lead_id} — {reason}",
        )

    async def _send(
        self, svc: Any, *, lead: dict[str, Any], settings: dict[str, Any],
        draft_message_id: str, subject: str, body: str,
    ) -> bool:
        creds = await so.resolve_sender(org_id=self.org_id, settings=settings)
        if not creds:
            return False
        try:
            sent = await gmail.send_email(
                access_token=creds["access_token"],
                sender=creds["email_address"],
                to=lead["contact_email"],
                subject=subject,
                body=body,
            )
        except Exception as exc:
            log.warning("sales_autonomous_send_failed org=%s err=%s", self.org_id, exc)
            return False

        await asyncio.to_thread(
            lambda: svc.table("sales_messages").update({
                "status": "sent", "sent_via": "gmail",
                "provider_message_id": sent.get("message_id"),
            }).eq("id", draft_message_id).execute()
        )
        await asyncio.to_thread(
            lambda: svc.table("sales_leads").update({
                "follow_up_count": int(lead.get("follow_up_count") or 0) + 1,
            }).eq("id", self.lead_id).execute()
        )
        await so.mark_contacted(svc, lead_id=self.lead_id, lead=lead, settings=settings)
        log.info(
            "sales_autonomous_send",
            org_id=self.org_id, lead_id=self.lead_id, run_id=self.run_id,
            sent_at=datetime.now(UTC).isoformat(),
        )
        return True
