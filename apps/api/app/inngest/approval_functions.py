"""Approval-workflow Inngest pipeline (Agent Roadmap Day 6).

Functions:

  approval_requested
    Triggered by `approval/requested` (fired from the FastAPI POST /approvals
    endpoint). Sends the approver an email with a magic-link AND a Slack DM
    with Approve/Reject buttons. Then sleeps 24h; if the approval is still
    pending, sends a reminder email. The plaintext token is in the event
    payload (not persisted) so the worker can build the magic-link URL.

  approval_resolved
    Triggered by `approval/resolved`. Notifies the original requester by
    email that their request was approved/rejected, and updates the Slack
    DM in place if one was sent.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import inngest

from app.config import get_settings
from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.observability import get_logger
from app.services.email import send_email_event
from app.services.integrations import slack as slack_service
from app.services.integrations.slack_block_kit import (
    approval_request_blocks,
    approval_resolved_blocks,
)

log = get_logger(__name__)

_inngest_client = get_inngest_client()


# ── Helpers ─────────────────────────────────────────────────────────────────


def _channel_label(channel: str) -> str:
    return {
        "gmail": "Send via Gmail",
        "slack": "Post to Slack",
        "notion": "Create Notion page",
        "gdocs": "Create Google Doc",
        # Day 14: API-triggered agent runs gated through the human-approval
        # workflow. Surface a friendly label per agent_type so the approver
        # email reads "Run onboarding" rather than "Run agent".
        "agent": "Run agent",
    }.get(channel, channel.title())


def _destination_from_action(action: dict[str, Any]) -> str | None:
    params = (action or {}).get("params") or {}
    ch = (action or {}).get("channel")
    if ch == "gmail":
        return params.get("to")
    if ch == "slack":
        return params.get("channel_name") or params.get("channel_id")
    if ch in {"notion", "gdocs"}:
        return params.get("title") or params.get("parent_page_title")
    if ch == "agent":
        agent_type = params.get("agent_type") or "agent"
        agent_input = params.get("agent_input") or {}
        # Surface the most identifying scalar from the input — usually an
        # email (onboarding/support) or a document id (policy). Falls back
        # to the agent type alone.
        for key in ("email", "from_email", "document_id", "send_to_email"):
            v = agent_input.get(key)
            if isinstance(v, str) and v:
                return f"{agent_type} · {v}"
        return agent_type
    return None


async def _load_approval(approval_id: str) -> dict[str, Any] | None:
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("approvals").select("*").eq("id", approval_id).maybe_single().execute()
    )
    return row.data if row and row.data else None


async def _resolve_user(user_id: str | None) -> dict[str, Any]:
    """Returns {email, display_name} for a users row. None values on failure.

    Tolerates None — API-triggered approvals don't have a workspace user as
    the requester, so we return a placeholder "API" identity instead of
    blowing up the email-formatting step.
    """
    if not user_id:
        return {"email": None, "display_name": "API"}
    svc = get_service_client()
    profile = await asyncio.to_thread(
        lambda: svc.table("users")
        .select("display_name")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    display_name: str | None = (profile.data or {}).get("display_name") if profile else None
    email: str | None = None
    try:
        au = await asyncio.to_thread(lambda: svc.auth.admin.get_user_by_id(user_id))
        email = getattr(getattr(au, "user", None), "email", None)
    except Exception:
        pass
    return {"email": email, "display_name": display_name}


# ── approval/requested → notify approver (email + Slack) + 24h reminder ─────


@_inngest_client.create_function(
    fn_id="approval-requested",
    trigger=inngest.TriggerEvent(event="approval/requested"),
    retries=2,
)
async def approval_requested_function(ctx: inngest.Context) -> dict[str, Any]:
    step = ctx.step
    data = ctx.event.data
    approval_id: str = data["approval_id"]
    raw_token: str = data["raw_token"]

    settings = get_settings()
    app_url = settings.app_url.rstrip("/")

    # Step 1: load approval state. If the row's missing the requester
    # likely revoked it before we got here.
    approval = await step.run("load-approval", lambda: _load_approval(approval_id))
    if not approval or approval.get("status") != "pending":
        return {"status": "skipped", "reason": "not_pending"}

    requester = await step.run("load-requester", lambda: _resolve_user(approval["requested_by"]))
    approver = await step.run("load-approver", lambda: _resolve_user(approval["approver_id"]))

    action = approval["execution_action"] or {}
    channel = action.get("channel") or "gmail"
    destination = _destination_from_action(action)

    magic_url = f"{app_url}/approve/{raw_token}"
    web_url = f"{app_url}/approvals/{approval_id}"

    # Step 2: email the approver.
    if approver.get("email"):
        await step.run(
            "email-approver",
            lambda: send_email_event(
                event_type="approval_request",
                to=approver["email"],
                user_id=approval["approver_id"],
                org_id=approval["org_id"],
                dedupe_key=f"approval-{approval_id}",
                data={
                    "approver_name": approver.get("display_name") or "there",
                    "requester_name": requester.get("display_name") or "A teammate",
                    "channel_label": _channel_label(channel),
                    "destination": destination,
                    "preview_text": (approval.get("preview_text") or "")[:1800],
                    "approve_url": f"{magic_url}?decision=approve",
                    "reject_url": f"{magic_url}?decision=reject",
                    "web_url": web_url,
                    "app_url": app_url,
                },
            ),
        )

    # Step 3: Slack DM (best-effort — workspace may not have Slack hooked up
    # or the approver may not be on Slack). The result {ts, channel} is
    # stored so the resolve flow can update the message in place.
    async def _send_slack_dm() -> dict[str, Any] | None:
        if not approver.get("email"):
            return None
        try:
            return await slack_service.send_dm(
                org_id=approval["org_id"],
                user_email=approver["email"],
                text=(
                    f"*Approval requested* — {_channel_label(channel)}"
                    f"{f' · {destination}' if destination else ''}"
                ),
                blocks=approval_request_blocks(
                    approval_id=approval_id,
                    requester_name=requester.get("display_name"),
                    channel=channel,
                    destination=destination,
                    preview_text=approval.get("preview_text"),
                    web_url=web_url,
                ),
            )
        except PermissionError as exc:
            log.info("approval_slack_dm_skipped", approval_id=approval_id, reason=str(exc))
            return None
        except Exception as exc:
            log.warning("approval_slack_dm_failed", approval_id=approval_id, error=str(exc))
            return None

    slack_msg = await step.run("slack-dm-approver", _send_slack_dm)
    if slack_msg and isinstance(slack_msg, dict):
        # Persist channel + ts so the resolve flow can chat.update the
        # message in place. Service-role bypasses RLS.
        async def _persist_slack_ref() -> None:
            svc = get_service_client()
            await asyncio.to_thread(
                lambda: svc.table("approvals")
                .update({"slack_message": slack_msg})
                .eq("id", approval_id)
                .execute()
            )

        await step.run("persist-slack-ref", _persist_slack_ref)

    # Step 4: sleep 24h, then send a reminder if still pending.
    await step.sleep("wait-24h", timedelta(hours=24))

    refreshed = await step.run("recheck-status", lambda: _load_approval(approval_id))
    if not refreshed or refreshed.get("status") != "pending":
        return {"status": "resolved_before_reminder"}

    if approver.get("email"):
        await step.run(
            "send-reminder",
            lambda: send_email_event(
                event_type="approval_reminder",
                to=approver["email"],
                user_id=approval["approver_id"],
                org_id=approval["org_id"],
                dedupe_key=f"approval-reminder-{approval_id}",
                data={
                    "approver_name": approver.get("display_name") or "there",
                    "requester_name": requester.get("display_name") or "A teammate",
                    "channel_label": _channel_label(channel),
                    "destination": destination,
                    "preview_text": (approval.get("preview_text") or "")[:1800],
                    "approve_url": f"{magic_url}?decision=approve",
                    "reject_url": f"{magic_url}?decision=reject",
                    "web_url": web_url,
                    "app_url": app_url,
                    "waiting_since": approval["created_at"],
                },
            ),
        )

    async def _stamp_reminder() -> None:
        svc = get_service_client()
        await asyncio.to_thread(
            lambda: svc.table("approvals")
            .update({"reminder_sent_at": datetime.now(UTC).isoformat()})
            .eq("id", approval_id)
            .execute()
        )

    await step.run("stamp-reminder", _stamp_reminder)
    return {"status": "reminder_sent"}


# ── approval/resolved → notify requester + update Slack DM ──────────────────


@_inngest_client.create_function(
    fn_id="approval-resolved",
    trigger=inngest.TriggerEvent(event="approval/resolved"),
    retries=2,
)
async def approval_resolved_function(ctx: inngest.Context) -> dict[str, Any]:
    step = ctx.step
    data = ctx.event.data
    approval_id: str = data["approval_id"]
    action: str = data["action"]
    note: str | None = data.get("note")

    settings = get_settings()
    app_url = settings.app_url.rstrip("/")

    approval = await step.run("load-approval", lambda: _load_approval(approval_id))
    if not approval:
        return {"status": "skipped", "reason": "approval_missing"}

    requester = await step.run("load-requester", lambda: _resolve_user(approval["requested_by"]))
    approver = await step.run("load-approver", lambda: _resolve_user(approval["approver_id"]))
    ch = (approval["execution_action"] or {}).get("channel") or "gmail"
    destination = _destination_from_action(approval["execution_action"] or {})

    # Step 1: email the requester.
    if requester.get("email"):
        await step.run(
            "email-requester",
            lambda: send_email_event(
                event_type="approval_resolved",
                to=requester["email"],
                user_id=approval["requested_by"],
                org_id=approval["org_id"],
                dedupe_key=f"approval-resolved-{approval_id}",
                data={
                    "requester_name": requester.get("display_name") or "there",
                    "approver_name": approver.get("display_name") or "Your approver",
                    "channel_label": _channel_label(ch),
                    "destination": destination,
                    "action": action,
                    "note": note,
                    "web_url": f"{app_url}/approvals/{approval_id}",
                    "app_url": app_url,
                },
            ),
        )

    # Step 2: update the Slack DM in place (if we sent one).
    slack_msg = approval.get("slack_message") or None

    async def _update_slack_dm() -> None:
        if not isinstance(slack_msg, dict):
            return
        channel = slack_msg.get("channel")
        ts = slack_msg.get("ts")
        if not channel or not ts:
            return
        try:
            await slack_service.update_message(
                org_id=approval["org_id"],
                channel=channel,
                ts=ts,
                text=(
                    f"{'✅ Approved' if action == 'approved' else '🚫 Rejected'} "
                    f"by {approver.get('display_name') or 'approver'}"
                ),
                blocks=approval_resolved_blocks(
                    requester_name=requester.get("display_name"),
                    channel=ch,
                    action=action,
                    approver_name=approver.get("display_name"),
                    note=note,
                ),
            )
        except Exception as exc:
            log.warning("approval_slack_update_failed", approval_id=approval_id, error=str(exc))

    await step.run("update-slack-dm", _update_slack_dm)

    return {"status": "notified"}


FUNCTIONS = [approval_requested_function, approval_resolved_function]
