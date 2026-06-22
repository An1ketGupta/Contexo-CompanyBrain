"""Slack bot (Day 15 / #31).

Endpoints:
    GET  /integrations/slack/connect   — returns slack.com OAuth URL
    GET  /integrations/slack/callback  — code exchange + install
    POST /slack/events                 — slash command + (future) events
    POST /slack/interactions           — Block Kit button + select events

The `/brain` slash command flow:
    1. Slack POSTs the command. Verify signing-secret HMAC + replay window.
    2. Acknowledge within 3 seconds (return 200 with empty body).
    3. In a background task, run execute_task_blocking against the org's
       knowledge base, then chat.update the placeholder message with the
       final answer + a sources block.

We don't pull `slack-bolt` to keep deps lean; the public Slack API is just
HTTP. signing verification is inlined per the Slack docs:
https://api.slack.com/authentication/verifying-requests-from-slack
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlencode

import httpx
import inngest as inngest_pkg
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.config import get_settings
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.inngest.client import get_inngest_client
from app.services.integrations import slack as slack_service
from app.services.integrations import slack_inbound
from app.services.integrations.slack_commands import dispatch as dispatch_slack_command
from app.services.integrations.slack_commands import get_slack_draft
from app.services.llm.task_chain import execute_task_blocking

log = logging.getLogger(__name__)

router = APIRouter(tags=["slack"])

_SLACK_API = "https://slack.com/api"
# Slack signs requests; we reject anything older than this to defeat replay.
_SLACK_REPLAY_WINDOW = 60 * 5


# ── Helpers ────────────────────────────────────────────────────────────────

def _require_org(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return org_id, user_id, token


async def _require_admin(user_id: str, token: str) -> None:
    client = get_user_client(token)
    me = await asyncio.to_thread(
        lambda: client.table("users").select("role").eq("id", user_id).maybe_single().execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only workspace admins can manage Slack.")


def _mint_state(*, user_id: str, org_id: str) -> str:
    """Same scheme as the integrations router state. Duplicated rather than
    cross-imported because the integrations router already mints provider-
    specific state and we don't want to risk a typo on the provider tag."""
    settings = get_settings()
    secret = settings.oauth_state_secret
    if not secret:
        raise HTTPException(status_code=500, detail="OAuth state secret not configured.")
    payload = f"slack.{user_id}.{org_id}.{int(time.time())}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _parse_state(token: str) -> tuple[str, str]:
    settings = get_settings()
    secret = settings.oauth_state_secret
    try:
        prov, user_id, org_id, ts, sig = token.split(".")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Malformed state.") from exc
    if prov != "slack":
        raise HTTPException(status_code=400, detail="Wrong provider.")
    payload = f"{prov}.{user_id}.{org_id}.{ts}"
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=400, detail="State signature mismatch.")
    if int(time.time()) - int(ts) > 600:
        raise HTTPException(status_code=400, detail="State expired.")
    return user_id, org_id


# ── OAuth install ───────────────────────────────────────────────────────────

@router.get("/integrations/slack/connect")
async def slack_connect(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    settings = get_settings()
    if not settings.slack_client_id:
        raise HTTPException(status_code=503, detail="Slack integration is not configured.")
    state = _mint_state(user_id=user_id, org_id=org_id)
    # Scope upgrade: chat:write + commands are the outbound surface; the
    # rest enable inbound ingest of pins/canvases/files. `canvases:read`
    # is paid-plan only — Slack quietly drops it for Free/Pro workspaces
    # and the inbound path detects + degrades gracefully.
    scope_str = ",".join([
        "chat:write",
        "commands",
        "channels:read",
        "channels:history",
        "groups:read",
        "groups:history",
        "pins:read",
        "files:read",
        "users:read",
        "canvases:read",
    ])
    params = {
        "client_id": settings.slack_client_id,
        "scope": scope_str,
        "redirect_uri": settings.slack_oauth_redirect_uri,
        "state": state,
    }
    return {"auth_url": f"https://slack.com/oauth/v2/authorize?{urlencode(params)}"}


@router.get("/integrations/slack/callback")
async def slack_callback(
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    user_id, org_id = _parse_state(state)
    settings = get_settings()

    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(
            f"{_SLACK_API}/oauth.v2.access",
            data={
                "client_id": settings.slack_client_id,
                "client_secret": settings.slack_client_secret,
                "code": code,
                "redirect_uri": settings.slack_oauth_redirect_uri,
            },
        )
    if resp.status_code != 200 or not resp.json().get("ok"):
        log.error("slack_oauth_failed: %s", resp.text[:300])
        return _settings_redirect(error="slack_oauth_failed")

    payload = resp.json()
    team = payload.get("team") or {}
    bot_user = payload.get("bot_user_id")
    access = payload.get("access_token") or ""
    if not access or not team.get("id"):
        return _settings_redirect(error="slack_oauth_missing_fields")

    # Slack returns the granted scope set as a comma-separated string on
    # `scope`. We persist it so the inbound surface can detect when a paid-
    # plan-only scope (canvases:read) wasn't granted and skip canvases
    # silently rather than crashing on every event.
    granted_scopes = [s for s in (payload.get("scope") or "").split(",") if s]

    svc = get_service_client()
    row = {
        "org_id": org_id,
        "slack_team_id": team["id"],
        "slack_team_name": team.get("name"),
        "bot_token": access,
        "bot_user_id": bot_user,
        "installed_by": user_id,
        "scopes": granted_scopes,
    }
    try:
        await asyncio.to_thread(
            lambda: svc.table("slack_integrations").upsert(row, on_conflict="org_id").execute()
        )
    except Exception as exc:
        log.error("slack_install_persist_failed: %s", exc)
        return _settings_redirect(error="slack_install_failed")
    return _settings_redirect(connected="slack")


@router.delete("/integrations/slack", status_code=status.HTTP_204_NO_CONTENT)
async def slack_disconnect(current_user: dict = Depends(verify_jwt)) -> None:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("slack_integrations").delete().eq("org_id", org_id).execute()
    )
    # Drop the cached channel list so a re-install doesn't surface stale data.
    await slack_service.invalidate_channels_cache(org_id)


# ── Outbound: list channels + post message ──────────────────────────────────

@router.get("/integrations/slack/channels")
async def slack_list_channels(
    refresh: bool = Query(default=False, description="Bypass cache and re-fetch."),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Channel picker data source. Cached 1h in Upstash per-org.

    Any member of the workspace can list — we don't gate on admin here
    because the picker is a chat-time affordance, and the bot only sees
    channels it's been invited to anyway.
    """
    org_id, _, _ = _require_org(current_user)
    channels = await slack_service.list_channels(org_id=org_id, force_refresh=refresh)
    return {"channels": channels}


class PostSlackRequest(BaseModel):
    message_id: str = Field(..., min_length=1, max_length=64)
    channel_id: str = Field(..., min_length=1, max_length=40)
    # Snapshot the channel name at click time so the delivery_status badge
    # in the UI can render "#announcements" without a second Slack call.
    channel_name: str = Field(..., min_length=1, max_length=200)
    text: str = Field(..., min_length=1, max_length=39_000)  # Slack mrkdwn cap
    thread_ts: str | None = Field(default=None, max_length=40)
    # When the frontend shows the "competitor matches in this text" warning,
    # the user clicks through; the post request then carries the explicit ack
    # so the server-side guard knows it was a deliberate choice.
    acknowledged_warnings: bool = Field(default=False)


class PostSlackResponse(BaseModel):
    queued: bool
    job_id: str


@router.post("/integrations/slack/post", response_model=PostSlackResponse)
async def slack_post(
    body: PostSlackRequest,
    current_user: dict = Depends(verify_jwt),
) -> PostSlackResponse:
    """Enqueue an outbound Slack post. Optimistically marks the message's
    delivery_status as `queued`; the Inngest worker flips it to sent/failed.
    """
    org_id, user_id, _ = _require_org(current_user)

    svc = get_service_client()

    # Cheap up-front: org connected to Slack?
    connected = await asyncio.to_thread(
        lambda: svc.table("slack_integrations")
        .select("id")
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not connected or not connected.data:
        raise HTTPException(status_code=400, detail="slack_not_connected")

    # Same defensive read as the Gmail path: confirm the message exists, belongs
    # to this org, and hasn't already been delivered. We also pull conversation_id
    # so the failure notification can link back to the source chat.
    msg = await asyncio.to_thread(
        lambda: svc.table("messages")
        .select("id, org_id, conversation_id, delivery_status")
        .eq("id", body.message_id)
        .maybe_single()
        .execute()
    )
    if not msg or not msg.data or msg.data.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail="message_not_found")
    existing = msg.data.get("delivery_status") or {}
    if existing.get("status") == "sent":
        raise HTTPException(status_code=409, detail="message_already_sent")
    conversation_id: str | None = msg.data.get("conversation_id")

    # Shared safety/quality gate: confidence floor, competitor match (with
    # explicit ack), moderation, per-user/per-org rate limit. Any failure
    # raises a typed OutboundGateError carrying the right HTTP code + detail.
    from app.services.outbound_gate import (
        OutboundGateError,
        enforce_outbound_write_guards,
    )

    try:
        await enforce_outbound_write_guards(
            channel="slack",
            org_id=org_id,
            user_id=user_id,
            message_id=body.message_id,
            content=body.text,
            competitor_acknowledged=body.acknowledged_warnings,
        )
    except OutboundGateError as gate_err:
        headers: dict[str, str] = {}
        retry = gate_err.extra.get("retry_after")
        if isinstance(retry, int) and retry > 0:
            headers["Retry-After"] = str(retry)
        raise HTTPException(
            status_code=gate_err.status_code,
            detail=gate_err.code,
            headers=headers or None,
        )

    job_id = str(uuid.uuid4())
    queued_at = datetime.now(timezone.utc).isoformat()

    await asyncio.to_thread(
        lambda: svc.table("messages")
        .update({
            "delivery_status": {
                "channel": "slack",
                "status": "queued",
                "channel_name": body.channel_name,
                "channel_id": body.channel_id,
                "job_id": job_id,
                "queued_at": queued_at,
            }
        })
        .eq("id", body.message_id)
        .execute()
    )

    # Audit row: status='running' until the worker flips it to completed/failed.
    from app.services.outbound_audit import record_queued

    await record_queued(
        run_id=job_id,
        channel="slack",
        org_id=org_id,
        user_id=user_id,
        message_id=body.message_id,
        destination=f"#{body.channel_name}",
        input_payload={
            "channel_id": body.channel_id,
            "channel_name": body.channel_name,
            "thread_ts": body.thread_ts,
            "text": body.text,
        },
    )

    client = get_inngest_client()
    await client.send(
        inngest_pkg.Event(
            name="slack/post-message",
            data={
                "job_id": job_id,
                "message_id": body.message_id,
                "org_id": org_id,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "channel_id": body.channel_id,
                "channel_name": body.channel_name,
                "text": body.text,
                "thread_ts": body.thread_ts,
            },
            id=f"slack-post-{job_id}",
        )
    )
    return PostSlackResponse(queued=True, job_id=job_id)


# ── Signing verification ────────────────────────────────────────────────────

async def _verify_slack_signature(request: Request) -> bytes:
    """Validate the Slack signing secret per
    https://api.slack.com/authentication/verifying-requests-from-slack.

    Returns the raw body bytes (reading the request again would be a footgun
    since starlette consumed the stream once).
    """
    settings = get_settings()
    if not settings.slack_signing_secret:
        raise HTTPException(status_code=503, detail="Slack signing secret not configured.")

    body = await request.body()
    ts = request.headers.get("X-Slack-Request-Timestamp") or ""
    sig = request.headers.get("X-Slack-Signature") or ""
    if not ts or not sig:
        raise HTTPException(status_code=401, detail="Missing Slack signature headers.")
    try:
        ts_int = int(ts)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Bad timestamp.") from exc
    if abs(time.time() - ts_int) > _SLACK_REPLAY_WINDOW:
        raise HTTPException(status_code=401, detail="Slack request too old.")

    base = f"v0:{ts}:".encode("utf-8") + body
    digest = hmac.new(
        settings.slack_signing_secret.encode("utf-8"),
        base,
        hashlib.sha256,
    ).hexdigest()
    expected = f"v0={digest}"
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=401, detail="Slack signature mismatch.")
    return body


# ── /slack/events: slash commands + URL verification ────────────────────────

@router.post("/slack/events")
async def slack_events(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Any:
    """Single endpoint for everything Slack POSTs us.

    URL verification challenge: Slack pings on app-config save; we echo the
    challenge back. Slash commands are form-encoded; events are JSON.
    """
    body = await _verify_slack_signature(request)
    content_type = (request.headers.get("Content-Type") or "").split(";")[0].strip()

    if content_type == "application/json":
        payload = json.loads(body.decode("utf-8"))
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge")}
        if payload.get("type") == "event_callback":
            # Slack's 3s ACK deadline — fan out to Inngest immediately and
            # return. The wrapper function handles team→org resolution +
            # routing per event type.
            await _dispatch_inbound_event(payload)
            return {"ok": True}
        return {"ok": True}

    # form-urlencoded slash command
    parsed = parse_qs(body.decode("utf-8"))
    command = (parsed.get("command") or [""])[0]
    if command not in ("/brain", "/ani"):
        return {"text": "Unknown command."}

    team_id = (parsed.get("team_id") or [""])[0]
    channel_id = (parsed.get("channel_id") or [""])[0]
    user_id = (parsed.get("user_id") or [""])[0]
    text = (parsed.get("text") or [""])[0].strip()
    response_url = (parsed.get("response_url") or [""])[0]

    if not text:
        return {"response_type": "ephemeral", "text": "Try `/brain what's our refund policy?`"}

    # Fan out to a background task and ACK within Slack's 3s window.
    background_tasks.add_task(
        _answer_in_background,
        team_id=team_id,
        channel_id=channel_id,
        user_id=user_id,
        query=text,
        response_url=response_url,
    )
    return {"response_type": "ephemeral", "text": "🧠 Thinking…"}


# ── Background answer + chat.update streaming approximation ─────────────────

async def _answer_in_background(
    *,
    team_id: str,
    channel_id: str,
    user_id: str,
    query: str,
    response_url: str,
) -> None:
    """Route the slash command. First try the execution-command dispatcher
    (Agent Day 13) — `draft email to …`, `post … to #channel`, etc. — which
    returns its own Slack payload. If no verb matches, fall through to the
    existing Q&A path.

    We use the slash command's response_url (no scopes required) for the
    final answer. Streaming token-by-token via chat.update is technically
    nicer but each update is a separate API call charged against Slack's
    rate-limit window; for V1, one final update is the right tradeoff.
    """
    svc = get_service_client()
    integ = await asyncio.to_thread(
        lambda: svc.table("slack_integrations")
        .select("org_id, bot_token").eq("slack_team_id", team_id)
        .maybe_single().execute()
    )
    if not integ or not integ.data:
        await _post_to_response_url(
            response_url,
            {"response_type": "ephemeral", "text": "Company Brain isn't connected to this Slack workspace."},
        )
        return

    org_id = integ.data["org_id"]
    bot_token = integ.data["bot_token"]

    # Day 13 — try the structured-command dispatcher first.
    try:
        cmd_payload = await dispatch_slack_command(
            command_text=query,
            org_id=org_id,
            user_id=user_id,
            channel_id=channel_id,
        )
    except Exception as exc:
        log.warning("slack_command_dispatch_failed: %s", exc)
        cmd_payload = None
    if cmd_payload is not None:
        await _post_to_response_url(response_url, cmd_payload)
        return

    try:
        result = await execute_task_blocking(
            user_message=query,
            org_id=org_id,
            db_client=svc,
        )
    except Exception as exc:
        log.exception("slack_answer_failed")
        await _post_to_response_url(
            response_url,
            {"response_type": "ephemeral", "text": f"Couldn't get an answer: {exc}"},
        )
        return

    if result.error:
        await _post_to_response_url(
            response_url,
            {"response_type": "ephemeral", "text": f"Error: {result.error}"},
        )
        return

    answer = result.text or "I couldn't find anything in the knowledge base for that."

    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": answer[:2900]}},
    ]
    if result.sources:
        # Slack mrkdwn doesn't render markdown links the same as web; use the
        # bullet-list shape Slack handles natively.
        names = ", ".join(
            f"📄 {s.get('document_name') or 'Source'}"
            for s in result.sources[:5]
        )
        blocks.append({"type": "divider"})
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"*Sources:* {names}"}],
            }
        )

    # Public answer in the channel so teammates benefit too.
    await _post_to_response_url(
        response_url,
        {
            "response_type": "in_channel",
            "text": answer[:2900],
            "blocks": blocks,
        },
    )
    _ = bot_token  # bot_token kept for future chat.postMessage / chat.update use.


async def _post_to_response_url(response_url: str, payload: dict[str, Any]) -> None:
    if not response_url:
        return
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0)) as client:
            await client.post(response_url, json=payload)
    except Exception as exc:
        log.warning("slack_response_post_failed: %s", exc)


# ── /slack/interactions: Block Kit button / select handler ──────────────────

@router.post("/slack/interactions")
async def slack_interactions(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Any:
    """Slack POSTs here on every Block Kit action (button click, dropdown
    select, modal submit). Payload arrives as application/x-www-form-urlencoded
    with a single `payload` key containing JSON.

    Dispatch model: action_id is `<verb>_<noun>_<entity_id>`. We split on the
    last underscore to recover the entity, prefix-match the verb_noun pair to
    a handler, ACK Slack within 3s with an ephemeral "Working on it…" and
    finish the actual work in a background task that posts back via the
    payload's response_url.
    """
    body = await _verify_slack_signature(request)
    parsed = parse_qs(body.decode("utf-8"))
    raw_payload = (parsed.get("payload") or [""])[0]
    if not raw_payload:
        raise HTTPException(status_code=400, detail="Missing payload.")
    try:
        payload = json.loads(raw_payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Payload is not valid JSON.") from exc

    actions = payload.get("actions") or []
    response_url = payload.get("response_url") or ""
    team_id = (payload.get("team") or {}).get("id") or ""
    slack_user_id = (payload.get("user") or {}).get("id") or ""
    payload_type = payload.get("type") or ""

    # Day 13: modal submission (view_submission) — the email-send modal
    # opened by the `send_email_<draft_id>` button submits here.
    if payload_type == "view_submission":
        org_id = await _resolve_org_from_team(team_id)
        if not org_id:
            return {"response_action": "errors", "errors": {"to_email_block": "Company Brain isn't connected to this Slack workspace."}}
        view = payload.get("view") or {}
        callback_id = view.get("callback_id") or ""
        if callback_id.startswith("email_send_modal:"):
            draft_id = callback_id.split(":", 1)[1]
            background_tasks.add_task(
                _send_slack_draft_email,
                org_id=org_id,
                draft_id=draft_id,
                slack_user_id=slack_user_id,
                view=view,
                response_url=response_url,
            )
            # Close the modal immediately; the actual send runs in background
            # and DMs the user with the outcome.
            return {"response_action": "clear"}
        return {"response_action": "clear"}

    if not actions:
        # Unhandled interaction kind — ACK to avoid a Slack-side error display.
        return {"ok": True}

    action = actions[0]
    action_id: str = action.get("action_id") or ""

    # Resolve org BEFORE returning so an unknown workspace gets a clean
    # ephemeral message rather than a silent ACK that orphans the button.
    org_id = await _resolve_org_from_team(team_id)
    if not org_id:
        return {
            "response_type": "ephemeral",
            "replace_original": False,
            "text": "Company Brain isn't connected to this Slack workspace.",
        }

    # Route by action_id prefix. Each handler runs in the background so we
    # always meet Slack's 3s ACK budget.
    if action_id.startswith("send_email_"):
        # Day 13: open a Slack modal to collect To + Subject before send.
        draft_id = action_id[len("send_email_"):]
        trigger_id = payload.get("trigger_id") or ""
        background_tasks.add_task(
            _open_email_send_modal,
            org_id=org_id,
            draft_id=draft_id,
            trigger_id=trigger_id,
            response_url=response_url,
        )
    elif action_id.startswith("post_slack_"):
        draft_id = action_id[len("post_slack_"):]
        background_tasks.add_task(
            _post_slack_draft_to_channel,
            org_id=org_id,
            draft_id=draft_id,
            slack_user_id=slack_user_id,
            response_url=response_url,
        )
    elif action_id.startswith("discard_"):
        # The cleanest UX is to delete the original draft message. Slack
        # supports this via {"delete_original": true} on the response payload.
        return {"delete_original": True}
    elif action_id.startswith("edit_"):
        background_tasks.add_task(
            _stub_acknowledge,
            response_url=response_url,
            text="✏️ Inline editing from Slack is coming soon — for now, open Company Brain to refine and resend.",
        )
    elif action_id.startswith("approve_request_") or action_id.startswith("reject_request_"):
        # Day 6 approval buttons. Recover approval id from the suffix and
        # resolve in the background — the response_url path swaps the
        # message contents once the resolve finishes.
        decision = "approved" if action_id.startswith("approve_request_") else "rejected"
        approval_id = action_id[len("approve_request_"):] if decision == "approved" else action_id[len("reject_request_"):]
        background_tasks.add_task(
            _resolve_approval_from_slack,
            approval_id=approval_id,
            decision=decision,
            slack_user_id=slack_user_id,
            response_url=response_url,
            org_id=org_id,
        )
    elif action_id.startswith("open_approval_"):
        # No-op — Slack handles the URL-style button client-side. We ACK so
        # the user doesn't see a "didn't work" error.
        return {"ok": True}
    else:
        log.warning("slack_unknown_action_id: %s", action_id)
        return {"ok": True}

    # Standard ACK shape Slack expects to keep the source message in place.
    _ = slack_user_id  # surfaced into observability tags in a follow-up day
    return {"ok": True}


async def _resolve_org_from_team(team_id: str) -> str | None:
    if not team_id:
        return None
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("slack_integrations")
        .select("org_id")
        .eq("slack_team_id", team_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        return None
    return row.data.get("org_id")


async def _stub_acknowledge(*, response_url: str, text: str) -> None:
    """Day-1 scaffold: just ack so we can validate the round-trip end to
    end before Days 2-4 wire the real execution paths to the action_id
    dispatch table."""
    await _post_to_response_url(
        response_url,
        {"response_type": "ephemeral", "replace_original": False, "text": text},
    )


async def _resolve_approval_from_slack(
    *,
    approval_id: str,
    decision: str,
    slack_user_id: str,
    response_url: str,
    org_id: str,
) -> None:
    """Background handler for Day-6 approval buttons.

    Two guards before we mutate state:
      1. The Slack user clicking the button must match the approver_id on
         the approval. We resolve their email via users.info → match against
         the auth.users row for approver_id.
      2. The approval must still be `pending`.

    Both failure modes respond ephemerally via response_url rather than
    chat.update so the original DM stays intact (the requester gets a clean
    "already resolved" message rather than seeing the buttons disappear).
    """
    from app.services.approvals import dispatch_execution
    from app.services.integrations.slack_block_kit import approval_resolved_blocks

    svc = get_service_client()

    row = await asyncio.to_thread(
        lambda: svc.table("approvals").select("*").eq("id", approval_id).maybe_single().execute()
    )
    if not row or not row.data:
        await _post_to_response_url(
            response_url,
            {"response_type": "ephemeral", "text": "This approval no longer exists."},
        )
        return
    approval = row.data

    if approval["status"] != "pending":
        await _post_to_response_url(
            response_url,
            {
                "response_type": "ephemeral",
                "text": f"This approval was already {approval['status']}.",
            },
        )
        return

    # Match the Slack-clicker to the persisted approver. We do an email
    # match because users.info doesn't surface an org_id, and we already
    # know approver_id maps to an auth.users email.
    approver_email: str | None = None
    try:
        au = await asyncio.to_thread(
            lambda: svc.auth.admin.get_user_by_id(approval["approver_id"])
        )
        approver_email = getattr(getattr(au, "user", None), "email", None)
    except Exception as exc:
        log.warning("slack_approval_approver_lookup_failed: %s", exc)

    clicker_email: str | None = None
    try:
        token = await slack_service.get_bot_token(org_id=org_id)
        if token and slack_user_id:
            async with httpx.AsyncClient(timeout=httpx.Timeout(8.0)) as client:
                resp = await client.get(
                    "https://slack.com/api/users.info",
                    params={"user": slack_user_id},
                    headers={"Authorization": f"Bearer {token}"},
                )
            body = resp.json() if resp.status_code == 200 else {}
            if body.get("ok"):
                clicker_email = (
                    ((body.get("user") or {}).get("profile") or {}).get("email")
                )
    except Exception as exc:
        log.warning("slack_users_info_failed: %s", exc)

    def _norm(v: str | None) -> str:
        return (v or "").strip().lower()

    if not approver_email or not clicker_email or _norm(approver_email) != _norm(clicker_email):
        await _post_to_response_url(
            response_url,
            {
                "response_type": "ephemeral",
                "text": "You're not the designated approver for this request.",
            },
        )
        return

    # Persist resolution + (optionally) dispatch execution.
    now_iso = datetime.now(timezone.utc).isoformat()
    await asyncio.to_thread(
        lambda: svc.table("approvals")
        .update(
            {
                "status": decision,
                "resolved_at": now_iso,
                "resolved_via": "slack",
                "resolved_audit": {"slack_user_id": slack_user_id, "ts": now_iso},
            }
        )
        .eq("id", approval_id)
        .execute()
    )

    if decision == "approved":
        try:
            await dispatch_execution(
                approval_id=approval_id,
                message_id=approval["message_id"],
                org_id=approval["org_id"],
                requested_by=approval["requested_by"],
                action=approval["execution_action"],
            )
        except Exception as exc:
            log.warning("slack_approval_dispatch_failed: %s", exc)
            await asyncio.to_thread(
                lambda: svc.table("approvals")
                .update({"status": "execution_failed"})
                .eq("id", approval_id)
                .execute()
            )

    # Notify the requester (email + Slack DM update fan-out).
    try:
        client = get_inngest_client()
        await client.send(
            inngest_pkg.Event(
                name="approval/resolved",
                data={
                    "approval_id": approval_id,
                    "org_id": approval["org_id"],
                    "requested_by": approval["requested_by"],
                    "approver_id": approval["approver_id"],
                    "action": decision,
                    "note": None,
                    "resolved_via": "slack",
                },
            )
        )
    except Exception as exc:
        log.warning("slack_approval_resolved_event_failed: %s", exc)

    # Resolve the DM in place — chat.update via response_url with
    # replace_original=true. This is faster than chat.update on the bot
    # token because Slack already authenticated the original message id.
    requester_name: str | None = None
    approver_name: str | None = None
    try:
        names = await asyncio.to_thread(
            lambda: svc.table("users")
            .select("id, display_name")
            .in_("id", [approval["requested_by"], approval["approver_id"]])
            .execute()
        )
        nm = {u["id"]: u.get("display_name") for u in (names.data or [])}
        requester_name = nm.get(approval["requested_by"])
        approver_name = nm.get(approval["approver_id"])
    except Exception:
        pass

    channel = (approval["execution_action"] or {}).get("channel") or "gmail"
    await _post_to_response_url(
        response_url,
        {
            "replace_original": True,
            "blocks": approval_resolved_blocks(
                requester_name=requester_name,
                channel=channel,
                action=decision,
                approver_name=approver_name,
                note=None,
            ),
        },
    )


# ── Day 13: email-send modal + post-to-channel from Slack ──────────────────


async def _open_email_send_modal(
    *,
    org_id: str,
    draft_id: str,
    trigger_id: str,
    response_url: str,
) -> None:
    """Slack `views.open` to collect To + Subject before sending.

    The draft body was cached at /brain time (slack_commands._persist_slack_draft),
    so we don't surface it in the modal — just collect routing info, then
    fan to the Gmail send pipeline from the view_submission handler.
    """
    draft = await get_slack_draft(org_id=org_id, draft_id=draft_id)
    if not draft:
        await _post_to_response_url(
            response_url,
            {
                "response_type": "ephemeral",
                "text": "That draft expired (drafts age out after 1 hour). Run `/brain draft email …` again.",
            },
        )
        return
    if not trigger_id:
        await _post_to_response_url(
            response_url,
            {"response_type": "ephemeral", "text": "Slack didn't supply a trigger id — please click again."},
        )
        return

    token = await slack_service.get_bot_token(org_id=org_id)
    if not token:
        await _post_to_response_url(
            response_url,
            {"response_type": "ephemeral", "text": "Slack isn't connected for this workspace."},
        )
        return

    meta = (draft.get("meta") or {})
    body_preview = (draft.get("body") or "")[:200]

    view = {
        "type": "modal",
        "callback_id": f"email_send_modal:{draft_id}",
        "title": {"type": "plain_text", "text": "Send draft via Gmail"},
        "submit": {"type": "plain_text", "text": "Send"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Draft preview*\n```{body_preview}…```",
                },
            },
            {
                "type": "input",
                "block_id": "to_email_block",
                "label": {"type": "plain_text", "text": "To"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "to_email",
                    "placeholder": {
                        "type": "plain_text",
                        "text": meta.get("recipient_hint") or "name@example.com",
                    },
                },
            },
            {
                "type": "input",
                "block_id": "subject_block",
                "label": {"type": "plain_text", "text": "Subject"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "subject",
                    "initial_value": (meta.get("topic") or "")[:120],
                },
            },
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.post(
                f"{_SLACK_API}/views.open",
                json={"trigger_id": trigger_id, "view": view},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
            )
        body = resp.json() if resp.status_code == 200 else {}
        if not body.get("ok"):
            log.warning("slack_views_open_failed: %s", body.get("error"))
            await _post_to_response_url(
                response_url,
                {"response_type": "ephemeral", "text": f":warning: Slack rejected the modal: {body.get('error')}"},
            )
    except Exception as exc:
        log.warning("slack_views_open_exception: %s", exc)
        await _post_to_response_url(
            response_url,
            {"response_type": "ephemeral", "text": ":warning: Couldn't open the send modal."},
        )


async def _send_slack_draft_email(
    *,
    org_id: str,
    draft_id: str,
    slack_user_id: str,
    view: dict[str, Any],
    response_url: str,
) -> None:
    """Background handler for the view_submission of the email-send modal.

    Resolves the Slack-clicker to an internal user so the Gmail send is
    attributed correctly, then fans into the standard Gmail send pipeline.
    """
    draft = await get_slack_draft(org_id=org_id, draft_id=draft_id)
    if not draft:
        await _dm_user_about_send(
            org_id=org_id, slack_user_id=slack_user_id,
            text=":warning: Draft expired before we could send it.",
        )
        return

    state_values = (view.get("state") or {}).get("values") or {}
    to_email = (
        (state_values.get("to_email_block") or {}).get("to_email") or {}
    ).get("value") or ""
    subject = (
        (state_values.get("subject_block") or {}).get("subject") or {}
    ).get("value") or "(no subject)"

    # Match the Slack user to an internal user by email.
    slack_user_email: str | None = None
    try:
        token = await slack_service.get_bot_token(org_id=org_id)
        if token and slack_user_id:
            async with httpx.AsyncClient(timeout=httpx.Timeout(8.0)) as client:
                resp = await client.get(
                    f"{_SLACK_API}/users.info",
                    params={"user": slack_user_id},
                    headers={"Authorization": f"Bearer {token}"},
                )
            body = resp.json() if resp.status_code == 200 else {}
            if body.get("ok"):
                slack_user_email = (
                    ((body.get("user") or {}).get("profile") or {}).get("email")
                )
    except Exception as exc:
        log.warning("slack_users_info_failed_for_send: %s", exc)

    if not slack_user_email:
        await _dm_user_about_send(
            org_id=org_id, slack_user_id=slack_user_id,
            text=":warning: Couldn't match your Slack account to a Company Brain user. Connect Gmail from the web app first.",
        )
        return

    svc = get_service_client()
    try:
        au_lookup = await asyncio.to_thread(
            lambda: svc.auth.admin.list_users()
        )
        internal_user_id: str | None = None
        for u in (getattr(au_lookup, "users", None) or []):
            if (getattr(u, "email", "") or "").lower() == slack_user_email.lower():
                internal_user_id = u.id
                break
    except Exception as exc:
        log.warning("slack_send_user_lookup_failed: %s", exc)
        internal_user_id = None

    if not internal_user_id:
        await _dm_user_about_send(
            org_id=org_id, slack_user_id=slack_user_id,
            text=":warning: Your Slack email isn't on Company Brain yet — sign in to the web app first.",
        )
        return

    # Confirm Gmail send scope is granted for this user.
    from app.services.integrations import gmail as gmail_service
    gmail_row = await asyncio.to_thread(
        lambda: svc.table("gmail_integrations")
        .select("scopes")
        .eq("org_id", org_id)
        .eq("user_id", internal_user_id)
        .maybe_single()
        .execute()
    )
    if not gmail_row or not gmail_row.data:
        await _dm_user_about_send(
            org_id=org_id, slack_user_id=slack_user_id,
            text=":warning: Connect Gmail in Company Brain settings first, then try again.",
        )
        return
    if not gmail_service.has_send_scope(gmail_row.data.get("scopes") or []):
        await _dm_user_about_send(
            org_id=org_id, slack_user_id=slack_user_id,
            text=":warning: Your Gmail connection is missing send permission — re-connect with Send scope.",
        )
        return

    # Mint a conversation + message just like the support send endpoint —
    # the existing Gmail audit/delivery_status pipeline expects a message_id.
    conversation_id = str(uuid.uuid4())
    await asyncio.to_thread(
        lambda: svc.table("conversations").insert({
            "id": conversation_id,
            "org_id": org_id,
            "user_id": internal_user_id,
            "title": f"Slack draft: {subject[:80]}",
        }).execute()
    )
    msg_id = str(uuid.uuid4())
    await asyncio.to_thread(
        lambda: svc.table("messages").insert({
            "id": msg_id,
            "conversation_id": conversation_id,
            "org_id": org_id,
            "role": "assistant",
            "content": draft["body"],
            "sources": draft.get("sources") or None,
        }).execute()
    )

    job_id = str(uuid.uuid4())
    queued_at = datetime.now(timezone.utc).isoformat()
    await asyncio.to_thread(
        lambda: svc.table("messages")
        .update({
            "delivery_status": {
                "channel": "gmail",
                "status": "queued",
                "recipient": to_email,
                "job_id": job_id,
                "queued_at": queued_at,
            }
        })
        .eq("id", msg_id)
        .execute()
    )
    client = get_inngest_client()
    await client.send(
        inngest_pkg.Event(
            name="gmail/send-email",
            data={
                "job_id": job_id,
                "message_id": msg_id,
                "org_id": org_id,
                "user_id": internal_user_id,
                "to": to_email,
                "subject": subject,
                "body": draft["body"],
                "cc": None,
                "reply_to": None,
            },
            id=f"gmail-send-{job_id}",
        )
    )
    await _dm_user_about_send(
        org_id=org_id, slack_user_id=slack_user_id,
        text=f":white_check_mark: Queued email to *{to_email}* with subject *{subject}*.",
    )


async def _post_slack_draft_to_channel(
    *,
    org_id: str,
    draft_id: str,
    slack_user_id: str,
    response_url: str,
) -> None:
    """Background handler for the `post_slack_<draft_id>` button.

    Posts the cached draft into the channel referenced in the draft's
    `meta.target_channel_name`. Replaces the original ephemeral with a
    "posted" confirmation. Lookups channel_id from list_channels cache.
    """
    draft = await get_slack_draft(org_id=org_id, draft_id=draft_id)
    if not draft:
        await _post_to_response_url(
            response_url,
            {"response_type": "ephemeral", "text": ":warning: That draft expired."},
        )
        return
    target_label = ((draft.get("meta") or {}).get("target_channel_name") or "").lstrip("#")
    if not target_label:
        await _post_to_response_url(
            response_url,
            {"response_type": "ephemeral", "text": ":warning: No target channel set on this draft."},
        )
        return

    channels = await slack_service.list_channels(org_id=org_id)
    match = next((c for c in channels if (c.get("name") or "").lower() == target_label.lower()), None)
    if not match:
        await _post_to_response_url(
            response_url,
            {"response_type": "ephemeral", "text": f":warning: Couldn't find #{target_label} in this workspace."},
        )
        return
    try:
        await slack_service.post_message(
            org_id=org_id, channel_id=match["id"], text=draft["body"],
        )
        await _post_to_response_url(
            response_url,
            {
                "replace_original": True,
                "text": f":white_check_mark: Posted to #{target_label}.",
            },
        )
    except PermissionError as exc:
        await _post_to_response_url(
            response_url,
            {"response_type": "ephemeral", "text": f":warning: Slack refused the post: {exc}"},
        )
    except Exception as exc:
        log.warning("slack_post_from_draft_failed: %s", exc)
        await _post_to_response_url(
            response_url,
            {"response_type": "ephemeral", "text": f":warning: Couldn't post: {type(exc).__name__}"},
        )
    _ = slack_user_id  # surfaced into observability tags in a follow-up


async def _dm_user_about_send(*, org_id: str, slack_user_id: str, text: str) -> None:
    """Best-effort DM. Used to tell the user how their modal-submit went."""
    try:
        await slack_service.send_dm(
            org_id=org_id, user_id=slack_user_id, text=text,
        )
    except Exception as exc:
        log.warning("slack_send_dm_failed: %s", exc)


def _settings_redirect(*, connected: str | None = None, error: str | None = None) -> RedirectResponse:
    settings = get_settings()
    base = settings.app_url.rstrip("/") + "/settings/integrations"
    if connected:
        return RedirectResponse(url=f"{base}?connected={connected}", status_code=302)
    if error:
        return RedirectResponse(url=f"{base}?error={error}", status_code=302)
    return RedirectResponse(url=base, status_code=302)


# ─────────────────────────────────────────────────────────────────────────────
# Slack INBOUND — channel subscriptions + event dispatch
# ─────────────────────────────────────────────────────────────────────────────


_INBOUND_EVENT_TYPES = {
    "pin_added",
    "pin_removed",
    "message",
    "file_shared",
    "canvas_added",
    "canvas_changed",
    "member_joined_channel",
}


async def _dispatch_inbound_event(envelope: dict[str, Any]) -> None:
    """Resolve team→org and fan out the event to Inngest.

    `event_callback` envelopes carry `team_id` at the top level + `event`
    payload nested. We trust the signature already verified upstream, then
    just normalise the shape so the Inngest worker has a stable contract.
    """
    team_id = envelope.get("team_id") or ""
    event = envelope.get("event") or {}
    event_type = event.get("type")
    if event_type not in _INBOUND_EVENT_TYPES:
        return
    org_id = await slack_inbound.find_org_by_team(team_id)
    if not org_id:
        log.info("slack_event_no_org team_id=%s", team_id)
        return

    client = get_inngest_client()
    # Stable dedupe key off the event_id Slack includes per delivery — Slack
    # retries every undelivered event up to 3 times, so we lean on this to
    # avoid double-processing.
    event_id = envelope.get("event_id") or f"{team_id}:{event.get('ts') or ''}"
    await client.send(
        inngest_pkg.Event(
            name="slack/inbound-event",
            data={
                "event_type": event_type,
                "team_id": team_id,
                "org_id": org_id,
                "payload": event,
            },
            id=f"slack-inbound-{event_id}",
        )
    )


# ── Subscriptions endpoints ─────────────────────────────────────────────────


class _SubscribeBody(BaseModel):
    channel_id: str = Field(..., min_length=1, max_length=64)
    channel_name: str | None = Field(default=None, max_length=200)
    is_private: bool = Field(default=False)
    ingest_pins: bool = Field(default=True)
    ingest_canvases: bool = Field(default=True)
    ingest_files: bool = Field(default=True)
    ingest_messages: bool = Field(default=False)


@router.get("/integrations/slack/subscriptions")
async def slack_list_subscriptions(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Return the org's current channel allowlist."""
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    svc = get_service_client()
    result = await asyncio.to_thread(
        lambda: svc.table("slack_channel_subscriptions")
        .select(
            "id, channel_id, channel_name, is_private, ingest_pins, "
            "ingest_canvases, ingest_files, ingest_messages, "
            "last_backfilled_at, last_error, last_error_at, created_at"
        )
        .eq("org_id", org_id)
        .order("created_at", desc=False)
        .execute()
    )
    return {"subscriptions": result.data or []}


@router.get("/integrations/slack/inbound-channels")
async def slack_list_inbound_channels(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Channels the bot is currently a member of — the picker's data source.

    Different from the outbound `/integrations/slack/channels` which returns
    every channel (for posting to). Here we filter to bot-member channels
    because the bot can only ingest from channels it's been invited to.
    """
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    channels = await slack_inbound.list_bot_channels(org_id=org_id)
    return {"channels": channels}


@router.post(
    "/integrations/slack/subscriptions",
    status_code=status.HTTP_201_CREATED,
)
async def slack_subscribe_channel(
    body: _SubscribeBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Add a channel to the ingest allowlist + queue a one-shot backfill."""
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)

    svc = get_service_client()
    # Confirm we still have a Slack install — without it, the subscription
    # row would be inert.
    integ = await asyncio.to_thread(
        lambda: svc.table("slack_integrations")
        .select("slack_team_id")
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not integ or not integ.data:
        raise HTTPException(status_code=400, detail="slack_not_connected")
    team_id: str = integ.data["slack_team_id"]

    # Resolve channel metadata server-side rather than trusting the body —
    # the bot must actually be a member for us to read from it.
    info = await slack_inbound.fetch_channel_info(
        org_id=org_id, channel_id=body.channel_id
    )
    if not info or not info.get("is_member"):
        raise HTTPException(
            status_code=400,
            detail="bot_not_in_channel",
        )

    row = {
        "org_id": org_id,
        "channel_id": body.channel_id,
        "channel_name": info.get("name") or body.channel_name,
        "is_private": bool(info.get("is_private")),
        "ingest_pins": body.ingest_pins,
        "ingest_canvases": body.ingest_canvases,
        "ingest_files": body.ingest_files,
        "ingest_messages": body.ingest_messages,
    }

    await asyncio.to_thread(
        lambda: svc.table("slack_channel_subscriptions")
        .upsert(row, on_conflict="org_id,channel_id")
        .execute()
    )

    # Kick off backfill.
    client = get_inngest_client()
    await client.send(
        inngest_pkg.Event(
            name="slack/backfill-channel",
            data={
                "org_id": org_id,
                "team_id": team_id,
                "channel_id": body.channel_id,
                "channel_name": row["channel_name"],
            },
            id=f"slack-backfill-{org_id}-{body.channel_id}-"
                f"{int(time.time())}",
        )
    )

    return {"status": "subscribed", "channel": row}


@router.delete(
    "/integrations/slack/subscriptions/{channel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def slack_unsubscribe_channel(
    channel_id: str,
    current_user: dict = Depends(verify_jwt),
) -> None:
    """Remove a channel from the allowlist. Existing ingested docs stay in
    place — admins can archive them manually via the documents page."""
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("slack_channel_subscriptions")
        .delete()
        .eq("org_id", org_id)
        .eq("channel_id", channel_id)
        .execute()
    )


@router.post(
    "/integrations/slack/subscriptions/{channel_id}/resync",
    status_code=status.HTTP_202_ACCEPTED,
)
async def slack_resync_channel(
    channel_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Trigger a fresh backfill for an existing subscription."""
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    svc = get_service_client()
    sub = await asyncio.to_thread(
        lambda: svc.table("slack_channel_subscriptions")
        .select("channel_id, channel_name")
        .eq("org_id", org_id)
        .eq("channel_id", channel_id)
        .maybe_single()
        .execute()
    )
    if not sub or not sub.data:
        raise HTTPException(status_code=404, detail="subscription_not_found")
    integ = await asyncio.to_thread(
        lambda: svc.table("slack_integrations")
        .select("slack_team_id")
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not integ or not integ.data:
        raise HTTPException(status_code=400, detail="slack_not_connected")
    client = get_inngest_client()
    await client.send(
        inngest_pkg.Event(
            name="slack/backfill-channel",
            data={
                "org_id": org_id,
                "team_id": integ.data["slack_team_id"],
                "channel_id": channel_id,
                "channel_name": sub.data.get("channel_name"),
            },
            id=f"slack-resync-{org_id}-{channel_id}-{int(time.time())}",
        )
    )
    return {"status": "queued"}
