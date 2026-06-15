from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone

import inngest
from typing import Any, AsyncIterator, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from supabase import Client

from app.auth import verify_jwt
from app.config import get_settings
from app.database import get_user_client
from app.errors import NoOrganization
from app.observability import get_logger
from app.services.llm import Message
from app.services.moderation import (
    ModerationResult,
    log_moderation_event,
    moderate_input,
)
from app.services.rate_limit import enforce_chat_quota
from app.inngest import get_inngest_client
from app.services.citation_tracker import record_citations
from app.services.summarization import load_conversation_summary
from app.services.webhooks import trigger_event as trigger_webhook_event
from app.services.llm.task_chain import (
    ConfidenceEvent,
    ErrorEvent,
    FinalEvent,
    IntentEvent,
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
    # V3 #19 — same locked-at-creation semantics, but the scope is "every doc
    # carrying any of these tags" instead of one specific document. Empty list
    # / omitted = unscoped. Capped to keep the doc-id resolution cheap and the
    # system-prompt note readable.
    scoped_tags: list[str] | None = Field(default=None, max_length=10)
    # V5 #35 — pick a saved Collection by id; the chat router resolves it to
    # its current tag_filters and reuses scoped_tags plumbing. Stored on the
    # conversation row so the chat UI can render "Pinned to <Collection>".
    scoped_collection_id: str | None = Field(default=None, min_length=8, max_length=64)


class UpdateConversationRequest(BaseModel):
    # Both fields are optional so the same PATCH can rename, pin, or do both.
    # Pinning is a single boolean; ordering and "pinned section" rendering
    # are handled on the read path.
    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX_LEN)
    is_pinned: bool | None = None


class RegenerateRequest(BaseModel):
    # Optional refinement steers the regeneration — appended to the original
    # user prompt as a "[Refinement: ...]" note. Capped to keep the prompt
    # reasonable and to discourage rewriting a whole new question via this
    # path (that should be a new user turn).
    refinement: str | None = Field(default=None, min_length=1, max_length=2_000)


class ActiveBranchRequest(BaseModel):
    branch_index: int = Field(..., ge=0, le=20)


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

    # V4 #79 — content moderation runs BEFORE the quota check so a malicious
    # prompt doesn't drain quota by being blocked. Decision is pure-regex,
    # ~sub-ms.
    decision = moderate_input(body.message)
    if decision.result == ModerationResult.BLOCKED:
        await log_moderation_event(
            org_id=org_id, user_id=user_id, query=body.message,
            decision=decision, action_taken="rejected",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "This query was blocked by content moderation. If you believe "
                "this is in error, contact your workspace admin."
            ),
        )
    if decision.result == ModerationResult.FLAGGED:
        await log_moderation_event(
            org_id=org_id, user_id=user_id, query=body.message,
            decision=decision, action_taken="logged_and_proceeded",
        )

    await enforce_chat_quota(user_id=user_id, org_id=org_id)
    client = get_user_client(token)

    conversation_id, scoped_document_id, scoped_tags = await _ensure_conversation(
        client, body.conversation_id, org_id, user_id, body.message,
        scoped_document_id=body.scoped_document_id,
        scoped_tags=body.scoped_tags,
        scoped_collection_id=body.scoped_collection_id,
    )
    history = await _load_history(client, conversation_id)
    summary = await load_conversation_summary(conversation_id, client=client)

    user_message_id = await _save_user_message_idempotent(
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
    final_intent: str = ""
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
        scoped_tags=scoped_tags or None,
        conversation_summary=summary,
    ):
        if isinstance(event, FinalEvent):
            final_text = event.text
            final_sources = event.sources
            tool_calls_made = event.tool_calls_made
            trace_id = event.trace_id
            final_intent = event.intent
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
        parent_user_message_id=user_message_id,
        branch_index=0,
        is_active_branch=True,
        intent=final_intent or None,
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
    await _emit_chat_analytics(
        org_id=org_id,
        user_id=user_id,
        query=body.message,
        intent=final_intent or "",
        source_count=len(final_sources or []),
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

    # V4 #79 — moderation as STEP 0. Blocked queries never start the stream;
    # we raise so the browser sees a 400 JSON, not a half-open SSE channel.
    decision = moderate_input(body.message)
    if decision.result == ModerationResult.BLOCKED:
        await log_moderation_event(
            org_id=org_id, user_id=user_id, query=body.message,
            decision=decision, action_taken="rejected",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "This query was blocked by content moderation. If you believe "
                "this is in error, contact your workspace admin."
            ),
        )
    if decision.result == ModerationResult.FLAGGED:
        await log_moderation_event(
            org_id=org_id, user_id=user_id, query=body.message,
            decision=decision, action_taken="logged_and_proceeded",
        )

    # Rate-limit raises with our envelope — the StreamingResponse never starts
    # so the browser sees a normal JSON error, not a half-open SSE.
    await enforce_chat_quota(user_id=user_id, org_id=org_id)

    client = get_user_client(token)
    request_id = getattr(request.state, "request_id", None)

    conversation_id, scoped_document_id, scoped_tags = await _ensure_conversation(
        client, body.conversation_id, org_id, user_id, body.message,
        scoped_document_id=body.scoped_document_id,
        scoped_tags=body.scoped_tags,
        scoped_collection_id=body.scoped_collection_id,
    )
    history = await _load_history(client, conversation_id)
    summary = await load_conversation_summary(conversation_id, client=client)

    user_message_id = await _save_user_message_idempotent(
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
                    scoped_tags=scoped_tags or None,
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
        final_intent: str = ""
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
                    final_intent = ev.intent
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
                    parent_user_message_id=user_message_id,
                    branch_index=0,
                    is_active_branch=True,
                    intent=final_intent or None,
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
                await _emit_chat_analytics(
                    org_id=org_id,
                    user_id=user_id,
                    query=body.message,
                    intent=final_intent or "",
                    source_count=len(final_sources or []),
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
        # Pinned conversations come first regardless of recency, then the
        # rest by updated_at DESC. Migration 019 adds an index that matches
        # this exact ORDER BY so Postgres can serve it without a sort.
        result = await asyncio.to_thread(
            lambda: client.table("conversations")
            .select("id, title, is_pinned, created_at, updated_at")
            .order("is_pinned", desc=True)
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
    # cares about order, not numeric weights. The pin flag rides along so
    # the sidebar can render the icon on each row. Search results pre-RPC
    # update may not carry the field; default to False for compatibility.
    convos = [
        {
            "id": r["id"],
            "title": r["title"],
            "is_pinned": bool(r.get("is_pinned", False)),
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
        .select(
            "id, title, is_pinned, created_at, updated_at, "
            "scoped_document_id, scoped_tags, scoped_collection_id"
        )
        .eq("id", conversation_id)
        .maybe_single()
        .execute()
    )
    if not convo.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")

    # Load every assistant branch + every user row. Two reasons we don't
    # filter to is_active_branch=true at the DB:
    #   * The UI's branch navigator needs to know `total_branches` per group
    #     to render "1/3" etc — we'd have to count separately otherwise.
    #   * Flipping branches is a client-only operation once the rows are in
    #     memory; round-tripping for each switch would feel laggy.
    # We do filter in the response so the timeline only includes the active
    # branch per group; the inactive ones ride along under a separate key.
    msgs_result = await asyncio.to_thread(
        lambda: client.table("messages")
        .select(
            "id, role, content, sources, feedback, metadata, created_at, "
            "parent_user_message_id, branch_index, is_active_branch"
        )
        .eq("conversation_id", conversation_id)
        .order("created_at")
        .execute()
    )
    all_rows: list[dict] = msgs_result.data or []

    # Group assistant rows by their parent_user_message_id so we can emit:
    #   * branches[parent_user_id] = [ {id, branch_index, ...}, ... ]
    #   * timeline = user rows + the active branch from each group
    branches_by_parent: dict[str, list[dict]] = {}
    user_rows: list[dict] = []
    active_by_parent: dict[str, dict] = {}
    orphan_assistants: list[dict] = []

    for row in all_rows:
        role = row.get("role")
        if role == "user":
            user_rows.append(row)
            continue
        if role != "assistant":
            continue
        parent_id = row.get("parent_user_message_id")
        if not parent_id:
            # Pre-migration assistant rows that didn't get backfilled — keep
            # them in the timeline as a stand-alone bubble so legacy chats
            # don't lose history.
            orphan_assistants.append(row)
            continue
        branches_by_parent.setdefault(parent_id, []).append(row)
        if row.get("is_active_branch"):
            active_by_parent[parent_id] = row

    # Build the linear timeline. We rely on created_at order to interleave;
    # for each user message we emit the active assistant branch (if any)
    # immediately after.
    timeline: list[dict] = []
    for u in user_rows:
        timeline.append(u)
        active = active_by_parent.get(u["id"])
        if active is not None:
            total_branches = len(branches_by_parent.get(u["id"], []))
            active = {
                **active,
                "total_branches": total_branches,
            }
            timeline.append(active)

    # Anything we missed (orphans, or assistant rows whose user row didn't
    # come back due to RLS) goes at the end so the chat doesn't look broken.
    if orphan_assistants:
        # Insert orphans by created_at so they slot near where they belong.
        timeline.extend({**r, "total_branches": 1} for r in orphan_assistants)
        timeline.sort(key=lambda r: r.get("created_at") or "")

    return {
        "conversation": convo.data,
        "messages": timeline,
        # Expose all branches keyed by parent so the UI can switch without
        # an extra round trip. The default render uses the active one.
        "branches": {
            parent_id: [
                {
                    "id": b["id"],
                    "branch_index": b.get("branch_index", 0),
                    "is_active_branch": b.get("is_active_branch", False),
                    "content": b.get("content", ""),
                    "sources": b.get("sources"),
                    "feedback": b.get("feedback"),
                    "metadata": b.get("metadata"),
                    "created_at": b.get("created_at"),
                }
                for b in sorted(rows, key=lambda r: r.get("branch_index", 0))
            ]
            for parent_id, rows in branches_by_parent.items()
            if len(rows) > 1  # only emit when there's something to switch to
        },
    }


@router.patch("/conversations/{conversation_id}")
async def update_conversation(
    conversation_id: str,
    body: UpdateConversationRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Rename and/or pin a conversation. Either field is optional; supplying
    neither is a 400. The pinned flag drives sidebar ordering via the
    `(user_id, is_pinned DESC, updated_at DESC)` index added in migration 019.
    """
    _, _, token = _require_org(current_user)
    client = get_user_client(token)

    payload: dict[str, Any] = {}
    if body.title is not None:
        cleaned = " ".join(body.title.split())[:TITLE_MAX_LEN].strip()
        if not cleaned:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Title cannot be empty.",
            )
        payload["title"] = cleaned
    if body.is_pinned is not None:
        payload["is_pinned"] = body.is_pinned

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nothing to update.",
        )

    result = await asyncio.to_thread(
        lambda: client.table("conversations")
        .update(payload)
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

    # V4 #18: analytics event for the admin dashboard feedback ratio. Only log
    # actual ratings (not clears) — a "took back my thumb" event isn't useful.
    if body.feedback is not None:
        from app.services.analytics import track_event

        await track_event(
            org_id=current_user.get("org_id"),
            user_id=current_user.get("user_id"),
            event_type="feedback_given",
            metadata={"feedback": body.feedback},
        )

    # Agent Day 15: fire the output-improvement loop on negative feedback.
    # Idempotent on (message_id) — re-tapping thumbs-down won't re-analyse.
    if body.feedback == "negative" and current_user.get("org_id"):
        try:
            import inngest as _inngest
            from app.inngest.client import get_inngest_client as _client

            await _client().send(
                _inngest.Event(
                    name="message/feedback-negative",
                    data={
                        "message_id": message_id,
                        "org_id": current_user["org_id"],
                    },
                    id=f"feedback-negative-{message_id}",
                )
            )
        except Exception as exc:
            log.warning(
                "feedback_negative_event_failed msg=%s err=%s",
                message_id,
                exc,
            )

    return {"feedback": body.feedback}


# ── V5 #59 — Copy = used (implicit quality signal) ──────────────────────────

@router.post("/messages/{message_id}/copied")
async def record_message_copy(
    message_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Fire-and-forget signal from the assistant-message Copy button.

    Each click increments `messages.copy_count` and forwards an `output_used`
    score onto the Langfuse trace so we can slice retrieval quality by what
    users actually used. RLS keeps cross-org calls from working; the user
    sees a 404 (instead of 403) on those because that's what their session
    is allowed to know.

    The endpoint is intentionally idempotent-feeling but additive — repeated
    copies of the same message are still useful signal (very high counts
    correlate with templates / boilerplate the user is iterating on).
    """
    if not _is_uuid(message_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid message id.")

    org_id, _user_id, token = _require_org(current_user)
    client = get_user_client(token)

    msg_res = await asyncio.to_thread(
        lambda: client.table("messages")
        .select("id, role, copy_count, langfuse_trace_id")
        .eq("id", message_id)
        .maybe_single()
        .execute()
    )
    if not msg_res or not msg_res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found.")
    msg = msg_res.data
    if msg.get("role") != "assistant":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only assistant messages can be marked as copied.",
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    next_count = int(msg.get("copy_count") or 0) + 1
    patch: dict[str, Any] = {
        "copy_count": next_count,
        "last_copied_at": now_iso,
    }
    # Set first_copied_at only on the first hit — the column has no UPSERT
    # semantics from Postgres' perspective so we conditionally include it.
    if next_count == 1:
        patch["first_copied_at"] = now_iso

    await asyncio.to_thread(
        lambda: client.table("messages")
        .update(patch)
        .eq("id", message_id)
        .execute()
    )

    # Forward to Langfuse. Score is 1 — the trace's name `output_used` is
    # what matters; a multi-copy session shows up as multiple score events.
    trace_id = msg.get("langfuse_trace_id")
    if trace_id:
        try:
            from app.services.langfuse import langfuse_client

            if langfuse_client is not None:
                langfuse_client.create_score(
                    trace_id=trace_id,
                    name="output_used",
                    value=1,
                    comment="User copied the assistant output",
                    data_type="NUMERIC",
                )
        except Exception as exc:
            log.debug("langfuse_copy_score_failed", error=str(exc))

    return {"copy_count": next_count, "org_id": org_id}


# ── Branching (V3 Day 3 #42) ────────────────────────────────────────────────

@router.post("/messages/{message_id}/regenerate")
async def regenerate_message(
    message_id: str,
    body: RegenerateRequest,
    request: Request,
    current_user: dict = Depends(verify_jwt),
) -> StreamingResponse:
    """Stream a new assistant branch in place of the given assistant message.

    Semantics:
      * The new branch shares `parent_user_message_id` with all siblings of
        the message being regenerated. branch_index is `max(existing)+1`.
      * On success, all sibling branches of this parent are flipped to
        `is_active_branch=false`; the new row is the only active one.
      * Failures leave the existing branch active — we never clear the old
        active without a successful replacement.
      * Optional `refinement` rides inside the user prompt as an inline
        "[Refinement: ...]" suffix so the LLM treats it as steering, not as
        a wholesale new question.

    SSE shape mirrors POST /chat/stream so the frontend's stream consumer
    is shared between the two flows.
    """
    if not _is_uuid(message_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid message id.")

    org_id, user_id, token = _require_org(current_user)
    await enforce_chat_quota(user_id=user_id, org_id=org_id)
    client = get_user_client(token)
    request_id = getattr(request.state, "request_id", None)

    original = await asyncio.to_thread(
        lambda: client.table("messages")
        .select(
            "id, role, conversation_id, parent_user_message_id, branch_index"
        )
        .eq("id", message_id)
        .maybe_single()
        .execute()
    )
    if not original or not original.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found.")
    if original.data.get("role") != "assistant":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only assistant messages can be regenerated.",
        )

    conversation_id = original.data["conversation_id"]
    parent_user_message_id = original.data.get("parent_user_message_id")
    if not parent_user_message_id:
        # Pre-migration assistant message — figure out which user message
        # produced it by walking back to the previous user row in time.
        parent_user_message_id = await _find_preceding_user_message(
            client, conversation_id=conversation_id, before=message_id
        )
        if not parent_user_message_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot regenerate — no prior user message found.",
            )

    user_msg = await asyncio.to_thread(
        lambda: client.table("messages")
        .select("content")
        .eq("id", parent_user_message_id)
        .maybe_single()
        .execute()
    )
    if not user_msg or not user_msg.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Original prompt missing.")

    base_prompt = (user_msg.data.get("content") or "").strip()
    refinement = (body.refinement or "").strip()
    effective_prompt = (
        f"{base_prompt}\n\n[Refinement: {refinement}]" if refinement else base_prompt
    )
    if not effective_prompt:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty prompt.")

    siblings = await asyncio.to_thread(
        lambda: client.table("messages")
        .select("branch_index")
        .eq("parent_user_message_id", parent_user_message_id)
        .execute()
    )
    sibling_rows = siblings.data or []
    next_branch_index = max((r.get("branch_index", 0) for r in sibling_rows), default=-1) + 1

    # Load conversation context — same shape as POST /chat/stream, but bounded
    # to the *active branch only* so we don't feed the LLM contradictory
    # alternate replies.
    history = await _load_history_active_branch(client, conversation_id)
    summary = await load_conversation_summary(conversation_id, client=client)

    async def event_stream() -> AsyncIterator[bytes]:
        yield _sse(
            {
                "type": "start",
                "conversation_id": conversation_id,
                "parent_user_message_id": parent_user_message_id,
                "branch_index": next_branch_index,
            }
        )

        events_queue: asyncio.Queue[OrchestratorEvent | None] = asyncio.Queue()

        async def producer() -> None:
            try:
                async for ev in execute_task(
                    user_message=effective_prompt,
                    org_id=org_id,
                    db_client=client,
                    history=history,
                    stream=True,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    conversation_summary=summary,
                ):
                    await events_queue.put(ev)
            finally:
                await events_queue.put(None)

        final_text = ""
        final_sources: list[dict] = []
        tool_calls_made = 0
        trace_id: str | None = None
        confidence_evt: ConfidenceEvent | None = None
        final_intent: str = ""
        had_error = False
        error_payload: dict | None = None

        producer_task = asyncio.create_task(producer())
        last_emit = time.monotonic()

        try:
            while True:
                try:
                    timeout = max(0.1, SSE_HEARTBEAT_SECONDS - (time.monotonic() - last_emit))
                    ev = await asyncio.wait_for(events_queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    yield b": keepalive\n\n"
                    last_emit = time.monotonic()
                    continue

                if ev is None:
                    break

                payload = _event_to_payload(ev)
                if payload is not None:
                    yield _sse(payload)
                    last_emit = time.monotonic()

                if isinstance(ev, FinalEvent):
                    final_text = ev.text
                    final_sources = ev.sources
                    tool_calls_made = ev.tool_calls_made
                    trace_id = ev.trace_id
                    final_intent = ev.intent
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
            log.exception("regenerate_stream_unhandled", error=str(exc))
            yield _sse(
                {
                    "type": "error",
                    "code": "internal_error",
                    "message": "Something broke while regenerating. Try again.",
                    "request_id": request_id,
                }
            )
            producer_task.cancel()
            return
        finally:
            if not producer_task.done():
                producer_task.cancel()

        if had_error and error_payload is not None:
            yield _sse(error_payload)
            return

        if not final_text:
            yield _sse(
                {
                    "type": "error",
                    "code": "upstream_unavailable",
                    "message": "Empty regeneration. The old answer is unchanged.",
                    "request_id": request_id,
                }
            )
            return

        try:
            # Two-step branch flip: deactivate siblings, then insert active.
            # The partial unique index uniq_messages_active_branch_per_group
            # would 23505 if we tried to insert active while another active
            # row exists, so the order matters.
            await asyncio.to_thread(
                lambda: client.table("messages")
                .update({"is_active_branch": False})
                .eq("parent_user_message_id", parent_user_message_id)
                .execute()
            )
            new_id = await _save_message(
                client,
                conversation_id=conversation_id,
                org_id=org_id,
                role="assistant",
                content=final_text,
                sources=final_sources,
                trace_id=trace_id,
                confidence=confidence_evt,
                parent_user_message_id=parent_user_message_id,
                branch_index=next_branch_index,
                is_active_branch=True,
                intent=final_intent,
            )
            await _touch_conversation(client, conversation_id)
            await _fire_summarize_event(conversation_id, org_id)
            await record_citations(
                sources=final_sources,
                message_id=new_id,
                conversation_id=conversation_id,
                org_id=org_id,
            )
            yield _sse(
                {
                    "type": "done",
                    "message_id": new_id,
                    "parent_user_message_id": parent_user_message_id,
                    "branch_index": next_branch_index,
                    "total_branches": len(sibling_rows) + 1,
                    "tool_calls": tool_calls_made,
                }
            )
        except Exception as exc:
            log.exception("regen_save_failed", error=str(exc))
            yield _sse(
                {
                    "type": "error",
                    "code": "internal_error",
                    "message": "Regenerated, but failed to save. The old answer is unchanged.",
                    "request_id": request_id,
                }
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.patch("/messages/{message_id}/active-branch")
async def set_active_branch(
    message_id: str,
    body: ActiveBranchRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Flip which branch in the regen group is rendered as the canonical answer.

    `message_id` identifies any one of the siblings; we look up the group via
    `parent_user_message_id` and atomically activate the row at `branch_index`,
    deactivating all others. RLS scopes both updates to the caller's org.
    """
    if not _is_uuid(message_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid message id.")

    _, _, token = _require_org(current_user)
    client = get_user_client(token)

    anchor = await asyncio.to_thread(
        lambda: client.table("messages")
        .select("parent_user_message_id, conversation_id, role")
        .eq("id", message_id)
        .maybe_single()
        .execute()
    )
    if not anchor or not anchor.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found.")
    if anchor.data.get("role") != "assistant":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not an assistant message.")
    parent_id = anchor.data.get("parent_user_message_id")
    if not parent_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This message has no regeneration history.",
        )

    target = await asyncio.to_thread(
        lambda: client.table("messages")
        .select("id")
        .eq("parent_user_message_id", parent_id)
        .eq("branch_index", body.branch_index)
        .maybe_single()
        .execute()
    )
    if not target or not target.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Branch not found.")

    await asyncio.to_thread(
        lambda: client.table("messages")
        .update({"is_active_branch": False})
        .eq("parent_user_message_id", parent_id)
        .execute()
    )
    result = await asyncio.to_thread(
        lambda: client.table("messages")
        .update({"is_active_branch": True})
        .eq("id", target.data["id"])
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Branch flip failed.")
    return {
        "active_message_id": target.data["id"],
        "branch_index": body.branch_index,
    }


# ── Export (V3 Day 4 #25) ────────────────────────────────────────────────────

@router.get("/conversations/{conversation_id}/export")
async def export_conversation(
    conversation_id: str,
    format: str = Query("markdown", pattern="^(markdown|json)$"),
    current_user: dict = Depends(verify_jwt),
) -> Response:
    """Export a conversation as markdown (download) or JSON (raw).

    Markdown is what users actually want — it pastes cleanly into Notion,
    Google Docs, Slack, and email clients. JSON is offered for power users
    and integrations. Only the *active* branch per regen group is included
    so the export reflects what the user saw on screen.
    """
    _, _, token = _require_org(current_user)
    client = get_user_client(token)

    convo = await asyncio.to_thread(
        lambda: client.table("conversations")
        .select("id, title, created_at, updated_at")
        .eq("id", conversation_id)
        .maybe_single()
        .execute()
    )
    if not convo or not convo.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")

    msgs = await asyncio.to_thread(
        lambda: client.table("messages")
        .select("role, content, sources, created_at, is_active_branch, parent_user_message_id")
        .eq("conversation_id", conversation_id)
        .order("created_at")
        .execute()
    )
    rows = msgs.data or []
    # User rows have no branching; assistant rows must be active. Also include
    # legacy assistant rows that pre-date branching (no parent_user_message_id).
    timeline = [
        r for r in rows
        if r.get("role") == "user"
        or r.get("is_active_branch")
        or r.get("parent_user_message_id") is None
    ]

    title = (convo.data.get("title") or "Conversation").strip()
    slug = _slugify(title) or "conversation"

    if format == "json":
        return Response(
            content=json.dumps(
                {
                    "conversation": convo.data,
                    "messages": [
                        {
                            "role": m.get("role"),
                            "content": m.get("content"),
                            "sources": m.get("sources"),
                            "created_at": m.get("created_at"),
                        }
                        for m in timeline
                    ],
                },
                separators=(",", ":"),
            ),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{slug}.json"',
            },
        )

    lines: list[str] = [
        f"# {title}",
        "",
        f"*Exported from Company Brain — {_format_export_ts()}*",
        "",
        "---",
        "",
    ]
    for m in timeline:
        role = "**You**" if m.get("role") == "user" else "**Company Brain**"
        lines.append(role)
        lines.append("")
        lines.append((m.get("content") or "").rstrip())
        sources = m.get("sources") or []
        if sources:
            names = ", ".join(
                str(s.get("document_name") or s.get("name") or "Untitled")
                for s in sources[:5]
            )
            lines.append("")
            lines.append(f"*Sources: {names}*")
        lines.append("")
        lines.append("---")
        lines.append("")

    return Response(
        content="\n".join(lines),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{slug}.md"',
        },
    )


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
    scoped_tags: list[str] | None = None,
    scoped_collection_id: str | None = None,
) -> tuple[str, str | None, list[str]]:
    """Look up or create the conversation; return (id, scoped_document_id, scoped_tags).

    Scope is only honored at *creation* — we ignore an attempt to retro-scope
    an existing conversation because the LLM may have already produced
    answers under the assumption that all docs were available.

    `scoped_tags` is a sibling of `scoped_document_id`. The two are not
    mutually exclusive at the schema level, but the chat UI never sets both
    at once — doc-pinning is a per-document affordance, tag-pinning is a
    multi-tag affordance from the new-chat surface.

    V5 #35: when `scoped_collection_id` is supplied, we resolve it to its
    current tag_filters and treat the resolved tags as scoped_tags. We also
    persist the collection id alongside so the UI can render the collection
    label on subsequent loads. If a collection is later deleted, the tag
    snapshot on the conversation row keeps working.
    """
    if conversation_id:
        check = await asyncio.to_thread(
            lambda: client.table("conversations")
            .select("id, scoped_document_id, scoped_tags")
            .eq("id", conversation_id)
            .maybe_single()
            .execute()
        )
        if not check.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
        return (
            conversation_id,
            check.data.get("scoped_document_id"),
            list(check.data.get("scoped_tags") or []),
        )

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

    # Resolve collection → tag snapshot. If the caller passed both an explicit
    # `scoped_tags` AND a `scoped_collection_id`, the collection wins — the UI
    # only ever sets one of the two and we'd rather trust the saved view than
    # a stale tag list cached on the client.
    persisted_collection_id: str | None = None
    collection_tags: list[str] = []
    if scoped_collection_id:
        if not _is_uuid(scoped_collection_id):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid collection id.")
        # Lazy import to keep chat.py's cold-start light.
        from app.routers.collections import resolve_collection_tags

        persisted_collection_id, collection_tags = await resolve_collection_tags(
            client=client, org_id=org_id, collection_id=scoped_collection_id,
        )

    # Normalize tag scope at the API boundary so what we persist matches what
    # `documents.tags` stores — saves us a normalization step inside the
    # retrieval path on every search call.
    tag_source = collection_tags if collection_tags else (scoped_tags or [])
    persisted_tags: list[str] = []
    seen: set[str] = set()
    for raw in tag_source:
        cleaned = (raw or "").strip().lower()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        persisted_tags.append(cleaned)

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
    if persisted_tags:
        row["scoped_tags"] = persisted_tags
    if persisted_collection_id:
        row["scoped_collection_id"] = persisted_collection_id
    await asyncio.to_thread(
        lambda: client.table("conversations").insert(row).execute()
    )
    return new_id, persisted_scope, persisted_tags


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
    parent_user_message_id: str | None = None,
    branch_index: int = 0,
    is_active_branch: bool = True,
    intent: str | None = None,
) -> str:
    new_id = str(uuid.uuid4())
    metadata: dict[str, Any] = {}
    if confidence is not None:
        metadata["confidence"] = {
            "level": confidence.level,
            "score": confidence.score,
            "n": confidence.chunks_considered,
        }
    if intent:
        metadata["intent"] = intent
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
    if parent_user_message_id:
        row["parent_user_message_id"] = parent_user_message_id
        row["branch_index"] = branch_index
        row["is_active_branch"] = is_active_branch
    # V5 #73 — stamp the per-turn time-saved estimate on the assistant row
    # only. User rows always stay at 0 so we can SUM the column for a clean
    # "minutes saved" total without double-counting either side of the turn.
    if role == "assistant":
        from app.services.time_savings import estimate_minutes

        row["time_saved_minutes"] = estimate_minutes(
            intent=intent, response_length=len(content or ""),
        )
    await asyncio.to_thread(
        lambda: client.table("messages").insert(row).execute()
    )
    return new_id


async def _emit_chat_analytics(
    *,
    org_id: str,
    user_id: str,
    query: str,
    intent: str,
    source_count: int,
) -> None:
    """Fire analytics + activity for a completed turn. Never raises.

    Separated from the chat endpoints so both /chat and /chat/stream stay
    readable; both call this after a successful save. We don't gate on intent
    here — the activity feed surfaces task_generation as "an output", everything
    else as the generic intent label. Privacy is resolved per-call so a user
    flipping the toggle takes effect on their NEXT turn, not retroactively.
    """
    try:
        from app.services.analytics import track_event
        from app.services.activity import (
            INTENT_FEED_LABEL,
            log_activity,
            resolve_user_privacy,
        )

        await track_event(
            org_id=org_id,
            user_id=user_id,
            event_type="chat_sent",
            metadata={
                "intent": intent or "unknown",
                "query_len": len(query),
                "source_count": source_count,
            },
        )

        if intent == "task_generation":
            is_private = await resolve_user_privacy(user_id)
            await log_activity(
                org_id=org_id,
                user_id=user_id,
                activity_type="generated_content",
                metadata={"intent_label": INTENT_FEED_LABEL.get(intent, "content")},
                is_private=is_private,
            )
    except Exception as exc:
        log.warning("chat_analytics_emit_failed", error=str(exc))


async def _load_history_active_branch(
    client: Client, conversation_id: str
) -> list[Message]:
    """Same shape as `_load_history`, but ignores inactive assistant branches.

    Used by the regenerate stream so the LLM never sees both an older answer
    and the one it's about to replace — otherwise it might "double down" on
    the answer the user is actively rejecting.
    """
    settings = get_settings()
    n = settings.chat_history_turns
    result = await asyncio.to_thread(
        lambda: client.table("messages")
        .select("role, content, is_active_branch, parent_user_message_id")
        .eq("conversation_id", conversation_id)
        .order("created_at", desc=True)
        .limit(n * 2)  # over-fetch — we filter, then trim to n
        .execute()
    )
    rows = list(reversed(result.data or []))
    out: list[Message] = []
    for row in rows:
        role = row["role"]
        if role not in ("user", "assistant"):
            continue
        if role == "assistant":
            # Skip non-active branches AND skip legacy rows with no parent
            # only when they explicitly carry is_active_branch=false (which
            # backfill would have set true). Concretely: only emit assistant
            # rows the user is currently looking at.
            if not row.get("is_active_branch", True):
                continue
        content = row.get("content") or ""
        if not content:
            continue
        out.append(Message(role=role, content=content))
    return out[-n:]


async def _find_preceding_user_message(
    client: Client, *, conversation_id: str, before: str
) -> str | None:
    """For pre-branching assistant rows: find the user msg that produced them."""
    anchor = await asyncio.to_thread(
        lambda: client.table("messages")
        .select("created_at")
        .eq("id", before)
        .maybe_single()
        .execute()
    )
    if not anchor or not anchor.data:
        return None
    prior = await asyncio.to_thread(
        lambda: client.table("messages")
        .select("id")
        .eq("conversation_id", conversation_id)
        .eq("role", "user")
        .lt("created_at", anchor.data["created_at"])
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = prior.data or []
    return rows[0]["id"] if rows else None


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return cleaned[:60] or "conversation"


def _format_export_ts() -> str:
    return datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")


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
    if isinstance(event, IntentEvent):
        return {"type": "intent", "intent": event.intent}
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
