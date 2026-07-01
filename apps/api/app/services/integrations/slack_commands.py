"""Slack /brain command router (Agent Day 13 Part A).

Extends the basic `/brain <question>` Q&A path with structured execution
verbs. Each verb is matched by a single anchored regex; non-matching text
falls through to the general Q&A path the way it always did.

Why regex (not LLM intent):

  Predictable cost, predictable latency. Slack expects an ACK within 3
  seconds and a regex test is sub-millisecond. The downside — users must
  hit the canonical phrasings — is mitigated by surfacing the patterns in
  Slack's slash-command help and `/brain help`.

Supported verbs (all start with a verb word; everything after is content):

  draft email to <recipient> about <topic>
  post <text> to <#channel>
  add action item: <task>
  onboard <name> as <role>
  summarize <topic>
  help    (lists the verbs above)

The dispatcher returns a serialized Slack response_url payload — the
caller (the slash-command background task) POSTs it directly. We don't
own the network call from here so the module stays unit-testable.
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from app.database import get_service_client
from app.observability import get_logger
from app.services.integrations.slack_block_kit import (
    email_draft_blocks,
    slack_post_blocks,
)
from app.services.llm.task_chain import execute_task_blocking

log = get_logger(__name__)


# ── Command patterns ────────────────────────────────────────────────────────

# Each pattern is anchored at the start, case-insensitive. The first group is
# the primary argument; the second (if present) is the secondary.
COMMAND_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\s*help\s*$", re.IGNORECASE), "help"),
    (re.compile(r"^\s*draft\s+email\s+to\s+(.+?)\s+about\s+(.+)$", re.IGNORECASE), "draft_email"),
    (re.compile(r"^\s*post\s+(.+?)\s+to\s+(<?#\S+>?|#\S+)\s*$", re.IGNORECASE), "post_to_channel"),
    (re.compile(r"^\s*add\s+action\s+item[:\s]+(.+)$", re.IGNORECASE), "add_action_item"),
    (re.compile(r"^\s*onboard\s+(.+?)\s+as\s+(.+)$", re.IGNORECASE), "onboard"),
    (re.compile(r"^\s*summari[sz]e\s+(.+)$", re.IGNORECASE), "summarize"),
)


# Public API shape: a handler is called with the matched groups + the
# common context dict and returns the Slack response_url payload (dict).
HandlerContext = dict[str, Any]
Handler = Callable[[tuple[str, ...], HandlerContext], Awaitable[dict[str, Any]]]


async def dispatch(
    *,
    command_text: str,
    org_id: str,
    user_id: str,
    channel_id: str,
    user_email: str | None = None,
) -> dict[str, Any] | None:
    """Match the slash-command text to a verb and execute it.

    Returns the Slack response_url payload to POST back to the user.
    Returns None if no command verb matched, signalling the caller to
    fall back to the default Q&A path.
    """
    ctx: HandlerContext = {
        "org_id": org_id,
        "user_id": user_id,
        "channel_id": channel_id,
        "user_email": user_email,
        "command_text": command_text,
    }

    for pattern, name in COMMAND_PATTERNS:
        m = pattern.match(command_text)
        if m:
            handler = _HANDLERS[name]
            try:
                return await handler(tuple(m.groups()), ctx)
            except Exception as exc:
                log.exception("slack_command_handler_failed", verb=name, err=str(exc))
                return _ephemeral(
                    f":warning: That command failed: `{type(exc).__name__}`. Try again in a moment."
                )
    return None  # Caller falls back to default Q&A


# ── Handler implementations ─────────────────────────────────────────────────

async def _handle_help(_groups: tuple[str, ...], _ctx: HandlerContext) -> dict[str, Any]:
    return {
        "response_type": "ephemeral",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Nirnaya IQ — what you can do with `/brain`*\n\n"
                        "_Questions_\n"
                        "• `/brain what's our refund policy?`\n\n"
                        "_Drafting_\n"
                        "• `/brain draft email to Sarah about Q3 budget cuts`\n"
                        "• `/brain post our new SOC2 update to #announcements`\n"
                        "• `/brain summarize this week's product launch`\n\n"
                        "_Workflow_\n"
                        "• `/brain add action item: ship invoice PDF by Friday`\n"
                        "• `/brain onboard Priya as Senior PM`\n"
                    ),
                },
            }
        ],
    }


async def _handle_draft_email(groups: tuple[str, ...], ctx: HandlerContext) -> dict[str, Any]:
    recipient_hint = groups[0].strip()
    topic = groups[1].strip()
    if not topic or not recipient_hint:
        return _ephemeral(
            ":warning: I couldn't parse that. Try: `/brain draft email to Sarah about Q3 budget cuts`"
        )

    draft = await execute_task_blocking(
        user_message=(
            f"Draft a short professional email to {recipient_hint} about: {topic}.\n"
            "Search the company knowledge base before answering — cite real "
            "facts. Plain text only, no markdown. Do not include 'Hi <name>' "
            "or a sign-off — the sender will add those before sending."
        ),
        org_id=ctx["org_id"],
        db_client=get_service_client(),
    )
    if draft.error or not (draft.text or "").strip():
        return _ephemeral(
            f":warning: Couldn't draft that email — {draft.error or 'no content returned'}."
        )

    # We need a stable id so the Send/Edit/Discard buttons can recover the
    # draft body server-side. We could insert into messages here, but that
    # would require a conversation row (and a user_id). Instead, persist the
    # draft to a transient `slack_draft_actions` map keyed by run/job id —
    # simplest path: pass the text inline via the button's `value` so the
    # interactions handler doesn't need a DB lookup. Block Kit `value` cap
    # is 2000 chars which works for the typical draft size.
    draft_id = _short_id(ctx["user_id"], topic)
    await _persist_slack_draft(
        org_id=ctx["org_id"],
        draft_id=draft_id,
        user_id=ctx["user_id"],
        body=draft.text.strip(),
        kind="email",
        meta={"recipient_hint": recipient_hint, "topic": topic},
        sources=draft.sources or [],
    )

    return {
        "response_type": "ephemeral",
        "blocks": email_draft_blocks(
            draft_body=draft.text.strip(),
            message_id=draft_id,
            recipient_hint=recipient_hint,
        ),
    }


async def _handle_post_to_channel(groups: tuple[str, ...], ctx: HandlerContext) -> dict[str, Any]:
    content_hint = groups[0].strip()
    raw_channel = groups[1].strip()
    channel_name = _normalize_channel_label(raw_channel)
    if not content_hint:
        return _ephemeral(
            ":warning: I couldn't parse that. Try: `/brain post our SOC2 update to #announcements`"
        )

    draft = await execute_task_blocking(
        user_message=(
            f"Draft a Slack message about: {content_hint}.\n"
            "Use the org's brand voice. Keep it under 150 words. Use light "
            "mrkdwn (bold for emphasis, bullets for lists). Search the "
            "knowledge base for relevant facts."
        ),
        org_id=ctx["org_id"],
        db_client=get_service_client(),
    )
    if draft.error or not (draft.text or "").strip():
        return _ephemeral(
            f":warning: Couldn't draft that post — {draft.error or 'no content returned'}."
        )

    draft_id = _short_id(ctx["user_id"], content_hint)
    await _persist_slack_draft(
        org_id=ctx["org_id"],
        draft_id=draft_id,
        user_id=ctx["user_id"],
        body=draft.text.strip(),
        kind="slack_post",
        meta={"target_channel_name": channel_name},
        sources=draft.sources or [],
    )

    return {
        "response_type": "ephemeral",
        "blocks": slack_post_blocks(
            draft_body=draft.text.strip(),
            message_id=draft_id,
            target_channel_hint=channel_name,
        ),
    }


async def _handle_add_action_item(groups: tuple[str, ...], ctx: HandlerContext) -> dict[str, Any]:
    task = groups[0].strip().rstrip(".")
    if not task:
        return _ephemeral(
            ":warning: I couldn't parse that. Try: `/brain add action item: ship invoice PDF by Friday`"
        )

    svc = get_service_client()
    try:
        import asyncio
        await asyncio.to_thread(
            lambda: svc.table("action_items").insert({
                "org_id": ctx["org_id"],
                "created_by": ctx["user_id"],
                "task": task[:500],
                "source": "slack_brain_command",
                "status": "open",
            }).execute()
        )
    except Exception as exc:
        # Schema may not have action_items table yet — degrade gracefully.
        log.info("action_item_persist_failed", err=str(exc))
        return _ephemeral(
            f":memo: Captured action item: *{task}*\n_(Not persisted: action-items module is offline.)_"
        )
    return _ephemeral(f":memo: Captured action item: *{task}*")


async def _handle_onboard(groups: tuple[str, ...], ctx: HandlerContext) -> dict[str, Any]:
    name = groups[0].strip()
    role = groups[1].strip()
    if not name or not role:
        return _ephemeral(
            ":warning: I couldn't parse that. Try: `/brain onboard Priya as Senior PM`"
        )
    # We can't actually invite + onboard from Slack alone — we'd need an
    # email at minimum. Point the user at the team UI; the onboarding agent
    # fires when invitations are accepted.
    return _ephemeral(
        f":wave: To onboard *{name}* as *{role}*, invite them at "
        f"`/team` with their email. The onboarding agent will run "
        f"automatically once they accept the invite."
    )


async def _handle_summarize(groups: tuple[str, ...], ctx: HandlerContext) -> dict[str, Any]:
    topic = groups[0].strip()
    if not topic:
        return _ephemeral(":warning: What should I summarize?")
    draft = await execute_task_blocking(
        user_message=(
            f"Summarize what the company knowledge base says about: {topic}.\n"
            "Search broadly. Return a 4-6 sentence overview with the most "
            "important facts. Cite the relevant document names inline."
        ),
        org_id=ctx["org_id"],
        db_client=get_service_client(),
    )
    if draft.error or not (draft.text or "").strip():
        return _ephemeral(
            f":warning: Couldn't summarise — {draft.error or 'no content returned'}."
        )

    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Summary — {topic}*\n\n{(draft.text or '')[:2700]}"}},
    ]
    if draft.sources:
        names = ", ".join(
            f"📄 {s.get('document_name') or 'Source'}"
            for s in draft.sources[:5]
        )
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"*Sources:* {names}"}],
        })
    return {
        "response_type": "ephemeral",
        "blocks": blocks,
    }


_HANDLERS: dict[str, Handler] = {
    "help": _handle_help,
    "draft_email": _handle_draft_email,
    "post_to_channel": _handle_post_to_channel,
    "add_action_item": _handle_add_action_item,
    "onboard": _handle_onboard,
    "summarize": _handle_summarize,
}


# ── Helpers ─────────────────────────────────────────────────────────────────


def _ephemeral(text: str) -> dict[str, Any]:
    return {"response_type": "ephemeral", "text": text}


def _normalize_channel_label(raw: str) -> str:
    """Slack <#C123|name> link → #name. Bare #name → #name. Anything else
    untouched."""
    m = re.match(r"^<#[^|]+\|([^>]+)>$", raw)
    if m:
        return f"#{m.group(1)}"
    return raw if raw.startswith("#") else f"#{raw}"


def _short_id(user_id: str, seed: str) -> str:
    """Compact id for Slack action_id suffixes (Block Kit caps action_id at
    255 chars but the value field at 2000). 16 hex chars is plenty."""
    import hashlib
    import time

    blob = f"{user_id}|{seed}|{int(time.time() * 1000)}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


async def _persist_slack_draft(
    *,
    org_id: str,
    draft_id: str,
    user_id: str,
    body: str,
    kind: str,
    meta: dict[str, Any],
    sources: list[dict[str, Any]],
) -> None:
    """Cache the Slack-originated draft for the interactions handler.

    Goes into Upstash for 1 hour — long enough that a user can step away
    and return to click Send, short enough that abandoned drafts age out.
    Falls back silently if Redis isn't available (the button just won't
    work, which is fine for V1).
    """
    from app.services.redis_cache import cache_set_json

    await cache_set_json(
        f"slack:draft:{org_id}:{draft_id}",
        {
            "kind": kind,
            "user_id": user_id,
            "body": body,
            "meta": meta,
            "sources": sources,
        },
        ttl_seconds=3600,
    )


async def get_slack_draft(*, org_id: str, draft_id: str) -> dict[str, Any] | None:
    """Read back a persisted Slack draft. Public so the interactions
    handler in slack_router.py can resolve action_id suffixes."""
    from app.services.redis_cache import cache_get_json

    data = await cache_get_json(f"slack:draft:{org_id}:{draft_id}")
    if isinstance(data, dict):
        return data
    return None
