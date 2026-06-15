"""Agent completion fan-out (Agent Roadmap Day 14).

When an agent_runs row transitions to `completed` or `failed`, two parallel
notification paths fire:

  1. Org-wide outbound webhooks (services/webhooks.py) for
     `agent.completed` / `agent.failed` — for orgs that have configured a
     webhook to receive every agent event.

  2. Per-request webhook (the `webhook_url` parameter on the trigger
     endpoint). HMAC-signed with the API key's hash so receivers can verify
     authenticity. This is the "one-shot integration" path — BambooHR
     triggers an onboarding agent and gets the result POSTed back without
     needing to subscribe to webhooks first.

Both paths are best-effort. A failed callback never reverts the agent run.

We sign one-shot callbacks with the SAME header shape as the configured
webhooks (services/webhooks.py + inngest/webhook_functions.py) so customers
learn one signature scheme. The secret for one-shot callbacks is derived
from the API key id via HMAC(internal_email_secret, api_key_id) — that way
the receiver can compute the secret server-side without us ever sending the
plaintext API key back out.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any

import inngest

from app.config import get_settings
from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.services.webhooks import trigger_event as trigger_webhook_event

log = logging.getLogger(__name__)


def derive_callback_secret(api_key_id: str) -> str:
    """Per-key HMAC secret used on one-shot callback signatures.

    Receivers compute the same value server-side using the api_key id we
    echo in the callback body. Rotation is automatic on key revocation —
    issuing a new key derives a new secret.
    """
    settings = get_settings()
    base = (settings.internal_email_secret or "").encode("utf-8")
    return hmac.new(base, api_key_id.encode("utf-8"), hashlib.sha256).hexdigest()


async def fire_agent_lifecycle_events(
    *,
    run_id: str,
    org_id: str,
    agent_type: str,
    status: str,  # 'completed' | 'failed'
    output: dict[str, Any] | None,
    error: str | None,
    api_context: dict[str, Any] | None,
) -> None:
    """Fan-out helper called by every agent on terminal status.

    Idempotent: the org-webhook path dedupes via Inngest event id +
    delivery audit, and the one-shot callback uses the run_id in its
    Inngest event id so a retry collapses into one POST.
    """
    if status not in ("completed", "failed"):
        return

    event_name = "agent.completed" if status == "completed" else "agent.failed"

    payload = {
        "agent_run_id": run_id,
        "agent_type": agent_type,
        "status": status,
        "output": output or {},
        "error": error,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

    # Path 1: configured outbound webhooks for the org.
    try:
        await trigger_webhook_event(
            org_id=org_id, event=event_name, payload=payload,
        )
    except Exception as exc:
        log.warning(
            "agent_lifecycle_webhook_failed run=%s err=%s", run_id, exc,
        )

    # Path 2: per-request callback URL (Day 14 trigger API).
    if api_context and api_context.get("webhook_url"):
        try:
            client = get_inngest_client()
            await client.send(
                inngest.Event(
                    name="agent/api-callback",
                    data={
                        "run_id": run_id,
                        "org_id": org_id,
                        "api_key_id": api_context.get("api_key_id"),
                        "webhook_url": api_context["webhook_url"],
                        "payload": payload,
                    },
                    id=f"agent-callback-{run_id}-{status}",
                )
            )
        except Exception as exc:
            log.warning(
                "agent_lifecycle_callback_enqueue_failed run=%s err=%s",
                run_id,
                exc,
            )


async def load_api_context_from_run(run_id: str) -> dict[str, Any] | None:
    """Re-load the API trigger context from the agent_runs.input JSONB.

    Agents pre-create their run row with `_api_context` embedded in input
    (when triggered via API). Workers call this on terminal status to
    decide whether to fire the one-shot callback.
    """
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("agent_runs")
        .select("input")
        .eq("id", run_id)
        .maybe_single()
        .execute()
    )
    if not res or not res.data:
        return None
    inp = res.data.get("input") or {}
    return inp.get("_api_context") if isinstance(inp, dict) else None
