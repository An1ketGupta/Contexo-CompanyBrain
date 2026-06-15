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

log = get_logger(__name__)


# Cap the draft body — long generic FAQ pastes blow Slack block kit + email
# thread length budgets. 2000 chars ≈ ~300 words; tuned to feel like a real
# support agent rather than a wall-of-text bot.
_DRAFT_BODY_CAP = 2000

# How many source chunks to keep on the ticket for the reviewer panel.
# More than this is noise; the chat retrieval already capped at 8 by default.
_DRAFT_SOURCE_CAP = 6


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
        await self.log_step("draft_response", "started")
        draft_prompt = self._build_draft_prompt()
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

        # Confidence proxy — execute_task doesn't surface a score directly
        # (the chat UI computes it from chunk similarities). We piggy-back on
        # source count + first source similarity so the queue triage UI
        # always has a number to sort by. 0..10 to match messages.metadata.
        confidence = self._estimate_confidence(draft.sources)
        body = draft.text.strip()[:_DRAFT_BODY_CAP]
        sources = (draft.sources or [])[:_DRAFT_SOURCE_CAP]
        self.add_confidence(confidence)
        await self.log_step(
            "draft_response", "completed",
            {
                "body_chars": len(body),
                "source_count": len(sources),
                "confidence": confidence,
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

        # ── Step 4: best-effort Slack ping ─────────────────────────────
        await self._maybe_notify_support_channel(ticket_id=ticket_id, confidence=confidence)

        return {
            "ticket_id": ticket_id,
            "status": "drafted",
            "confidence": confidence,
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
        sig = hashlib.sha256(f"{subject}\n{body[:256]}".encode("utf-8")).hexdigest()[:24]

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

    def _build_draft_prompt(self) -> str:
        """Prompt is intentionally specific. Generic 'write a helpful reply'
        prompts produce generic-sounding text. We force the model to:
            - keep replies short (under 200 words)
            - cite real policy text from retrieved docs (the prompt hints at
              the search tool)
            - say "I'm not sure" rather than fabricate
            - skip the salutation prefix (we add 'Hi <name>' on send)
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

    async def _maybe_notify_support_channel(
        self, *, ticket_id: str, confidence: float
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
