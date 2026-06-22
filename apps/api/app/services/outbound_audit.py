"""Audit trail for outbound writes (Agent Day 3-4 hardening).

Mirrors what BaseAgent does for the autonomous agents: every outbound write
(Slack post, Notion create-page, Gdocs export, Gmail send) gets an
`agent_runs` row so the /admin/agent-runs feed has a single ledger of
"who pushed what where, and whether it landed."

We deliberately do NOT extend BaseAgent for this — outbound writes are a
shallow, one-step "queue → confirm" pattern, not a tool-using LLM loop, and
trying to share the lifecycle machinery would just shoehorn a square peg.
Instead, this module provides three small helpers:

  * `record_queued()` — called from the router when it enqueues the Inngest
    event. Inserts a row with status='running'.
  * `record_sent()` / `record_failed()` — called from the Inngest worker on
    terminal status. Updates the same row.

The `id` is the same UUID we already mint as `job_id` in the routers, so
the worker can find the row without an extra lookup.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Literal

from app.database import get_service_client

log = logging.getLogger(__name__)


OutboundChannel = Literal["slack", "gmail", "notion", "gdocs"]


def _agent_type(channel: OutboundChannel) -> str:
    # Keep these in lockstep with the admin UI's filter chips. Free-text by
    # design (migration 029) so we can add new channels without a migration,
    # but the prefix lets us index/filter (see migration 044).
    return f"outbound_write_{channel}"


async def record_queued(
    *,
    run_id: str,
    channel: OutboundChannel,
    org_id: str,
    user_id: str | None,
    message_id: str,
    destination: str,
    input_payload: dict[str, Any],
) -> None:
    """Insert the agent_runs row at queue time. Best-effort: a failure here
    must NOT prevent the actual outbound write from going out — the audit
    trail is observability, not load-bearing for delivery. We log and move
    on so a transient DB blip doesn't break the user's flow.
    """
    svc = get_service_client()
    # Strip large fields from the payload before persisting — we don't want
    # to duplicate full Slack/Gmail bodies into agent_runs.input when the
    # message itself already lives in `messages.content`. Keep just the
    # routing fields plus a content_chars summary.
    safe_input = _redact_input(input_payload)
    row = {
        "id": run_id,
        "org_id": org_id,
        "agent_type": _agent_type(channel),
        "triggered_by": "user",
        "triggered_by_user_id": user_id,
        "status": "running",
        "input": {
            "message_id": message_id,
            "channel": channel,
            "destination": destination,
            **safe_input,
        },
        "started_at": datetime.now(UTC).isoformat(),
    }
    try:
        await asyncio.to_thread(
            lambda: svc.table("agent_runs").insert(row).execute()
        )
    except Exception as exc:
        # Most likely cause: duplicate primary key from an at-least-once
        # Inngest retry that re-queued (we use job_id as run_id). The worker
        # will still flip status on the existing row.
        log.info(
            "outbound_audit_insert_skipped run_id=%s channel=%s err=%s",
            run_id,
            channel,
            str(exc)[:120],
        )


async def record_sent(
    *,
    run_id: str,
    output: dict[str, Any],
) -> None:
    """Mark the row completed. `output` is what the worker would have
    returned to the caller — channel-specific fields like `url`,
    `external_id`, `slack_ts`, etc.
    """
    svc = get_service_client()
    now = datetime.now(UTC).isoformat()
    try:
        await asyncio.to_thread(
            lambda: svc.table("agent_runs")
            .update(
                {
                    "status": "completed",
                    "output": output,
                    "completed_at": now,
                }
            )
            .eq("id", run_id)
            .execute()
        )
    except Exception as exc:
        log.warning("outbound_audit_complete_failed run_id=%s err=%s", run_id, exc)


async def record_failed(
    *,
    run_id: str,
    error: str,
    output: dict[str, Any] | None = None,
) -> None:
    svc = get_service_client()
    now = datetime.now(UTC).isoformat()
    try:
        await asyncio.to_thread(
            lambda: svc.table("agent_runs")
            .update(
                {
                    "status": "failed",
                    "error": error[:2000],
                    "output": output or {},
                    "completed_at": now,
                }
            )
            .eq("id", run_id)
            .execute()
        )
    except Exception as exc:
        log.warning("outbound_audit_fail_failed run_id=%s err=%s", run_id, exc)


# Fields safe to keep verbatim on agent_runs.input. Everything else gets
# dropped or summarized so we don't double-store full message bodies.
_INPUT_PASSTHROUGH = {
    "channel_id",
    "channel_name",
    "thread_ts",
    "parent_page_id",
    "parent_page_title",
    "title",
    "share_with_email",
    "to_emails",
    "subject",
}


def _redact_input(payload: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in payload.items():
        if key in _INPUT_PASSTHROUGH:
            redacted[key] = value
    body = payload.get("text") or payload.get("content") or payload.get("body_html")
    if isinstance(body, str):
        redacted["content_chars"] = len(body)
    return redacted
