"""OnboardingAgent — autonomous new-hire onboarding (Agent Day 8).

Triggered by `org/member-joined` after an invitation is accepted. Steps:

  1. generate_plan      Personalised 90-day onboarding plan via
                        `execute_task_blocking` so the plan draws on actual
                        org documents.
  2. persist_plan       Save plan to `onboarding_plans` and stamp the
                        user's `onboarding_status` so the team panel
                        shows progress.
  3. create_notion_page Optional. If Notion is connected we pick the
                        first writable page as the parent. We don't
                        block the agent on Notion failures — the plan
                        already exists in our DB and surfaces in-app.
  4. send_welcome       Welcome email via Resend (event_type
                        `onboarding_welcome`). Replaces the generic
                        `welcome` email — they'd otherwise duplicate.
  5. notify_manager     If `manager_user_id` is set AND Slack is
                        connected, DM the manager with the plan link.

Each step is wrapped in `log_step` so the audit page shows a clean
execution log. Side effects are best-effort — a Notion / Slack outage
shouldn't fail the run.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.database import get_service_client
from app.observability import get_logger
from app.services.agents.base_agent import BaseAgent
from app.services.email import send_email_event
from app.services.integrations import notion as notion_service
from app.services.integrations import slack as slack_service
from app.services.llm.task_chain import execute_task_blocking

log = get_logger(__name__)


class OnboardingAgent(BaseAgent):
    agent_type = "onboarding"

    def __init__(self, *, org_id: str, hire_data: dict[str, Any]) -> None:
        super().__init__(
            org_id=org_id,
            input_data=hire_data,
            triggered_by="webhook",
            triggered_by_user_id=hire_data.get("invited_by"),
        )
        self.hire = hire_data

    # ── Helpers ─────────────────────────────────────────────────────────

    def _first_name(self) -> str:
        name = (self.hire.get("name") or "").strip()
        return name.split(" ")[0] if name else "there"

    def _role_label(self) -> str:
        role_title = (self.hire.get("role_title") or "").strip()
        return role_title or (self.hire.get("role") or "teammate").title()

    async def _resolve_org_name(self) -> str:
        svc = get_service_client()
        row = await asyncio.to_thread(
            lambda: svc.table("organizations")
            .select("name")
            .eq("id", self.org_id)
            .maybe_single()
            .execute()
        )
        return (row.data or {}).get("name") or "your workspace"

    async def _resolve_manager(self) -> dict[str, Any] | None:
        manager_id = self.hire.get("manager_user_id")
        if not manager_id:
            return None
        svc = get_service_client()
        profile = await asyncio.to_thread(
            lambda: svc.table("users")
            .select("display_name")
            .eq("id", manager_id)
            .maybe_single()
            .execute()
        )
        email: str | None = None
        try:
            au = await asyncio.to_thread(lambda: svc.auth.admin.get_user_by_id(manager_id))
            email = getattr(getattr(au, "user", None), "email", None)
        except Exception:
            pass
        if not email:
            return None
        return {
            "id": manager_id,
            "email": email,
            "display_name": (profile.data or {}).get("display_name"),
        }

    async def _stamp_status(
        self,
        *,
        status: str,
        plan_id: str | None = None,
    ) -> None:
        svc = get_service_client()
        update: dict[str, Any] = {
            "onboarding_status": status,
            "onboarding_agent_run_id": self.run_id,
        }
        await asyncio.to_thread(
            lambda: svc.table("users")
            .update(update)
            .eq("id", self.hire["user_id"])
            .execute()
        )
        if plan_id:
            # No-op — plan_id is set in the onboarding_plans row already.
            pass

    # ── Main flow ──────────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        settings = get_settings()
        app_url = settings.app_url.rstrip("/")

        # Mark in_progress so the team panel reflects the agent kicked off.
        await asyncio.to_thread(
            lambda: get_service_client()
            .table("users")
            .update(
                {
                    "onboarding_status": "in_progress",
                    "onboarding_agent_run_id": self.run_id,
                }
            )
            .eq("id", self.hire["user_id"])
            .execute()
        )

        # ── Step 1: generate the plan via LLM ─────────────────────────
        await self.log_step("generate_plan", "started")
        plan_prompt = self._build_plan_prompt()
        try:
            plan_result = await execute_task_blocking(
                user_message=plan_prompt,
                org_id=self.org_id,
                db_client=get_service_client(),
            )
        except Exception as exc:
            await self.log_step("generate_plan", "failed", error=str(exc))
            raise

        if plan_result.error or not (plan_result.text or "").strip():
            await self.log_step(
                "generate_plan",
                "failed",
                error=plan_result.error or "empty_plan",
            )
            raise RuntimeError("plan_generation_failed")

        plan_text = plan_result.text.strip()
        plan_sources = plan_result.sources or []
        await self.log_step(
            "generate_plan",
            "completed",
            {
                "plan_length": len(plan_text),
                "source_count": len(plan_sources),
            },
        )

        # ── Step 2: persist onboarding_plans row ──────────────────────
        await self.log_step("persist_plan", "started")
        plan_id: str | None = None
        try:
            svc = get_service_client()
            row = await asyncio.to_thread(
                lambda: svc.table("onboarding_plans")
                .insert(
                    {
                        "org_id": self.org_id,
                        "user_id": self.hire["user_id"],
                        "agent_run_id": self.run_id,
                        "plan_content": plan_text,
                        "plan_sources": plan_sources,
                    }
                )
                .execute()
            )
            plan_id = (row.data or [{}])[0].get("id")
            await self.log_step("persist_plan", "completed", {"plan_id": plan_id})
        except Exception as exc:
            # If a duplicate plan exists, fetch it instead — idempotent retries.
            if "duplicate key" in str(exc).lower():
                existing = await asyncio.to_thread(
                    lambda: get_service_client()
                    .table("onboarding_plans")
                    .select("id")
                    .eq("user_id", self.hire["user_id"])
                    .maybe_single()
                    .execute()
                )
                plan_id = (existing.data or {}).get("id") if existing else None
                await self.log_step("persist_plan", "skipped", {"reason": "exists", "plan_id": plan_id})
            else:
                await self.log_step("persist_plan", "failed", error=str(exc))
                raise

        await self._stamp_status(status="plan_generated", plan_id=plan_id)

        # ── Step 3: Notion page (best-effort) ─────────────────────────
        notion_result = await self._maybe_create_notion_page(plan_text=plan_text)

        # ── Step 4: welcome email ─────────────────────────────────────
        org_name = await self._resolve_org_name()
        welcome_sent = False
        if self.hire.get("email"):
            await self.log_step("send_welcome", "started")
            try:
                await send_email_event(
                    event_type="onboarding_welcome",
                    to=self.hire["email"],
                    user_id=self.hire["user_id"],
                    org_id=self.org_id,
                    dedupe_key=f"onboarding-{self.hire['user_id']}",
                    data={
                        "first_name": self._first_name(),
                        "role": self._role_label(),
                        "org_name": org_name,
                        "start_date": self.hire.get("start_date"),
                        "notion_url": notion_result.get("url") if notion_result else None,
                        "plan_preview": plan_text[:600] + ("…" if len(plan_text) > 600 else ""),
                        "app_url": app_url,
                    },
                )
                welcome_sent = True
                await self.log_step("send_welcome", "completed")
                if plan_id:
                    await asyncio.to_thread(
                        lambda: get_service_client()
                        .table("onboarding_plans")
                        .update({"welcome_email_sent_at": datetime.now(timezone.utc).isoformat()})
                        .eq("id", plan_id)
                        .execute()
                    )
            except Exception as exc:
                # Don't fail the run for a transient email outage; the worker
                # already has retry built in for the actual delivery step.
                await self.log_step("send_welcome", "failed", error=str(exc))
        else:
            await self.log_step("send_welcome", "skipped", {"reason": "no_email"})

        # ── Step 5: manager Slack DM ──────────────────────────────────
        manager_notified = await self._maybe_notify_manager(
            plan_id=plan_id,
            notion_url=(notion_result or {}).get("url"),
            app_url=app_url,
        )

        await self._stamp_status(status="completed", plan_id=plan_id)

        return {
            "plan_id": plan_id,
            "notion_url": (notion_result or {}).get("url"),
            "welcome_email_sent": welcome_sent,
            "manager_notified": manager_notified,
        }

    # ── Sub-steps ──────────────────────────────────────────────────────

    def _build_plan_prompt(self) -> str:
        role_label = self._role_label()
        start_date = self.hire.get("start_date") or "their first day"
        name = self.hire.get("name") or "the new hire"
        return (
            f"Create a comprehensive 90-day onboarding plan for {name}, "
            f"who is joining as {role_label} starting {start_date}.\n\n"
            "Structure the plan with these sections, each as a numbered list:\n"
            "  1. Day 1 checklist (5-8 concrete tasks)\n"
            "  2. Week 1 goals (3-5 outcomes)\n"
            "  3. 30-day milestones\n"
            "  4. 60-day milestones\n"
            "  5. 90-day milestones\n"
            "  6. Key contacts and where to ask for help\n\n"
            "Search the company knowledge base for: handbook, role-specific docs, "
            "team norms, tools they'll use. Reference real documents where "
            "possible. Where the knowledge base lacks specifics, leave a clear "
            "`[manager to fill in]` placeholder instead of making things up. "
            "Keep the tone warm but practical."
        )

    async def _maybe_create_notion_page(self, *, plan_text: str) -> dict[str, Any] | None:
        # Cheap connectivity probe before we go fishing for a parent page.
        svc = get_service_client()
        connected = await asyncio.to_thread(
            lambda: svc.table("notion_integrations")
            .select("id")
            .eq("org_id", self.org_id)
            .maybe_single()
            .execute()
        )
        if not connected or not connected.data:
            await self.log_step("create_notion_page", "skipped", {"reason": "notion_not_connected"})
            return None

        try:
            targets = await notion_service.list_write_targets(org_id=self.org_id, query="Onboarding")
        except Exception as exc:
            await self.log_step("create_notion_page", "failed", error=f"list_targets: {exc}")
            return None

        if not targets:
            # Fall back to the first writable page available.
            try:
                targets = await notion_service.list_write_targets(org_id=self.org_id)
            except Exception:
                targets = []
        if not targets:
            await self.log_step(
                "create_notion_page",
                "skipped",
                {"reason": "no_writable_parent"},
            )
            return None

        parent = targets[0]
        title = f"Onboarding — {self.hire.get('name') or 'New Hire'} ({self._role_label()})"

        await self.log_step("create_notion_page", "started", {"parent_page_id": parent.get("id")})
        try:
            page = await notion_service.create_page(
                org_id=self.org_id,
                parent_page_id=parent["id"],
                title=title,
                content=plan_text,
            )
        except PermissionError as exc:
            await self.log_step("create_notion_page", "failed", error=f"permission: {exc}")
            return None
        except Exception as exc:
            await self.log_step("create_notion_page", "failed", error=str(exc))
            return None

        await self.log_step("create_notion_page", "completed", page)
        try:
            await asyncio.to_thread(
                lambda: get_service_client()
                .table("onboarding_plans")
                .update(
                    {
                        "notion_page_id": page.get("page_id"),
                        "notion_page_url": page.get("url"),
                    }
                )
                .eq("user_id", self.hire["user_id"])
                .execute()
            )
        except Exception:
            pass
        return page

    async def _maybe_notify_manager(
        self,
        *,
        plan_id: str | None,
        notion_url: str | None,
        app_url: str,
    ) -> bool:
        manager = await self._resolve_manager()
        if not manager:
            await self.log_step("notify_manager", "skipped", {"reason": "no_manager"})
            return False

        org_name = await self._resolve_org_name()
        plan_link = notion_url or (f"{app_url}/onboarding/{plan_id}" if plan_id else app_url)
        text = (
            f"Hi *{manager['display_name'] or 'there'}* — "
            f"{self.hire.get('name') or 'your new teammate'}'s onboarding plan is ready.\n\n"
            f"*Role:* {self._role_label()}\n"
            f"*Start date:* {self.hire.get('start_date') or 'TBD'}\n\n"
            f"<{plan_link}|View their 90-day plan>"
        )

        await self.log_step("notify_manager", "started")
        try:
            result = await slack_service.send_dm(
                org_id=self.org_id,
                user_email=manager["email"],
                text=text,
            )
        except PermissionError as exc:
            await self.log_step("notify_manager", "skipped", {"reason": str(exc)})
            return False
        except Exception as exc:
            await self.log_step("notify_manager", "failed", error=str(exc))
            return False

        if not result:
            await self.log_step("notify_manager", "skipped", {"reason": "manager_not_on_slack"})
            return False

        await self.log_step("notify_manager", "completed", result)
        if plan_id:
            try:
                await asyncio.to_thread(
                    lambda: get_service_client()
                    .table("onboarding_plans")
                    .update({"manager_notified_at": datetime.now(timezone.utc).isoformat()})
                    .eq("id", plan_id)
                    .execute()
                )
            except Exception:
                pass
        return True

    # Org name resolver moved up for clarity. (Unused after refactor.)
    async def _org_name(self) -> str:  # pragma: no cover — kept for backwards compat
        return await self._resolve_org_name()
