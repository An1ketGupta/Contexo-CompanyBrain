"""Post-write side effects for outbound writes (Day 3-4 hardening).

After the Inngest worker has POSTed to Slack/Gmail/Notion/Gdocs and
updated `messages.delivery_status`, we want two additional things:

  1. Audit trail — flip the agent_runs row to completed / failed (the
     queueing router already inserted it as 'running').
  2. Notify the requesting user when delivery fails permanently so they
     can retry or reconnect the integration. Success notifications are
     deliberately omitted — the UI already shows a "sent" badge inline,
     and a bell flash on every successful Slack post would be noise.
Every side effect here is best-effort: a failure to write an audit row
or notification MUST NOT bubble up and trip an Inngest retry, because
the actual delivery already happened (or didn't) and we don't want to
double-send. Each block catches broadly and logs.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from app.services.outbound_audit import record_failed, record_sent

log = logging.getLogger(__name__)


OutboundChannel = Literal["slack", "gmail", "notion", "gdocs"]


# Notification copy by channel — short, actionable, matches the rest of the
# in-app notification feed's tone.
_FAILURE_TITLE: dict[OutboundChannel, str] = {
    "slack": "Slack post failed",
    "gmail": "Email send failed",
    "notion": "Notion page creation failed",
    "gdocs": "Google Doc export failed",
}


async def on_sent(
    *,
    run_id: str,
    channel: OutboundChannel,
    org_id: str,
    message_id: str,
    destination: str,
    external_id: str | None,
    url: str | None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Call after the message has been confirmed delivered."""
    output = {
        "destination": destination,
        "external_id": external_id,
        "url": url,
        **(extra or {}),
    }
    # 1. Audit trail.
    try:
        await record_sent(run_id=run_id, output=output)
    except Exception as exc:
        log.warning("postwrite_audit_sent_failed run_id=%s err=%s", run_id, exc)


async def on_failed(
    *,
    run_id: str,
    channel: OutboundChannel,
    org_id: str,
    user_id: str | None,
    message_id: str,
    conversation_id: str | None,
    destination: str,
    reason: str,
    permanent: bool,
) -> None:
    """Call after delivery has permanently failed (PermissionError, exhausted
    retries, etc). `permanent=True` triggers the user-facing notification;
    transient failures inside the retry budget should not call this — only
    the terminal failure path should.
    """
    error_payload = {
        "destination": destination,
        "reason": reason,
    }

    # 1. Audit trail.
    try:
        await record_failed(run_id=run_id, error=reason, output=error_payload)
    except Exception as exc:
        log.warning("postwrite_audit_failed_failed run_id=%s err=%s", run_id, exc)

    # 2. Notify the user (in-app). dedupe_key=run_id so a re-fire of this
    #    same job (e.g. a retry that also fails) doesn't double-notify.
    if permanent and user_id:
        try:
            from app.services.notifications import create_notification

            await create_notification(
                org_id=org_id,
                user_id=user_id,
                type="outbound_write_failed",
                title=_FAILURE_TITLE.get(channel, "Outbound delivery failed"),
                body=f"{destination}: {reason}",
                metadata={
                    "channel": channel,
                    "destination": destination,
                    "reason": reason,
                    "run_id": run_id,
                    "message_id": message_id,
                },
                link_url=(
                    f"/chat/{conversation_id}" if conversation_id else None
                ),
                dedupe_key=run_id,
            )
        except Exception as exc:
            log.warning("postwrite_notify_failed run_id=%s err=%s", run_id, exc)
