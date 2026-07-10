"""SupportResponseAgent — autonomous customer-reply drafter (Agent Day 12).

Triggered by `support/email-received` after an inbound email is classified
as `support` or `sales`. Steps:

  1. dedupe_or_create_ticket — insert a row in `support_tickets`, idempotent
     on (org_id, inbound_sig) so a webhook retry doesn't open two tickets.
  2. draft_response          — `execute_task_blocking` against the org's
                               knowledge base. Confidence score is captured
                               for the admin queue to triage by.
  3. persist_draft           — stamp the draft body + sources + confidence
                               onto the ticket row. Status → 'drafted' on
                               success, 'drafting_failed' on LLM error.
  4. notify_support_channel  — best-effort Slack post to the org's
                               configured support channel (if connected).
                               Channel id comes from
                               organizations.metadata.support_channel_id;
                               we skip silently if unset.

Why the draft lives on the ticket row (not in `messages`):

  Conversations require a NOT NULL user_id. The agent runs from a webhook
  with no human session — synthesizing a "support-bot" user adds an
  authorization surface (RLS for that user) for marginal benefit. Storing
  draft text directly is denormalized but trivially small; when an admin
  promotes a draft to "Send via Gmail" the API mints a real message owned
  by them at that point so delivery_status / approval plumbing reuses
  unchanged.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any

from app.config import get_settings
from app.database import get_service_client
from app.observability import get_logger
from app.services.agents.base_agent import BaseAgent
from app.services.integrations import slack as slack_service
from app.services.llm.task_chain import execute_task_blocking
from app.services.support_delivery import (
    DEFAULT_AUTOSEND_THRESHOLD,
    ESCALATION_MARKER as _ESCALATION_MARKER,
    deliver_ticket_reply,
    resolve_autosend_sender,
)

log = get_logger(__name__)


# Cap the draft body — long generic FAQ pastes blow Slack block kit + email
# thread length budgets. 2000 chars ≈ ~300 words; tuned to feel like a real
# support agent rather than a wall-of-text bot.
_DRAFT_BODY_CAP = 2000

# How many source chunks to keep on the ticket for the reviewer panel.
# More than this is noise; the chat retrieval already capped at 8 by default.
_DRAFT_SOURCE_CAP = 6

# The escalation sentinel (`_ESCALATION_MARKER`) is imported from
# support_delivery so the confidence floor here and the metrics endpoint's
# escalation counting both key off one phrase that stays in sync with the prompt.

# Confidence floor stamped on escalation drafts so the queue + Slack badge always
# render red (< 4), regardless of how many irrelevant chunks retrieval returned.
_ESCALATION_CONFIDENCE = 1.0

# Canned replies reuse the existing prompt_templates library (migration 018) —
# admins author them in Settings → Templates under these categories, and the
# agent offers them to the model as approved phrasing to adapt. No support-
# specific table: 'Customer Response' / 'Email' already exist as categories.
_REPLY_TEMPLATE_CATEGORIES = ("Customer Response", "Email")
_MAX_REPLY_TEMPLATES = 5        # keep the prompt bounded — top N by use_count
_REPLY_TEMPLATE_TEXT_CAP = 800  # per-template char cap in the prompt


class SupportResponseAgent(BaseAgent):
    agent_type = "support_response"

    def __init__(
        self,
        *,
        org_id: str,
        ticket_input: dict[str, Any],
        api_context: dict[str, Any] | None = None,
    ) -> None:
        input_data: dict[str, Any] = dict(ticket_input)
        if api_context:
            input_data["_api_context"] = api_context
        super().__init__(
            org_id=org_id,
            input_data=input_data,
            triggered_by="api" if api_context else "webhook",
        )
        self.ticket = ticket_input
        if api_context and api_context.get("run_id"):
            self.run_id = api_context["run_id"]

    # ── Main flow ──────────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        # ── Step 1: create (or recover) the ticket row ────────────────
        await self.log_step("create_ticket", "started")
        ticket_id = await self._upsert_ticket()
        await self.log_step("create_ticket", "completed", {"ticket_id": ticket_id})

        # ── Step 2: draft via execute_task_blocking ────────────────────
        templates = await self._fetch_reply_templates()
        await self.log_step(
            "draft_response", "started", {"reply_templates": len(templates)},
        )
        draft_prompt = self._build_draft_prompt(templates)
        try:
            draft = await execute_task_blocking(
                user_message=draft_prompt,
                org_id=self.org_id,
                db_client=get_service_client(),
            )
        except Exception as exc:
            await self.log_step("draft_response", "failed", error=str(exc))
            await self._mark_ticket(ticket_id, status="drafting_failed")
            raise

        if draft.error or not (draft.text or "").strip():
            await self.log_step(
                "draft_response", "failed",
                error=draft.error or "empty_draft",
            )
            await self._mark_ticket(ticket_id, status="drafting_failed")
            return {"ticket_id": ticket_id, "status": "drafting_failed"}

        body = draft.text.strip()[:_DRAFT_BODY_CAP]
        sources = (draft.sources or [])[:_DRAFT_SOURCE_CAP]

        # Confidence proxy — execute_task doesn't surface a score directly
        # (the chat UI computes it from chunk similarities). We piggy-back on
        # source count + first source similarity so the queue triage UI
        # always has a number to sort by. 0..10 to match messages.metadata.
        #
        # But when the model emits the escalation fallback (KB doesn't cover
        # the question) retrieval count is meaningless — the nearest chunks got
        # pulled but answered nothing. Scoring those as 7/10 paints a 🟢 badge
        # on a draft that literally says "I don't know", inverting the triage
        # signal the queue exists for. Floor escalations so they always badge red.
        is_escalation = self._is_escalation_draft(body)
        confidence = (
            _ESCALATION_CONFIDENCE if is_escalation
            else self._estimate_confidence(sources)
        )
        self.add_confidence(confidence)
        await self.log_step(
            "draft_response", "completed",
            {
                "body_chars": len(body),
                "source_count": len(sources),
                "confidence": confidence,
                "escalation": is_escalation,
            },
        )

        # ── Step 3: persist the draft on the ticket ───────────────────
        await self.log_step("persist_draft", "started")
        svc = get_service_client()
        try:
            await asyncio.to_thread(
                lambda: svc.table("support_tickets").update({
                    "ai_draft_body": body,
                    "ai_draft_sources": sources,
                    "ai_draft_confidence": confidence,
                    "status": "drafted",
                }).eq("id", ticket_id).execute()
            )
            await self.log_step("persist_draft", "completed")
        except Exception as exc:
            await self.log_step("persist_draft", "failed", error=str(exc))
            raise

        # ── Step 4: maybe auto-send (opt-in, high-confidence only) ─────
        final_status = await self._maybe_auto_send(
            ticket_id=ticket_id,
            body=body,
            confidence=confidence,
            is_escalation=is_escalation,
        )

        # ── Step 5: best-effort Slack ping ─────────────────────────────
        await self._maybe_notify_support_channel(
            ticket_id=ticket_id,
            confidence=confidence,
            auto_sent=(final_status == "auto_sent"),
        )

        return {
            "ticket_id": ticket_id,
            "status": final_status,
            "confidence": confidence,
            "escalation": is_escalation,
            "source_count": len(sources),
        }

    # ── Sub-steps ──────────────────────────────────────────────────────

    async def _upsert_ticket(self) -> str:
        """Insert the ticket, or recover the existing id on retry.

        Dedupe is on (org_id, inbound_sig) where the sig hashes
        subject + first 256 chars of body — matches the email_forward
        document de-dupe so the same forwarded thread doesn't open both
        a ticket and a doc on every retry.
        """
        svc = get_service_client()
        subject = self.ticket.get("subject") or ""
        body = self.ticket.get("body") or ""
        sig = hashlib.sha256(f"{subject}\n{body[:256]}".encode()).hexdigest()[:24]

        row = {
            "org_id": self.org_id,
            "from_email": (self.ticket.get("from_email") or "").lower()[:320],
            "from_name": self.ticket.get("from_name"),
            "subject": subject[:200],
            "body": body[:60_000],
            "category": self.ticket.get("category") or "support",
            "classifier_confidence": self.ticket.get("classifier_confidence"),
            "classifier_reason": self.ticket.get("classifier_reason"),
            "agent_run_id": self.run_id,
            "inbound_sig": sig,
            "status": "pending",
        }

        # Try insert; on the unique-violation we resolve the existing id and
        # cross-link it back to this run so the audit trail isn't lost.
        try:
            result = await asyncio.to_thread(
                lambda: svc.table("support_tickets").insert(row).execute()
            )
            return (result.data or [{}])[0].get("id")
        except Exception as exc:
            if "duplicate key" not in str(exc).lower():
                raise
            existing = await asyncio.to_thread(
                lambda: svc.table("support_tickets")
                .select("id")
                .eq("org_id", self.org_id)
                .eq("inbound_sig", sig)
                .maybe_single()
                .execute()
            )
            existing_id = (existing.data or {}).get("id") if existing else None
            if not existing_id:
                raise
            # Update the agent_run_id pointer so the latest run owns the audit.
            await asyncio.to_thread(
                lambda: svc.table("support_tickets")
                .update({"agent_run_id": self.run_id})
                .eq("id", existing_id)
                .execute()
            )
            return existing_id

    async def _fetch_reply_templates(self) -> list[dict[str, Any]]:
        """Pull the org's approved reply templates from prompt_templates.

        Only org-shared and built-in templates in the reply categories — a
        member's *private* template (is_shared=false, non-builtin) belongs to
        one person and the agent has no user context, so it's excluded. Ordered
        by use_count so the most-relied-on phrasing wins the prompt budget.
        """
        svc = get_service_client()
        try:
            res = await asyncio.to_thread(
                lambda: svc.table("prompt_templates")
                .select("title, description, template_text, category, is_builtin")
                .in_("category", list(_REPLY_TEMPLATE_CATEGORIES))
                .or_(f"and(org_id.eq.{self.org_id},is_shared.eq.true),is_builtin.eq.true")
                .order("use_count", desc=True)
                .limit(_MAX_REPLY_TEMPLATES)
                .execute()
            )
            return res.data or []
        except Exception as exc:
            # Templates are an enhancement, never a hard dependency — a query
            # error must not block drafting.
            log.warning("support_reply_templates_fetch_failed", org_id=self.org_id, error=str(exc))
            return []

    def _build_draft_prompt(self, templates: list[dict[str, Any]] | None = None) -> str:
        """Prompt is intentionally specific. Generic 'write a helpful reply'
        prompts produce generic-sounding text. We force the model to:
            - keep replies short (under 200 words)
            - cite real policy text from retrieved docs (the prompt hints at
              the search tool)
            - say "I'm not sure" rather than fabricate
            - skip the salutation prefix (we add 'Hi <name>' on send)
            - prefer an approved reply template when one fits the question
        """
        subject = self.ticket.get("subject") or "(no subject)"
        body = (self.ticket.get("body") or "")[:4000]
        from_name = self.ticket.get("from_name") or self.ticket.get("from_email") or "the customer"
        category = self.ticket.get("category") or "support"
        return (
            f"You are drafting a {category} response on behalf of the company.\n\n"
            f"Inbound email from {from_name}:\n"
            f"  Subject: {subject}\n"
            f"  Body: {body}\n\n"
            f"{self._render_templates_block(templates)}"
            "Draft a response under 200 words. Match a professional, warm "
            "tone — like a senior support engineer who knows the product. "
            "Search the company knowledge base for relevant policies, prices, "
            "procedures, and product details before answering — cite real "
            "facts rather than guessing.\n\n"
            "Strict rules:\n"
            "  - Do NOT include any greeting line (no 'Hi <name>,' or 'Hello,'). "
            "    A human will add that when sending.\n"
            "  - Do NOT include a sign-off (no 'Best,' or '— Team'). "
            "    A human will add that on send.\n"
            "  - Do NOT invent prices, dates, refund windows, or SLAs not in the docs.\n"
            "  - If the knowledge base doesn't cover the question, write: "
            "    'I don't have a confident answer for this — escalating to a teammate.' "
            "    and stop. A human will rewrite the rest.\n"
            "  - Plain text only, no markdown or HTML."
        )

    @staticmethod
    def _render_templates_block(templates: list[dict[str, Any]] | None) -> str:
        """Format approved reply templates as a prompt section. Empty string
        when there are none so the base prompt is byte-for-byte unchanged."""
        if not templates:
            return ""
        lines = [
            "Approved reply templates (author-vetted phrasing for common "
            "questions). If one clearly fits this customer's question, use it as "
            "the basis for your reply and adapt the specifics from the knowledge "
            "base. If none fit, ignore them and draft normally:\n",
        ]
        for i, t in enumerate(templates, 1):
            title = (t.get("title") or "Untitled").strip()
            desc = (t.get("description") or "").strip()
            text = (t.get("template_text") or "").strip()[:_REPLY_TEMPLATE_TEXT_CAP]
            when = f" — use when: {desc}" if desc else ""
            lines.append(f"  {i}. {title}{when}\n     {text}\n")
        lines.append("\n")
        return "".join(lines)

    @staticmethod
    def _is_escalation_draft(body: str) -> bool:
        """True when the draft is the KB-miss escalation fallback.

        The draft prompt instructs the model to emit '…escalating to a
        teammate.' verbatim when the knowledge base doesn't cover the
        question. We match on the distinctive tail phrase (not the whole
        sentence) so a punctuation/quote swap or a leading word doesn't slip
        an escalation through scored as if it were a real answer.
        """
        return _ESCALATION_MARKER in body.lower()

    @staticmethod
    def _estimate_confidence(sources: list[dict] | None) -> float:
        """Proxy confidence score (0..10) from retrieval signal.

        The chat UI's confidence pill is derived from chunk similarity
        thresholds; for the support queue we approximate with:

            no sources                → 1.0 (low)
            1 source                  → 5.0 (medium)
            2+ sources                → 7.0 (high)
            top-1 similarity > 0.75   → +2.0 bump (clamped to 10)

        This is a rough heuristic, not a calibrated score. It exists so
        the queue triage UI can sort and badge — admins still read the
        actual draft before sending.
        """
        if not sources:
            return 1.0
        base = 5.0 if len(sources) == 1 else 7.0
        try:
            top_sim = float(sources[0].get("similarity") or 0.0)
        except (TypeError, ValueError):
            top_sim = 0.0
        if top_sim > 0.75:
            base += 2.0
        return min(round(base, 1), 10.0)

    async def _mark_ticket(self, ticket_id: str, *, status: str) -> None:
        svc = get_service_client()
        try:
            await asyncio.to_thread(
                lambda: svc.table("support_tickets")
                .update({"status": status})
                .eq("id", ticket_id)
                .execute()
            )
        except Exception as exc:
            log.warning("support_ticket_status_update_failed", ticket_id=ticket_id, error=str(exc))

    async def _maybe_auto_send(
        self, *, ticket_id: str, body: str, confidence: float, is_escalation: bool,
    ) -> str:
        """Auto-send the draft when the org opted in and it clears the bar.

        Returns the resulting ticket status ('auto_sent' or 'drafted'). All of
        these must hold or the ticket stays 'drafted' in the review queue:

          * org enabled auto-send (metadata.support_autosend_enabled)
          * confidence >= org threshold (default DEFAULT_AUTOSEND_THRESHOLD)
          * NOT an escalation draft (belt-and-suspenders — escalations are
            already floored to 1.0 upstream so they can't clear the bar)
          * an eligible connected Gmail sender exists

        Auto-send never *blocks* a draft from reaching a human — every miss
        (and every send error) simply leaves it for manual review.
        """
        if is_escalation:
            return "drafted"

        svc = get_service_client()
        org_row = await asyncio.to_thread(
            lambda: svc.table("organizations")
            .select("metadata").eq("id", self.org_id).maybe_single().execute()
        )
        meta = (org_row.data or {}).get("metadata") or {} if org_row and org_row.data else {}
        if not meta.get("support_autosend_enabled"):
            await self.log_step("auto_send", "skipped", {"reason": "disabled"})
            return "drafted"

        threshold = float(meta.get("support_autosend_threshold", DEFAULT_AUTOSEND_THRESHOLD))
        if confidence < threshold:
            await self.log_step(
                "auto_send", "skipped",
                {"reason": "below_threshold", "confidence": confidence, "threshold": threshold},
            )
            return "drafted"

        sender_user_id = await resolve_autosend_sender(self.org_id)
        if not sender_user_id:
            await self.log_step("auto_send", "skipped", {"reason": "no_connected_sender"})
            return "drafted"

        # Re-read the ticket so delivery mints the outgoing message from the
        # persisted draft body + sources + confidence.
        ticket_row = await asyncio.to_thread(
            lambda: svc.table("support_tickets").select("*")
            .eq("id", ticket_id).maybe_single().execute()
        )
        ticket = (ticket_row.data or {}) if ticket_row else {}
        if not ticket.get("from_email"):
            await self.log_step("auto_send", "skipped", {"reason": "missing_recipient"})
            return "drafted"

        await self.log_step("auto_send", "started", {"threshold": threshold})
        try:
            result = await deliver_ticket_reply(
                org_id=self.org_id,
                sender_user_id=sender_user_id,
                ticket=ticket,
                body=body,
                status="auto_sent",
            )
            await self.log_step(
                "auto_send", "completed",
                {"to": result.get("to"), "message_id": result.get("message_id")},
            )
            return "auto_sent"
        except Exception as exc:
            # Send failure must not fail the run — the draft is safely persisted;
            # leave it in the queue for a human to send manually.
            await self.log_step("auto_send", "failed", error=str(exc))
            return "drafted"

    async def _maybe_notify_support_channel(
        self, *, ticket_id: str, confidence: float, auto_sent: bool = False,
    ) -> None:
        """Post a notification to the configured Slack support channel.

        Channel id lives at organizations.metadata.support_channel_id (set
        by admin in /admin/support settings). Unset → skip silently. Slack
        not connected → also skip silently. We don't want a misconfigured
        channel to bubble up as a "support ticket draft failed" error.
        """
        svc = get_service_client()
        org_row = await asyncio.to_thread(
            lambda: svc.table("organizations")
            .select("metadata")
            .eq("id", self.org_id)
            .maybe_single()
            .execute()
        )
        meta = (org_row.data or {}).get("metadata") if org_row and org_row.data else {}
        meta = meta or {}
        channel_id = meta.get("support_channel_id")
        if not channel_id:
            await self.log_step(
                "notify_support_team", "skipped",
                {"reason": "no_support_channel_configured"},
            )
            return

        try:
            settings = get_settings()
            ticket_url = f"{settings.app_url.rstrip('/')}/admin/support/{ticket_id}"
            subject = (self.ticket.get("subject") or "(no subject)").strip()
            from_email = (self.ticket.get("from_email") or "unknown").strip()
            badge = "🟢" if confidence >= 7 else "🟡" if confidence >= 4 else "🔴"
            if auto_sent:
                text = (
                    f"📤 *Auto-sent {self.ticket.get('category') or 'support'} reply to {from_email}*\n"
                    f"*Subject:* {subject}\n"
                    f"{badge} Confidence: {confidence:.1f}/10 — cleared the auto-send bar\n"
                    f"<{ticket_url}|View sent reply>"
                )
            else:
                text = (
                    f"📧 *New {self.ticket.get('category') or 'support'} email from {from_email}*\n"
                    f"*Subject:* {subject}\n"
                    f"{badge} Draft confidence: {confidence:.1f}/10\n"
                    f"<{ticket_url}|Review & send draft>"
                )
            await self.log_step("notify_support_team", "started", {"channel_id": channel_id})
            await slack_service.post_message(
                org_id=self.org_id,
                channel_id=channel_id,
                text=text,
            )
            await self.log_step("notify_support_team", "completed")
        except PermissionError as exc:
            await self.log_step("notify_support_team", "skipped", {"reason": str(exc)})
        except Exception as exc:
            # Best-effort — don't fail the run for a Slack outage.
            await self.log_step("notify_support_team", "failed", error=str(exc))


# Light helper for the inbound webhook to extract a display name from the
# bare envelope `From:` header. Returns None when there's nothing usable so
# the queue UI can just render the email.
_FROM_NAME_RE = re.compile(r"^\s*\"?([^\"<]+?)\"?\s*<")


def parse_from_name(from_header: str | None) -> str | None:
    if not from_header:
        return None
    m = _FROM_NAME_RE.match(from_header)
    if not m:
        return None
    name = m.group(1).strip()
    return name or None
