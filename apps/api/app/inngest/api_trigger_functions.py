"""Inngest functions for API-triggered agents (Agent Roadmap Day 14).

Three workers live here:

  1. onboarding-api-triggered    (event: agent/onboarding/triggered-api)
        Wraps OnboardingAgent.run_safely() for the public-API path. Distinct
        from `onboard-new-member` (invite-acceptance) so the audit row + the
        concurrency caps + the input shape are explicit.

  2. weekly-digest-api-triggered (event: agent/weekly-digest/triggered-api)
        Equivalent of the manual `email/weekly-digest-now` button but creates
        an agent_runs row + fires the agent-lifecycle callback.

  3. agent-api-callback          (event: agent/api-callback)
        HMAC-signs and POSTs the one-shot webhook_url passed on the trigger
        request. Mirrors the org-webhook deliver function but with a smaller
        retry budget and per-key derived secret.

Why we introduce dedicated events for onboarding + weekly-digest rather
than reusing `org/member-joined` / `email/weekly-digest-now`: the original
events carry user/session semantics (invitation flow, manual Settings
trigger). Mixing API-triggered context into those events would force every
downstream handler to learn about API callbacks. Cleaner to keep the
trigger path explicit.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
import inngest

from app.config import get_settings
from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.services.agent_callbacks import (
    derive_callback_secret,
    fire_agent_lifecycle_events,
)
from app.services.agents.onboarding_agent import OnboardingAgent

log = logging.getLogger(__name__)

_inngest_client = get_inngest_client()


# Match the existing webhook deliver function so customers learn one shape.
_HEADER_EVENT = "X-NirnayaIQ-Event"
_HEADER_DELIVERY = "X-NirnayaIQ-Delivery"
_HEADER_SIGNATURE = "X-NirnayaIQ-Signature"
_HEADER_KEY_ID = "X-NirnayaIQ-Api-Key-Id"
_CALLBACK_TIMEOUT_SECONDS = 10.0


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
    # Stash the api_context inside input_data so post-run callbacks find it.
    api_context = {
        "webhook_url": data.get("webhook_url"),
        "api_key_id": data.get("api_key_id"),
        "approval_id": data.get("approval_id"),
        "run_id": run_id,
    }
    agent.input_data = {**hire_data, "_api_context": api_context}
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

    await fire_agent_lifecycle_events(
        run_id=run_id,
        org_id=org_id,
        agent_type="onboarding",
        status=status,
        output=result if isinstance(result, dict) else {},
        error=error,
        api_context=api_context,
    )

    if status == "failed":
        # Re-raise after firing callbacks so Inngest retries kick in.
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
    api_context = {
        "webhook_url": data.get("webhook_url"),
        "api_key_id": data.get("api_key_id"),
        "approval_id": data.get("approval_id"),
        "run_id": run_id,
    }

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
            input_data={"send_to_email": send_to_email, "_api_context": api_context},
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
        nonce = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
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
                    "completed_at": datetime.now(timezone.utc).isoformat(),
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
                    "completed_at": datetime.now(timezone.utc).isoformat(),
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

    await fire_agent_lifecycle_events(
        run_id=run_id,
        org_id=org_id,
        agent_type="weekly_digest",
        status=status,
        output=result,
        error=error,
        api_context=api_context,
    )

    if status == "failed":
        raise RuntimeError(error or "weekly_digest_failed")
    return {"status": "ok", "run_id": run_id, **(result or {})}


# ── 3. Per-request agent callback delivery ─────────────────────────────────


@_inngest_client.create_function(
    fn_id="agent-api-callback",
    trigger=inngest.TriggerEvent(event="agent/api-callback"),
    retries=3,
    concurrency=[
        inngest.Concurrency(limit=4, key="event.data.org_id", scope="fn"),
    ],
)
async def agent_api_callback(ctx: inngest.Context) -> dict[str, Any]:
    """POST the agent result to the caller-supplied webhook_url.

    Signature: HMAC-SHA256 over the raw JSON body using the per-key derived
    secret (see agent_callbacks.derive_callback_secret). Receivers compute
    the same value from the api_key_id we echo in the X-NirnayaIQ-Api-Key-Id
    header + the internal_email_secret they share with us (configured at
    integration setup).
    """
    data = ctx.event.data
    run_id: str = data["run_id"]
    webhook_url: str = data["webhook_url"]
    api_key_id: str | None = data.get("api_key_id")
    org_id: str = data["org_id"]
    payload: dict[str, Any] = data.get("payload") or {}
    attempt = (getattr(ctx, "attempt", 0) or 0) + 1

    body = {
        "event": "agent.completed" if payload.get("status") == "completed" else "agent.failed",
        "data": payload,
        "delivered_at": datetime.now(timezone.utc).isoformat(),
        "attempt": attempt,
    }
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        _HEADER_EVENT: body["event"],
        _HEADER_DELIVERY: ctx.event.id or run_id,
        "User-Agent": "NirnayaIQ-AgentCallbacks/1.0",
    }
    if api_key_id:
        secret = derive_callback_secret(api_key_id)
        sig = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
        headers[_HEADER_SIGNATURE] = f"sha256={sig}"
        headers[_HEADER_KEY_ID] = api_key_id

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_CALLBACK_TIMEOUT_SECONDS, connect=3.0)
        ) as client:
            resp = await client.post(webhook_url, content=raw, headers=headers)
        log.info(
            "agent_callback_delivered run=%s status=%s url=%s attempt=%s",
            run_id,
            resp.status_code,
            webhook_url,
            attempt,
        )
        # Treat 5xx + 429 as transient. 4xx is permanent (don't retry — the
        # receiver's URL or signature config is broken; retrying won't fix it).
        if resp.status_code >= 500 or resp.status_code == 429:
            raise RuntimeError(f"callback HTTP {resp.status_code}")
        return {
            "status": "delivered",
            "status_code": resp.status_code,
            "attempt": attempt,
        }
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        log.warning(
            "agent_callback_transient_failure run=%s url=%s err=%s attempt=%s",
            run_id,
            webhook_url,
            exc,
            attempt,
        )
        raise
    except Exception as exc:
        # Permanent (e.g. 4xx raised above). Don't bubble up to Inngest as a
        # retryable — log + drop.
        log.warning(
            "agent_callback_permanent_failure run=%s url=%s err=%s",
            run_id,
            webhook_url,
            exc,
        )
        return {"status": "failed_permanent", "attempt": attempt, "error": str(exc)[:200]}


FUNCTIONS = [
    onboarding_api_triggered,
    weekly_digest_api_triggered,
    agent_api_callback,
]
