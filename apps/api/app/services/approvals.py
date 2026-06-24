"""Internal helpers for the Day-6 approval workflow.

Two responsibilities only:

1. Magic-link token minting + verification. Plaintext token lives ONLY in
   the email URL; we persist sha256(secret || token). A leaked DB therefore
   doesn't grant approval rights — an attacker would also need the
   `internal_email_secret`.

2. Execution-action validation + fan-out. After an approval flips to
   'approved' we synthesise the same Inngest event that the
   gmail/slack/notion/gdocs routers send, so the existing workers handle
   everything. Keeping execution in one place means the approve-via-Slack,
   approve-via-magic-link, and approve-via-web paths share one execution
   surface.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

import inngest

from app.config import get_settings
from app.inngest.client import get_inngest_client
from app.observability import get_logger

log = get_logger(__name__)

# Channels recognised by execution_action.channel. Adding a new outbound
# channel: add the literal, add a branch in `dispatch_execution`.
#
# "agent" is the Day-14 channel for API-triggered agent runs. On approval
# we fan out the appropriate `agent/<type>/triggered-api` Inngest event
# instead of sending a side effect directly.
ExecutionChannel = Literal["gmail", "slack", "notion", "gdocs", "agent", "autoflow"]

# Plaintext tokens are 32 url-safe bytes (~43 chars). Long enough to be
# unguessable; short enough that the email URL stays readable.
_TOKEN_BYTES = 32

# Magic-link TTL. Longer than the 24h email-reminder window so a delayed
# approver who only opens email next morning still gets one-click.
TOKEN_TTL_HOURS = 24 * 7


def mint_token() -> tuple[str, str, datetime]:
    """Return (plaintext_token, token_hash, expires_at).

    plaintext_token → put in the email URL.
    token_hash      → persist on the approval row.
    expires_at      → persist on the approval row.
    """
    raw = secrets.token_urlsafe(_TOKEN_BYTES)
    digest = hash_token(raw)
    expires_at = datetime.now(UTC).replace(microsecond=0)
    from datetime import timedelta

    expires_at = expires_at + timedelta(hours=TOKEN_TTL_HOURS)
    return raw, digest, expires_at


def hash_token(raw_token: str) -> str:
    """sha256 of (internal_email_secret || raw_token). HMAC isn't strictly
    needed because we're hashing a high-entropy random token, not a low-
    entropy secret; using a keyed digest just makes the hash table useless
    if the DB leaks without the app secret."""
    secret = (get_settings().internal_email_secret or "").encode("utf-8")
    return hmac.new(secret, raw_token.encode("utf-8"), hashlib.sha256).hexdigest()


def consteq(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


# ── Execution fan-out ──────────────────────────────────────────────────────


_ALLOWED_CHANNELS: set[str] = {"gmail", "slack", "notion", "gdocs", "agent", "autoflow"}

# Agent types accepted on the public API + by the agent-channel approval.
# Kept in sync with services/agent_registry.py:AGENT_REGISTRY.
_AGENT_CHANNEL_TYPES: set[str] = {
    "onboarding",
    "policy_propagation",
    "support_response",
    "weekly_digest",
}


def validate_execution_action(action: dict[str, Any] | None) -> dict[str, Any]:
    """Boundary check for the execution_action JSONB on insert.

    We trust the FastAPI caller (an authenticated org member submitting from
    the UI), but execution_action is the field that controls a side effect
    so it gets a proper validator anyway. Returns the normalised shape we
    persist; raises ValueError on bad input."""
    if not isinstance(action, dict):
        raise ValueError("execution_action must be an object")

    channel = action.get("channel")
    if channel not in _ALLOWED_CHANNELS:
        raise ValueError(f"unsupported channel '{channel}'")

    params = action.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError("execution_action.params must be an object")

    # Per-channel required fields. Mirrors what each outbound Inngest
    # function actually consumes — if you change a worker's contract, add
    # the missing key here too.
    if channel == "gmail":
        for key in ("to", "subject", "body"):
            if not params.get(key):
                raise ValueError(f"gmail.{key} is required")
    elif channel == "slack":
        for key in ("channel_id", "text"):
            if not params.get(key):
                raise ValueError(f"slack.{key} is required")
    elif channel == "notion":
        for key in ("parent_page_id", "title", "content"):
            if not params.get(key):
                raise ValueError(f"notion.{key} is required")
    elif channel == "gdocs":
        for key in ("title", "content"):
            if not params.get(key):
                raise ValueError(f"gdocs.{key} is required")
    elif channel == "agent":
        agent_type = params.get("agent_type")
        if agent_type not in _AGENT_CHANNEL_TYPES:
            raise ValueError(
                f"agent.agent_type must be one of {sorted(_AGENT_CHANNEL_TYPES)}"
            )
        if not isinstance(params.get("agent_input"), dict):
            raise ValueError("agent.agent_input must be an object")
    elif channel == "autoflow":
        # Autoflow approvals (Agent2 Day 1). Approval is the gate; on resolution
        # the autoflow_run resumes (or cancels). params carry the run id so the
        # resume handler can pick the right row without back-references.
        if not params.get("autoflow_run_id"):
            raise ValueError("autoflow.autoflow_run_id is required")

    return {"channel": channel, "params": params}


async def dispatch_execution(
    *,
    approval_id: str,
    message_id: str,
    org_id: str,
    requested_by: str,
    action: dict[str, Any],
) -> str:
    """Fire the appropriate Inngest event to actually do the work.

    Returns the job_id so the caller can persist it for tracing. Idempotency
    is enforced via Inngest event id `approval-execute-{approval_id}`.
    """
    channel: str = action["channel"]
    params: dict[str, Any] = action.get("params") or {}
    job_id = str(uuid.uuid4())

    client = get_inngest_client()

    if channel == "gmail":
        await client.send(
            inngest.Event(
                name="gmail/send-email",
                data={
                    "job_id": job_id,
                    "message_id": message_id,
                    "org_id": org_id,
                    "user_id": requested_by,
                    "to": params["to"],
                    "subject": params["subject"],
                    "body": params["body"],
                    "cc": params.get("cc"),
                    "reply_to": params.get("reply_to"),
                    "approval_id": approval_id,
                },
                id=f"approval-execute-{approval_id}",
            )
        )
    elif channel == "slack":
        await client.send(
            inngest.Event(
                name="slack/post-message",
                data={
                    "job_id": job_id,
                    "message_id": message_id,
                    "org_id": org_id,
                    "channel_id": params["channel_id"],
                    "channel_name": params.get("channel_name") or params["channel_id"],
                    "text": params["text"],
                    "thread_ts": params.get("thread_ts"),
                    "approval_id": approval_id,
                },
                id=f"approval-execute-{approval_id}",
            )
        )
    elif channel == "notion":
        await client.send(
            inngest.Event(
                name="notion/create-page",
                data={
                    "job_id": job_id,
                    "message_id": message_id,
                    "org_id": org_id,
                    "parent_page_id": params["parent_page_id"],
                    "parent_page_title": params.get("parent_page_title") or "Notion",
                    "title": params["title"],
                    "content": params["content"],
                    "approval_id": approval_id,
                },
                id=f"approval-execute-{approval_id}",
            )
        )
    elif channel == "gdocs":
        await client.send(
            inngest.Event(
                name="gdocs/create-doc",
                data={
                    "job_id": job_id,
                    "message_id": message_id,
                    "org_id": org_id,
                    "title": params["title"],
                    "content": params["content"],
                    "share_with_email": params.get("share_with_email"),
                    "approval_id": approval_id,
                },
                id=f"approval-execute-{approval_id}",
            )
        )
    elif channel == "agent":
        # Day 14: fan out to the API-trigger pipeline. The pipeline owns
        # the agent_run lifecycle so we just pass through the validated
        # payload + cross-link the approval id. We also flip the run row's
        # status from pending_approval → running so the polling endpoint
        # immediately reflects that execution started; BaseAgent's idempotent
        # create on the worker side won't downgrade it.
        from app.database import get_service_client as _svc
        from app.services.agent_registry import dispatch_api_agent

        run_id = params.get("run_id") or job_id
        try:
            import asyncio as _asyncio
            client_db = _svc()
            await _asyncio.to_thread(
                lambda: client_db.table("agent_runs")
                .update({"status": "running"})
                .eq("id", run_id)
                .eq("status", "pending_approval")
                .execute()
            )
        except Exception as exc:
            log.warning(
                "agent_run_status_flip_failed run=%s err=%s", run_id, exc,
            )

        await dispatch_api_agent(
            org_id=org_id,
            agent_type=params["agent_type"],
            agent_input=params["agent_input"],
            output_channels=list(params.get("output_channels") or []),
            webhook_url=params.get("webhook_url"),
            api_key_id=params.get("api_key_id"),
            approval_id=approval_id,
            run_id=run_id,
        )
    elif channel == "autoflow":
        # Resume the held autoflow_run. Fired as a distinct Inngest event so
        # the resume function gets retries + observability separate from the
        # approval dispatch path.
        await client.send(
            inngest.Event(
                name="autoflow/resume",
                data={
                    "job_id": job_id,
                    "approval_id": approval_id,
                    "autoflow_run_id": params["autoflow_run_id"],
                    "org_id": org_id,
                    "approved": True,
                },
                id=f"approval-execute-{approval_id}",
            )
        )
    else:
        raise ValueError(f"unsupported channel '{channel}'")

    log.info(
        "approval_execution_dispatched",
        approval_id=approval_id,
        org_id=org_id,
        channel=channel,
        job_id=job_id,
    )
    return job_id


# Plans where Submit-for-Approval is enabled. The user picked "all paid
# plans" — Starter+ surfaces the button; free orgs see a paywall hint.
_PAID_PLANS: set[str] = {"starter", "growth", "business"}


def plan_can_request_approvals(plan: str | None) -> bool:
    return (plan or "").lower() in _PAID_PLANS
