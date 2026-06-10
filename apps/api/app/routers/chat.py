from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

import inngest
from typing import Any, AsyncIterator, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from supabase import Client

from app.auth import verify_jwt
from app.config import get_settings
from app.database import get_user_client
from app.errors import NoOrganization
from app.observability import get_logger
from app.services.llm import Message
from app.services.rate_limit import enforce_chat_quota
from app.inngest import get_inngest_client
from app.services.citation_tracker import record_citations
from app.services.summarization import load_conversation_summary
from app.services.webhooks import trigger_event as trigger_webhook_event
from app.services.llm.task_chain import (
    ConfidenceEvent,
    ErrorEvent,
    FinalEvent,
    KnowledgeGapEvent,
    OrchestratorEvent,
    SearchedEvent,
    SearchingEvent,
    SourcesEvent,
    TokenEvent,
    execute_task,
)

log = get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

TITLE_MAX_LEN = 80

# SSE keepalive cadence. 15s is comfortably under typical reverse-proxy idle
# timeouts (Vercel = 30s, Cloudflare = 100s, generic Nginx default = 60s).
SSE_HEARTBEAT_SECONDS = 15.0


# ── Request models ───────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    # Upper bound mirrors settings.chat_max_message_chars; we hard-cap here so
    # Pydantic rejects oversized payloads before they reach the LLM.
    message: str = Field(..., min_length=1, max_length=16_000)
    conversation_id: str | None = None
    # Optional client-generated UUID. When supplied, persists the user message
    # under this id so a retried request is idempotent: the SECOND attempt
    # sees the row already exists and reuses it instead of duplicating.
    client_message_id: str | None = Field(default=None, min_length=8, max_length=64)
    # Day-10 #100 (Document Q&A Mode): when present on a *new* conversation,
    # pins all retrieval for that conversation to the named document. Ignored
    # on follow-up turns of an existing conversation — scope is set once at
    # creation so the LLM can rely on it across the whole thread.
    scoped_document_id: str | None = Field(default=None, min_length=8, max_length=64)


class RenameConversationRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=TITLE_MAX_LEN)


class FeedbackBody(BaseModel):
    # Tri-state: 'positive', 'negative', or null to clear an existing rating.
    # Sent from the thumbs UI; the second click on the same icon nulls it out.
    feedback: Literal["positive", "negative"] | None


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
    request: Request,
    current_user: dict = Depends(verify_jwt),
) -> ChatResponse:
    """One-shot task execution. Returns the full output once retrieval +
    generation are complete. Use /chat/stream for interactive UIs."""
    org_id, user_id, token = _require_org(current_user)
    await enforce_chat_quota(user_id=user_id, org_id=org_id)
    client = get_user_client(token)

    conversation_id, scoped_document_id = await _ensure_conversation(
        client, body.conversation_id, org_id, user_id, body.message,
        scoped_document_id=body.scoped_document_id,
    )
    history = await _load_history(client, conversation_id)
    summary = await load_conversation_summary(conversation_id, client=client)

    await _save_user_message_idempotent(
        client,
        conversation_id=conversation_id,
        org_id=org_id,
        content=body.message,
        client_message_id=body.client_message_id,
    )

    final_text = ""
    final_sources: list[dict] = []
    tool_calls_made = 0
    trace_id: str | None = None
    confidence: ConfidenceEvent | None = None
    error_msg: str | None = None

    async for event in execute_task(
        user_message=body.message,
        org_id=org_id,
        db_client=client,
        history=history,
        stream=False,
        user_id=user_id,
        conversation_id=conversation_id,
        scoped_document_id=scoped_document_id,
        conversation_summary=summary,
    ):
        if isinstance(event, FinalEvent):
            final_text = event.text
            final_sources = event.sources
            tool_calls_made = event.tool_calls_made
            trace_id = event.trace_id
        elif isinstance(event, ConfidenceEvent):
            confidence = event
        elif isinstance(event, ErrorEvent):
            error_msg = event.message

    if error_msg:
        log.warning("execute_task_error", conversation_id=conversation_id, error=error_msg)
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
        trace_id=trace_id,
        confidence=confidence,
    )

    await _touch_conversation(client, conversation_id)
    await _fire_summarize_event(conversation_id, org_id)
    await record_citations(
        sources=final_sources,
        message_id=assistant_id,
        conversation_id=conversation_id,
        org_id=org_id,
    )
    # Day-13: notify subscribers that a turn completed. Fire-and-forget;
    # webhook delivery happens off the chat hot path via Inngest.
    await _fire_query_completed_webhook(
        org_id=org_id,
        conversation_id=conversation_id,
        message_id=assistant_id,
        user_id=user_id,
        sources=final_sources,
        output=final_text,
    )

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
    request: Request,
    current_user: dict = Depends(verify_jwt),
) -> StreamingResponse:
    """Server-Sent Events stream of orchestrator events.

    Event types: `start`, `searching`, `searched`, `sources`, `token`, `done`,
    `error`. See task_chain.py for full event semantics. Heartbeats are sent
    as SSE comment frames (`: keepalive\\n\\n`) and never appear to consumers
    that filter on `data:` lines.
    """
    org_id, user_id, token = _require_org(current_user)
    # Rate-limit raises with our envelope — the StreamingResponse never starts
    # so the browser sees a normal JSON error, not a half-open SSE.
    await enforce_chat_quota(user_id=user_id, org_id=org_id)

    client = get_user_client(token)
    request_id = getattr(request.state, "request_id", None)

    conversation_id, scoped_document_id = await _ensure_conversation(
        client, body.conversation_id, org_id, user_id, body.message,
        scoped_document_id=body.scoped_document_id,
    )
    history = await _load_history(client, conversation_id)
    summary = await load_conversation_summary(conversation_id, client=client)

    await _save_user_message_idempotent(
        client,
        conversation_id=conversation_id,
        org_id=org_id,
        content=body.message,
        client_message_id=body.client_message_id,
    )

    async def event_stream() -> AsyncIterator[bytes]:
        # Tell the client which conversation row this turn attaches to.
        yield _sse({"type": "start", "conversation_id": conversation_id})

        # Heartbeat orchestration: we wrap execute_task() in a queue-and-pump
        # pattern so we can interleave keepalive frames during long tool-call
        # phases (where the model is searching/thinking and emitting nothing).
        events_queue: asyncio.Queue[OrchestratorEvent | None] = asyncio.Queue()

        async def producer() -> None:
            try:
                async for ev in execute_task(
                    user_message=body.message,
                    org_id=org_id,
                    db_client=client,
                    history=history,
                    stream=True,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    scoped_document_id=scoped_document_id,
                    conversation_summary=summary,
                ):
                    await events_queue.put(ev)
            finally:
                await events_queue.put(None)  # sentinel

        final_text = ""
        final_sources: list[dict] = []
        tool_calls_made = 0
        trace_id: str | None = None
        confidence_evt: ConfidenceEvent | None = None
        had_error = False
        error_payload: dict | None = None

        producer_task = asyncio.create_task(producer())
        last_emit = time.monotonic()

        try:
            while True:
                # Wait for either the next event or a heartbeat tick.
                try:
                    timeout = max(0.1, SSE_HEARTBEAT_SECONDS - (time.monotonic() - last_emit))
                    ev = await asyncio.wait_for(events_queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    yield b": keepalive\n\n"
                    last_emit = time.monotonic()
                    continue

                if ev is None:
                    break  # producer finished

                payload = _event_to_payload(ev)
                if payload is not None:
                    yield _sse(payload)
                    last_emit = time.monotonic()

                if isinstance(ev, FinalEvent):
                    final_text = ev.text
                    final_sources = ev.sources
                    tool_calls_made = ev.tool_calls_made
                    trace_id = ev.trace_id
                elif isinstance(ev, ConfidenceEvent):
                    confidence_evt = ev
                elif isinstance(ev, ErrorEvent):
                    had_error = True
                    error_payload = {
                        "type": "error",
                        "code": "upstream_unavailable",
                        "message": ev.message,
                        "request_id": request_id,
                    }
        except Exception as exc:
            log.exception("chat_stream_unhandled", error=str(exc))
            yield _sse(
                {
                    "type": "error",
                    "code": "internal_error",
                    "message": "Something broke while generating. Try again.",
                    "request_id": request_id,
                }
            )
            producer_task.cancel()
            return
        finally:
            if not producer_task.done():
                producer_task.cancel()

        if had_error and error_payload is not None:
            # We already streamed the user-visible error from the orchestrator;
            # add the request_id so the frontend can quote it.
            yield _sse(error_payload)
            return

        if final_text:
            try:
                assistant_id = await _save_message(
                    client,
                    conversation_id=conversation_id,
                    org_id=org_id,
                    role="assistant",
                    content=final_text,
                    sources=final_sources,
                    trace_id=trace_id,
                    confidence=confidence_evt,
                )
                await _touch_conversation(client, conversation_id)
                await _fire_summarize_event(conversation_id, org_id)
                await record_citations(
                    sources=final_sources,
                    message_id=assistant_id,
                    conversation_id=conversation_id,
                    org_id=org_id,
                )
                await _fire_query_completed_webhook(
                    org_id=org_id,
                    conversation_id=conversation_id,
                    message_id=assistant_id,
                    user_id=user_id,
                    sources=final_sources,
                    output=final_text,
                )
                yield _sse(
                    {
                        "type": "done",
                        "message_id": assistant_id,
                        "tool_calls": tool_calls_made,
                    }
                )
            except Exception as exc:
                log.exception("assistant_save_failed", error=str(exc))
                yield _sse(
                    {
                        "type": "error",
                        "code": "internal_error",
                        "message": "Generated, but failed to save. Try sending again.",
                        "request_id": request_id,
                    }
                )
        else:
            # No text, no error — model gave up. Tell the client.
            yield _sse(
                {
                    "type": "error",
                    "code": "upstream_unavailable",
                    "message": "Empty response from the model. Try rephrasing.",
                    "request_id": request_id,
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
    q: str | None = None,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """List conversations, optionally filtered by a free-text search.

    With no `q`, returns the most-recently-active conversations the caller
    owns. With `q`, runs the `search_conversations` SQL function which:
      * Matches against both conversation titles and message contents
      * Uses `websearch_to_tsquery` for permissive user-shaped input
      * Returns relevance-ranked results (title hits weighted 2× message hits)
    Search is server-side; RLS still applies via SECURITY INVOKER.
    """
    _, _, token = _require_org(current_user)
    client = get_user_client(token)

    query = (q or "").strip()
    if not query:
        result = await asyncio.to_thread(
            lambda: client.table("conversations")
            .select("id, title, created_at, updated_at")
            .order("updated_at", desc=True)
            .limit(50)
            .execute()
        )
        return {"conversations": result.data or []}

    # Cap query length — the RPC handles syntax safely, but we don't want a
    # 10kB blob round-tripping through PostgREST as a query param.
    if len(query) > 200:
        query = query[:200]

    result = await asyncio.to_thread(
        lambda: client.rpc(
            "search_conversations",
            {"search_query": query, "result_limit": 50},
        ).execute()
    )
    rows = result.data or []
    # The RPC returns a `rank` column — drop it for API consumers; sidebar
    # cares about order, not numeric weights.
    convos = [
        {
            "id": r["id"],
            "title": r["title"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]
    return {"conversations": convos, "query": query}


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    _, _, token = _require_org(current_user)
    client = get_user_client(token)

    convo = await asyncio.to_thread(
        lambda: client.table("conversations")
        .select("id, title, created_at, updated_at, scoped_document_id")
        .eq("id", conversation_id)
        .maybe_single()
        .execute()
    )
    if not convo.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")

    msgs = await asyncio.to_thread(
        lambda: client.table("messages")
        .select("id, role, content, sources, feedback, metadata, created_at")
        .eq("conversation_id", conversation_id)
        .order("created_at")
        .execute()
    )
    return {"conversation": convo.data, "messages": msgs.data or []}


@router.patch("/conversations/{conversation_id}")
async def rename_conversation(
    conversation_id: str,
    body: RenameConversationRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    _, _, token = _require_org(current_user)
    client = get_user_client(token)

    cleaned = " ".join(body.title.split())[:TITLE_MAX_LEN].strip()
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Title cannot be empty.",
        )

    result = await asyncio.to_thread(
        lambda: client.table("conversations")
        .update({"title": cleaned})
        .eq("id", conversation_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    return {"conversation": result.data[0]}


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    current_user: dict = Depends(verify_jwt),
) -> None:
    _, _, token = _require_org(current_user)
    client = get_user_client(token)

    # RLS confines the delete to the caller's org. Messages cascade via the FK
    # on conversation_id (see migration 001).
    result = await asyncio.to_thread(
        lambda: client.table("conversations")
        .delete()
        .eq("id", conversation_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")


@router.patch("/messages/{message_id}/feedback")
async def update_message_feedback(
    message_id: str,
    body: FeedbackBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Record a thumbs-up / thumbs-down on an assistant message.

    Authz model: the `messages` RLS policy is org-wide, but we narrow it here
    to "only the conversation owner can rate" — otherwise a teammate could
    rate a message in someone else's chat, which would muddy the training
    signal. The conversations RLS already filters to user_id = auth.uid(), so
    a successful lookup of the parent conversation proves ownership.
    """
    if not _is_uuid(message_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid message id.")

    _, _, token = _require_org(current_user)
    client = get_user_client(token)

    msg = await asyncio.to_thread(
        lambda: client.table("messages")
        .select("id, role, conversation_id, langfuse_trace_id")
        .eq("id", message_id)
        .maybe_single()
        .execute()
    )
    if not msg or not msg.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found.")
    if msg.data.get("role") != "assistant":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only assistant messages can be rated.",
        )

    convo = await asyncio.to_thread(
        lambda: client.table("conversations")
        .select("id")
        .eq("id", msg.data["conversation_id"])
        .maybe_single()
        .execute()
    )
    if not convo or not convo.data:
        # RLS hid the conversation from this user — they don't own it.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your conversation.")

    result = await asyncio.to_thread(
        lambda: client.table("messages")
        .update({"feedback": body.feedback})
        .eq("id", message_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found.")

    # Forward to Langfuse so we can correlate quality with prompt/retrieval
    # variants in the dashboard. Skip clears (feedback=None) — Langfuse scores
    # are append-only; an unset feels weird to model as a score event.
    trace_id = msg.data.get("langfuse_trace_id")
    if trace_id and body.feedback is not None:
        from app.services.langfuse import score_feedback

        score_feedback(
            trace_id=trace_id,
            value=1 if body.feedback == "positive" else 0,
            comment=f"User rated {body.feedback}",
        )

    return {"feedback": body.feedback}


# ── DB helpers ───────────────────────────────────────────────────────────────

def _require_org(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return org_id, user_id, token


async def _ensure_conversation(
    client: Client,
    conversation_id: str | None,
    org_id: str,
    user_id: str,
    first_message: str,
    scoped_document_id: str | None = None,
) -> tuple[str, str | None]:
    """Look up or create the conversation; return (id, scoped_document_id).

    Scope is only honored at *creation* — we ignore an attempt to retro-scope
    an existing conversation because the LLM may have already produced
    answers under the assumption that all docs were available.
    """
    if conversation_id:
        check = await asyncio.to_thread(
            lambda: client.table("conversations")
            .select("id, scoped_document_id")
            .eq("id", conversation_id)
            .maybe_single()
            .execute()
        )
        if not check.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
        return conversation_id, check.data.get("scoped_document_id")

    # New conversation — validate scope (if provided) before persisting.
    persisted_scope: str | None = None
    if scoped_document_id:
        if not _is_uuid(scoped_document_id):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid document id.")
        doc = await asyncio.to_thread(
            lambda: client.table("documents")
            .select("id")
            .eq("id", scoped_document_id)
            .maybe_single()
            .execute()
        )
        if not doc or not doc.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scoped document not found.")
        persisted_scope = scoped_document_id

    new_id = str(uuid.uuid4())
    title = _derive_title(first_message)
    row: dict[str, Any] = {
        "id": new_id,
        "org_id": org_id,
        "user_id": user_id,
        "title": title,
    }
    if persisted_scope:
        row["scoped_document_id"] = persisted_scope
    await asyncio.to_thread(
        lambda: client.table("conversations").insert(row).execute()
    )
    return new_id, persisted_scope


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


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


async def _save_user_message_idempotent(
    client: Client,
    *,
    conversation_id: str,
    org_id: str,
    content: str,
    client_message_id: str | None,
) -> str:
    """Insert the user message, dedup'd by client_message_id when provided.

    Why we don't just rely on the DB to throw on duplicate primary key:
    the supabase-py client raises a generic APIError, and parsing PG error
    codes is brittle. A pre-check is simpler and the row count here is tiny.
    """
    # Only use the client-supplied id if it parses as a UUID — the messages.id
    # column is uuid-typed and we don't want to widen it.
    if client_message_id and _is_uuid(client_message_id):
        existing = await asyncio.to_thread(
            lambda: client.table("messages")
            .select("id")
            .eq("id", client_message_id)
            .eq("conversation_id", conversation_id)
            .maybe_single()
            .execute()
        )
        if getattr(existing, "data", None):
            return client_message_id
        message_id = client_message_id
    else:
        message_id = str(uuid.uuid4())

    await asyncio.to_thread(
        lambda: client.table("messages")
        .insert(
            {
                "id": message_id,
                "conversation_id": conversation_id,
                "org_id": org_id,
                "role": "user",
                "content": content,
                "sources": None,
            }
        )
        .execute()
    )
    return message_id


async def _save_message(
    client: Client,
    *,
    conversation_id: str,
    org_id: str,
    role: str,
    content: str,
    sources: list[dict] | None,
    trace_id: str | None = None,
    confidence: ConfidenceEvent | None = None,
) -> str:
    new_id = str(uuid.uuid4())
    metadata: dict[str, Any] = {}
    if confidence is not None:
        metadata["confidence"] = {
            "level": confidence.level,
            "score": confidence.score,
            "n": confidence.chunks_considered,
        }
    row: dict[str, Any] = {
        "id": new_id,
        "conversation_id": conversation_id,
        "org_id": org_id,
        "role": role,
        "content": content,
        "sources": sources,
    }
    if trace_id:
        row["langfuse_trace_id"] = trace_id
    if metadata:
        row["metadata"] = metadata
    await asyncio.to_thread(
        lambda: client.table("messages").insert(row).execute()
    )
    return new_id


async def _fire_query_completed_webhook(
    *,
    org_id: str,
    conversation_id: str,
    message_id: str,
    user_id: str,
    sources: list[dict],
    output: str,
) -> None:
    """Day 13 / #99 — best-effort `query.completed` webhook fan-out.

    Trimmed payload: we don't ship the full message body to third parties by
    default (it can contain confidential org context). Consumers that want
    the body can fetch it via the API using the message_id.
    """
    try:
        await trigger_webhook_event(
            org_id=org_id,
            event="query.completed",
            payload={
                "conversation_id": conversation_id,
                "message_id": message_id,
                "user_id": user_id,
                "output_chars": len(output or ""),
                "source_count": len(sources or []),
                "source_documents": list(
                    {s.get("document_id") for s in (sources or []) if s.get("document_id")}
                ),
            },
        )
    except Exception as exc:
        log.warning("webhook_fire_failed", event="query.completed", error=str(exc))


async def _fire_summarize_event(conversation_id: str, org_id: str) -> None:
    """Tail of every assistant turn — let Inngest decide if a refresh is due.

    Idempotency keyed by conversation + minute so a quick double-fire from
    retries doesn't enqueue twice, but a deliberate next turn still does.
    Best-effort: a failed send is logged and dropped (the summary just
    becomes stale until the next successful trigger).
    """
    try:
        client = get_inngest_client()
        await client.send(
            inngest.Event(
                name="conversation/turn-saved",
                data={"conversation_id": conversation_id, "org_id": org_id},
                id=f"conv-summarize-{conversation_id}-{int(time.time() // 60)}",
            )
        )
    except Exception as exc:
        log.warning(
            "summarize_event_failed",
            conversation_id=conversation_id, error=str(exc),
        )


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
        log.warning("conversation_touch_failed", conversation_id=conversation_id, error=str(exc))


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
    if isinstance(event, KnowledgeGapEvent):
        return {"type": "knowledge_gap", "topics": list(event.topics)}
    if isinstance(event, ConfidenceEvent):
        return {
            "type": "confidence",
            "level": event.level,
            "score": event.score,
            "chunks_considered": event.chunks_considered,
        }
    if isinstance(event, TokenEvent):
        return {"type": "token", "text": event.text}
    if isinstance(event, FinalEvent):
        # Don't emit `final` directly — the `done` event includes message_id
        # after we've persisted. Suppress here.
        return None
    if isinstance(event, ErrorEvent):
        # Translated into the rich error payload by the streamer; suppress here.
        return None
    return None
