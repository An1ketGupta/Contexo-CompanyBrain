"""Conversation summarization (Day 12 / #53).

Runs in the background so the chat hot path never pays the latency. The
Inngest `conversation/summarize` event fires after every assistant turn
lands; the handler short-circuits when a fresh summary isn't needed yet, so
99% of invocations are cheap.

Heuristic (matches the V2 roadmap):
    * Need at least 9 total messages before we summarize anything (a 6-turn
      live history covers everything shorter).
    * Re-summarize every 8 *new* messages (since the last summary).
    * Summarize everything except the last 6 messages — those stay in the
      live history window verbatim.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from supabase import Client

from app.database import get_service_client
from app.services.langfuse import observe
from app.services.llm import LLMClient, Message, get_llm_client

log = logging.getLogger(__name__)

# Numbers picked so a thread with ≥ 9 messages always has at least 3 to
# summarize (9 - 6 live = 3 historical), and so re-summarization happens
# roughly every 4 user-assistant pairs.
MIN_MESSAGES_TO_SUMMARIZE = 9
LIVE_HISTORY_KEEP = 6
NEW_MSGS_TRIGGER = 8

_SUMMARY_PROMPT = (
    "Summarize the conversation below in 3 short bullet points. "
    "Capture: (1) what the user is working on or asking about, (2) any key "
    "facts, names, or decisions established so far, and (3) constraints or "
    "preferences the user has expressed. Keep each bullet under 25 words. "
    "Output ONLY the bullets — no preamble, no closing line."
)


@observe(name="summarize_conversation")
async def summarize_conversation(
    *,
    conversation_id: str,
    client: Client | None = None,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    """Idempotent: safe to call repeatedly; only writes when a refresh is due.

    Returns a small dict for Inngest step output / log payloads:
        {"status": "skipped" | "summarized", "messages": N, ...}
    """
    client = client or get_service_client()
    llm = llm or get_llm_client()

    convo = await asyncio.to_thread(
        lambda: client.table("conversations")
        .select("id, summary, summary_turn_count")
        .eq("id", conversation_id)
        .maybe_single()
        .execute()
    )
    if not convo or not convo.data:
        return {"status": "skipped", "reason": "conversation_not_found"}

    summary_turn_count = int(convo.data.get("summary_turn_count") or 0)

    msgs_resp = await asyncio.to_thread(
        lambda: client.table("messages")
        .select("role, content")
        .eq("conversation_id", conversation_id)
        .order("created_at")
        .execute()
    )
    messages = msgs_resp.data or []
    total = len(messages)

    if total < MIN_MESSAGES_TO_SUMMARIZE:
        return {"status": "skipped", "reason": "too_short", "messages": total}
    if total - summary_turn_count < NEW_MSGS_TRIGGER:
        return {"status": "skipped", "reason": "no_new_turns", "messages": total}

    to_summarize = messages[:-LIVE_HISTORY_KEEP]
    if not to_summarize:
        return {"status": "skipped", "reason": "all_in_live_window", "messages": total}

    summary_text = await _summarize_messages(llm, to_summarize)
    if not summary_text:
        return {"status": "skipped", "reason": "empty_llm_output", "messages": total}

    await asyncio.to_thread(
        lambda: client.table("conversations")
        .update({"summary": summary_text, "summary_turn_count": total})
        .eq("id", conversation_id)
        .execute()
    )
    log.info(
        "summarize_conversation persisted: convo=%s total_msgs=%d summarized=%d",
        conversation_id, total, len(to_summarize),
    )
    return {
        "status": "summarized",
        "messages": total,
        "summarized": len(to_summarize),
    }


async def _summarize_messages(llm: LLMClient, messages: list[dict[str, Any]]) -> str:
    """Cheap one-shot LLM call. No tools, no system_extra — just a digest."""
    formatted = _format_messages_for_summary(messages)
    prompt = f"{_SUMMARY_PROMPT}\n\nConversation:\n{formatted}"
    try:
        response = await llm.complete(
            [Message(role="user", content=prompt)],
            tools=(),
            system_extra=None,
        )
    except Exception as exc:
        log.warning("summarize_conversation LLM call failed: %s", exc)
        return ""
    text = (response.text or "").strip()
    # Cap stored length — the column is TEXT but we don't want runaway output
    # eating prompt budget on every future turn.
    if len(text) > 1200:
        text = text[:1200].rstrip() + "…"
    return text


def _format_messages_for_summary(messages: list[dict[str, Any]]) -> str:
    """Render messages as "User: …" / "Assistant: …" lines for the summary prompt."""
    lines: list[str] = []
    for m in messages:
        role = m.get("role", "user")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        label = "User" if role == "user" else "Assistant"
        # Trim each turn so we don't blow the model's context with a single
        # long message hogging the budget.
        if len(content) > 800:
            content = content[:800].rstrip() + "…"
        lines.append(f"{label}: {content}")
    return "\n".join(lines)


async def load_conversation_summary(
    conversation_id: str,
    *,
    client: Client | None = None,
) -> str | None:
    """Fast read of the cached summary. Returns None if unset."""
    client = client or get_service_client()
    result = await asyncio.to_thread(
        lambda: client.table("conversations")
        .select("summary")
        .eq("id", conversation_id)
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        return None
    summary = result.data.get("summary")
    return summary if isinstance(summary, str) and summary.strip() else None
