"""Chat endpoints — non-streaming /chat and SSE /chat/stream.

Both routes share `execute_task` from task_chain. The streaming route is what
the UI uses in production; the non-streaming one is here for tests, scripts,
and any future use-case that wants a single JSON response (e.g. a cron job
that auto-drafts emails).

Conversation lifecycle:
    * If `conversation_id` is omitted, a new conversation is created with a
      title derived from the first user message.
    * History is the last N messages on the conversation (configurable via
      settings.chat_history_turns), ordered oldest → newest.
    * Each request persists a user row and an assistant row with `sources`.
    * All DB calls use a user-scoped Supabase client so RLS enforces tenancy.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from supabase import Client

from app.auth import verify_jwt
from app.config import get_settings
from app.database import get_user_client
from app.services.llm import Message
from app.services.llm.task_chain import (
    ErrorEvent,
    FinalEvent,
    OrchestratorEvent,
    SearchedEvent,
    SearchingEvent,
    SourcesEvent,
    TokenEvent,
    execute_task,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

TITLE_MAX_LEN = 80


# ── Request models ───────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    message_id: str
    output: str
    sources: list[dict]
    tool_calls: int


# ── Routes ───────────────────────────────────────────────────────────────────

@router.post("", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    current_user: dict = Depends(verify_jwt),
) -> ChatResponse:
    """One-shot task execution. Returns the full output once retrieval +
    generation are complete. Use /chat/stream for interactive UIs."""
    org_id, user_id, token = _require_org(current_user)
    client = get_user_client(token)

    conversation_id = await _ensure_conversation(
        client, body.conversation_id, org_id, user_id, body.message
    )
    history = await _load_history(client, conversation_id)

    user_message_id = await _save_message(
        client,
        conversation_id=conversation_id,
        org_id=org_id,
        role="user",
        content=body.message,
        sources=None,
    )

    final_text = ""
    final_sources: list[dict] = []
    tool_calls_made = 0
    error_msg: str | None = None

    async for event in execute_task(
        user_message=body.message,
        org_id=org_id,
        db_client=client,
        history=history,
        stream=False,
    ):
        if isinstance(event, FinalEvent):
            final_text = event.text
            final_sources = event.sources
            tool_calls_made = event.tool_calls_made
        elif isinstance(event, ErrorEvent):
            error_msg = event.message

    if error_msg:
        # Best effort: still record the user message so the conversation is
        # consistent; surface the error to the caller.
        log.warning("execute_task error for conv %s: %s", conversation_id, error_msg)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=error_msg,
        )

    if not final_text:
        final_text = "I couldn't generate a response. Please try again or rephrase."

    assistant_id = await _save_message(
        client,
        conversation_id=conversation_id,
        org_id=org_id,
        role="assistant",
        content=final_text,
        sources=final_sources,
    )

    await _touch_conversation(client, conversation_id)

    return ChatResponse(
        conversation_id=conversation_id,
        message_id=assistant_id,
        output=final_text,
        sources=final_sources,
        tool_calls=tool_calls_made,
    )


@router.post("/stream")
async def chat_stream(
    body: ChatRequest,
    current_user: dict = Depends(verify_jwt),
) -> StreamingResponse:
    """Server-Sent Events stream of orchestrator events.

    Event types: `searching`, `searched`, `sources`, `token`, `final`, `error`.
    See task_chain.py for full event semantics.
    """
    org_id, user_id, token = _require_org(current_user)
    client = get_user_client(token)

    conversation_id = await _ensure_conversation(
        client, body.conversation_id, org_id, user_id, body.message
    )
    history = await _load_history(client, conversation_id)

    await _save_message(
        client,
        conversation_id=conversation_id,
        org_id=org_id,
        role="user",
        content=body.message,
        sources=None,
    )

    async def event_stream() -> AsyncIterator[bytes]:
        # The very first SSE event is a small `start` so the client knows which
        # conversation row to attach to (useful when no conversation_id was
        # provided in the request).
        yield _sse({"type": "start", "conversation_id": conversation_id})

        final_text = ""
        final_sources: list[dict] = []
        tool_calls_made = 0
        had_error = False

        try:
            async for event in execute_task(
                user_message=body.message,
                org_id=org_id,
                db_client=client,
                history=history,
                stream=True,
            ):
                payload = _event_to_payload(event)
                if payload is not None:
                    yield _sse(payload)

                if isinstance(event, FinalEvent):
                    final_text = event.text
                    final_sources = event.sources
                    tool_calls_made = event.tool_calls_made
                elif isinstance(event, ErrorEvent):
                    had_error = True
        except Exception as exc:
            log.exception("Unhandled error in chat stream: %s", exc)
            yield _sse({"type": "error", "message": "Internal error during generation."})
            return

        if not had_error and final_text:
            try:
                assistant_id = await _save_message(
                    client,
                    conversation_id=conversation_id,
                    org_id=org_id,
                    role="assistant",
                    content=final_text,
                    sources=final_sources,
                )
                await _touch_conversation(client, conversation_id)
                yield _sse(
                    {
                        "type": "done",
                        "message_id": assistant_id,
                        "tool_calls": tool_calls_made,
                    }
                )
            except Exception as exc:
                log.exception("Failed to persist assistant message: %s", exc)
                yield _sse({"type": "error", "message": "Failed to save response."})
        elif not had_error:
            # No text, no error — model gave up. Tell the client.
            yield _sse(
                {
                    "type": "error",
                    "message": "Empty response from the model. Try rephrasing.",
                }
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Disable buffering on common reverse proxies (Vercel, Nginx).
            "X-Accel-Buffering": "no",
        },
    )


# ── List endpoints ───────────────────────────────────────────────────────────

@router.get("/conversations")
async def list_conversations(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _, token = _require_org(current_user)
    client = get_user_client(token)

    result = await asyncio.to_thread(
        lambda: client.table("conversations")
        .select("id, title, created_at, updated_at")
        .order("updated_at", desc=True)
        .limit(50)
        .execute()
    )
    return {"conversations": result.data or []}


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    _, _, token = _require_org(current_user)
    client = get_user_client(token)

    convo = await asyncio.to_thread(
        lambda: client.table("conversations")
        .select("id, title, created_at, updated_at")
        .eq("id", conversation_id)
        .maybe_single()
        .execute()
    )
    if not convo.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")

    msgs = await asyncio.to_thread(
        lambda: client.table("messages")
        .select("id, role, content, sources, created_at")
        .eq("conversation_id", conversation_id)
        .order("created_at")
        .execute()
    )
    return {"conversation": convo.data, "messages": msgs.data or []}


# ── DB helpers ───────────────────────────────────────────────────────────────

def _require_org(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No organization found. Please sign out and sign back in.",
        )
    return org_id, user_id, token


async def _ensure_conversation(
    client: Client,
    conversation_id: str | None,
    org_id: str,
    user_id: str,
    first_message: str,
) -> str:
    if conversation_id:
        check = await asyncio.to_thread(
            lambda: client.table("conversations")
            .select("id")
            .eq("id", conversation_id)
            .maybe_single()
            .execute()
        )
        if not check.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
        return conversation_id

    new_id = str(uuid.uuid4())
    title = _derive_title(first_message)
    await asyncio.to_thread(
        lambda: client.table("conversations")
        .insert(
            {
                "id": new_id,
                "org_id": org_id,
                "user_id": user_id,
                "title": title,
            }
        )
        .execute()
    )
    return new_id


async def _load_history(client: Client, conversation_id: str) -> list[Message]:
    settings = get_settings()
    n = settings.chat_history_turns
    # Pull the last N rows (newest first), then reverse for LLM ingestion.
    result = await asyncio.to_thread(
        lambda: client.table("messages")
        .select("role, content")
        .eq("conversation_id", conversation_id)
        .order("created_at", desc=True)
        .limit(n)
        .execute()
    )
    rows = list(reversed(result.data or []))
    out: list[Message] = []
    for row in rows:
        role = row["role"]
        if role not in ("user", "assistant"):
            continue
        content = row.get("content") or ""
        if not content:
            continue
        out.append(Message(role=role, content=content))
    return out


async def _save_message(
    client: Client,
    *,
    conversation_id: str,
    org_id: str,
    role: str,
    content: str,
    sources: list[dict] | None,
) -> str:
    new_id = str(uuid.uuid4())
    await asyncio.to_thread(
        lambda: client.table("messages")
        .insert(
            {
                "id": new_id,
                "conversation_id": conversation_id,
                "org_id": org_id,
                "role": role,
                "content": content,
                "sources": sources,
            }
        )
        .execute()
    )
    return new_id


async def _touch_conversation(client: Client, conversation_id: str) -> None:
    try:
        await asyncio.to_thread(
            lambda: client.table("conversations")
            .update({"updated_at": "now()"})
            .eq("id", conversation_id)
            .execute()
        )
    except Exception as exc:
        # Not critical; conversation listing just won't bump.
        log.warning("Failed to bump conversation %s updated_at: %s", conversation_id, exc)


def _derive_title(message: str) -> str:
    cleaned = " ".join(message.split())
    if len(cleaned) <= TITLE_MAX_LEN:
        return cleaned
    return cleaned[: TITLE_MAX_LEN - 1].rstrip() + "…"


# ── SSE helpers ──────────────────────────────────────────────────────────────

def _sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode("utf-8")


def _event_to_payload(event: OrchestratorEvent) -> dict | None:
    if isinstance(event, SearchingEvent):
        return {"type": "searching", "query": event.query}
    if isinstance(event, SearchedEvent):
        return {"type": "searched", "query": event.query, "hit_count": event.hit_count}
    if isinstance(event, SourcesEvent):
        return {"type": "sources", "sources": event.sources}
    if isinstance(event, TokenEvent):
        return {"type": "token", "text": event.text}
    if isinstance(event, FinalEvent):
        # Don't emit `final` directly — the `done` event includes message_id
        # after we've persisted. Suppress here.
        return None
    if isinstance(event, ErrorEvent):
        return {"type": "error", "message": event.message}
    return None
