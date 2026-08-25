"""Inngest functions for API-triggered agents (Agent Roadmap Day 14).

Two workers live here:

  1. onboarding-api-triggered    (event: agent/onboarding/triggered-api)
        Wraps OnboardingAgent.run_safely() for the public-API path. Distinct
        from `onboard-new-member` (invite-acceptance) so the audit row + the
        concurrency caps + the input shape are explicit.

  2. weekly-digest-api-triggered (event: agent/weekly-digest/triggered-api)
        Equivalent of the manual `email/weekly-digest-now` button but creates
        an agent_runs row.

Why we introduce dedicated events for onboarding + weekly-digest rather
than reusing `org/member-joined` / `email/weekly-digest-now`: the original
events carry user/session semantics (invitation flow, manual Settings
trigger). Mixing API-triggered context into those events would force every
downstream handler to learn about API-specific context. Cleaner to keep the
trigger path explicit.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import inngest

from app.config import get_settings
from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.services.agents.onboarding_agent import OnboardingAgent

log = logging.getLogger(__name__)

_inngest_client = get_inngest_client()


# ── 1. Onboarding via public API ───────────────────────────────────────────


@_inngest_client.create_function(
    fn_id="onboarding-api-triggered",
    trigger=inngest.TriggerEvent(event="agent/onboarding/triggered-api"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=2, key="event.data.org_id", scope="fn"),
    ],
)
async def onboarding_api_triggered(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    org_id: str = data["org_id"]
    run_id: str = data["run_id"]
    payload: dict[str, Any] = data.get("input") or {}

    # OnboardingAgent expects a `hire_data` dict shaped like the invite-
    # acceptance event. Adapt the public-API payload (which uses `name`,
    # `email`, `role`, `start_date`) into that shape. The `user_id` is the
    # canonical join key in our DB — if the caller didn't pass one (BambooHR
    # doesn't know our user ids), we mint a deterministic placeholder so the
    # plan row + agent_runs row stay consistent. The real user row will be
    # created lazily if/when they accept an invitation.
    import uuid as _uuid

    hire_data = {
        "user_id": payload.get("user_id")
        or str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"{org_id}:{payload.get('email')}")),
        "name": payload.get("name"),
        "email": payload.get("email"),
        "role_title": payload.get("role"),
        "role": payload.get("role"),
        "start_date": payload.get("start_date"),
        "department": payload.get("department"),
        # manager_user_id intentionally not resolved here — manager_email is
        # surfaced in the agent's plan but we don't auto-DM unknown users.
        "invited_by": None,
    }

    agent = OnboardingAgent(org_id=org_id, hire_data=hire_data)
    agent.run_id = run_id  # honour the API-supplied id for idempotency
    agent.input_data = hire_data
    agent.triggered_by = "api"

    try:
        result = await agent.run_safely()
        status = "completed"
        error: str | None = None
    except Exception as exc:
        status = "failed"
        error = str(exc) or exc.__class__.__name__
        result = {}
        log.warning(
            "onboarding_api_run_failed org=%s run=%s err=%s",
            org_id,
            run_id,
            error,
        )

    if status == "failed":
        # Re-raise so Inngest retries kick in.
        raise RuntimeError(error or "onboarding_failed")
    return {"status": "ok", "run_id": run_id, **(result or {})}


# ── 2. Weekly digest via public API ────────────────────────────────────────


@_inngest_client.create_function(
    fn_id="weekly-digest-api-triggered",
    trigger=inngest.TriggerEvent(event="agent/weekly-digest/triggered-api"),
    retries=1,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.org_id", scope="fn"),
    ],
)
async def weekly_digest_api_triggered(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    org_id: str = data["org_id"]
    run_id: str = data["run_id"]
    send_to_email: str | None = data.get("send_to_email")

    from app.services.email import send_email_event
    from app.services.email.worker import gather_weekly_stats

    settings = get_settings()
    svc = get_service_client()

    # Insert (or no-op on duplicate) the agent_runs row. The API endpoint
    # pre-creates it; an Inngest retry would find it already there.
    try:
        from app.services.agent_registry import precreate_agent_run

        await precreate_agent_run(
            run_id=run_id,
            org_id=org_id,
            agent_type="weekly_digest",
            triggered_by="api",
            triggered_by_user_id=None,
            input_data={"send_to_email": send_to_email},
        )
    except Exception:
        pass

    try:
        org_row = await asyncio.to_thread(
            lambda: svc.table("organizations")
            .select("id, name")
            .eq("id", org_id)
            .maybe_single()
            .execute()
        )
        if not org_row or not org_row.data:
            raise RuntimeError("org_not_found")

        stats = await gather_weekly_stats(org_id)

        recipients: list[tuple[str | None, str]] = []
        if send_to_email:
            recipients.append((None, send_to_email))
        else:
            # Default: every admin gets the digest. Matches the
            # weekly_digest_send_now helper behaviour.
            admins = await asyncio.to_thread(
                lambda: svc.table("users")
                .select("id")
                .eq("org_id", org_id)
                .eq("role", "admin")
                .execute()
            )
            ids = [u["id"] for u in (admins.data or [])]
            for aid in ids:
                try:
                    au = await asyncio.to_thread(
                        lambda i=aid: svc.auth.admin.get_user_by_id(i)
                    )
                    email = getattr(getattr(au, "user", None), "email", None)
                    if email:
                        recipients.append((aid, email))
                except Exception:
                    continue

        if not recipients:
            raise RuntimeError("no_recipients")

        sent = 0
        nonce = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        for user_id, email in recipients:
            await send_email_event(
                event_type="weekly_digest",
                to=email,
                user_id=user_id,
                org_id=org_id,
                dedupe_key=f"api-{run_id}-{nonce}",
                data={
                    "org_name": org_row.data["name"],
                    "app_url": settings.app_url,
                    **stats,
                },
            )
            sent += 1
        result = {"recipients": sent}
        # Stamp the run completed.
        await asyncio.to_thread(
            lambda: svc.table("agent_runs")
            .update(
                {
                    "status": "completed",
                    "output": result,
                    "completed_at": datetime.now(UTC).isoformat(),
                }
            )
            .eq("id", run_id)
            .execute()
        )
        status = "completed"
        error: str | None = None
    except Exception as exc:
        status = "failed"
        error = str(exc) or exc.__class__.__name__
        result = {}
        await asyncio.to_thread(
            lambda: svc.table("agent_runs")
            .update(
                {
                    "status": "failed",
                    "error": error[:2000],
                    "completed_at": datetime.now(UTC).isoformat(),
                }
            )
            .eq("id", run_id)
            .execute()
        )
        log.warning(
            "weekly_digest_api_run_failed org=%s run=%s err=%s",
            org_id,
            run_id,
            error,
        )

    if status == "failed":
        raise RuntimeError(error or "weekly_digest_failed")
    return {"status": "ok", "run_id": run_id, **(result or {})}


FUNCTIONS = [
    onboarding_api_triggered,
    weekly_digest_api_triggered,
]
