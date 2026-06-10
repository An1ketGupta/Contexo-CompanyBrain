"""Slack bot (Day 15 / #31).

Endpoints:
    GET  /integrations/slack/connect   — returns slack.com OAuth URL
    GET  /integrations/slack/callback  — code exchange + install
    POST /slack/events                 — slash command + (future) events

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
from typing import Any
from urllib.parse import parse_qs, urlencode

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse, RedirectResponse

from app.auth import verify_jwt
from app.config import get_settings
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
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
    params = {
        "client_id": settings.slack_client_id,
        "scope": "chat:write,commands",
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

    svc = get_service_client()
    row = {
        "org_id": org_id,
        "slack_team_id": team["id"],
        "slack_team_name": team.get("name"),
        "bot_token": access,
        "bot_user_id": bot_user,
        "installed_by": user_id,
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
        # We don't subscribe to event types yet — accept and drop so the
        # subscription list can be tweaked without redeploying.
        return {"ok": True}

    # form-urlencoded slash command
    parsed = parse_qs(body.decode("utf-8"))
    command = (parsed.get("command") or [""])[0]
    if command != "/brain":
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
    """Run execute_task_blocking, post the answer + sources back to Slack.

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


def _settings_redirect(*, connected: str | None = None, error: str | None = None) -> RedirectResponse:
    settings = get_settings()
    base = settings.app_url.rstrip("/") + "/settings/integrations"
    if connected:
        return RedirectResponse(url=f"{base}?connected={connected}", status_code=302)
    if error:
        return RedirectResponse(url=f"{base}?error={error}", status_code=302)
    return RedirectResponse(url=base, status_code=302)
