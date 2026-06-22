"""Query history writer + reader (V3 Day 5 #91).

Writes one `query_logs` row per completed chat turn, using the service
client (RLS bypass) so the orchestrator doesn't need to thread the user JWT
deeper. Reads route through the user JWT so RLS enforces the "only your own
rows" guarantee.

Writes are fire-and-forget: `log_query_async` schedules an asyncio task and
returns immediately. A query log insert failure must never affect the
user-visible chat response.
"""
from __future__ import annotations

import asyncio
from typing import Any, Iterable

from app.database import get_service_client, get_user_client
from app.observability import get_logger

log = get_logger(__name__)

_QUERY_TEXT_MAX = 500  # widened from the DB cap (1000) on purpose — writer trims first
_VALID_INTENTS: frozenset[str] = frozenset({
    "factual_qa",
    "task_generation",
    "analysis",
    "search",
    "summarization",
    "comparison",
    "generic",
})


def log_query_async(
    *,
    user_id: str,
    org_id: str,
    query_text: str,
    intent: str | None = None,
    conversation_id: str | None = None,
    message_id: str | None = None,
    response_length: int = 0,
    source_count: int = 0,
    tool_calls: int = 0,
    latency_ms: int | None = None,
    model_used: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_micros: int = 0,
    retrieved_chunk_ids: list[str] | None = None,
) -> asyncio.Task[None]:
    """Schedule a non-blocking insert into `query_logs`.

    Returns the task handle so callers can `await` it in tests, but the
    expectation in production is to drop it on the floor — the chat hot path
    must not wait for the log write.
    """
    coro = _log_query_inner(
        user_id=user_id,
        org_id=org_id,
        query_text=query_text,
        intent=intent,
        conversation_id=conversation_id,
        message_id=message_id,
        response_length=response_length,
        source_count=source_count,
        tool_calls=tool_calls,
        latency_ms=latency_ms,
        model_used=model_used,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_micros=cost_micros,
        retrieved_chunk_ids=retrieved_chunk_ids or [],
    )
    return asyncio.create_task(coro)


async def _log_query_inner(
    *,
    user_id: str,
    org_id: str,
    query_text: str,
    intent: str | None,
    conversation_id: str | None,
    message_id: str | None,
    response_length: int,
    source_count: int,
    tool_calls: int,
    latency_ms: int | None,
    model_used: str | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_micros: int = 0,
    retrieved_chunk_ids: list[str] | None = None,
) -> None:
    # Skip silently if required IDs are missing — logging an anonymous query
    # log row would just be junk data.
    if not user_id or not org_id:
        return

    trimmed = (query_text or "").strip()[:_QUERY_TEXT_MAX]
    if not trimmed:
        return

    # Best-effort intent validation. Unknown intents are dropped to NULL
    # rather than passed through — keeps the WHERE intent=… queries on the
    # /history filters honest. The classifier sometimes emits experimental
    # labels we haven't added to the index yet.
    intent_value = intent if intent in _VALID_INTENTS else None

    # Cap retrieved_chunk_ids at a sane size. The orchestrator already dedupes
    # and caps by chat_max_context_chunks (=20 by default), so this is just a
    # defense-in-depth ceiling against a misbehaving caller passing thousands.
    chunk_ids: list[str] = []
    for cid in (retrieved_chunk_ids or [])[:100]:
        if isinstance(cid, str) and cid:
            chunk_ids.append(cid)

    row = {
        "user_id": user_id,
        "org_id": org_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "query_text": trimmed,
        "intent": intent_value,
        "response_length": max(0, int(response_length or 0)),
        "source_count": max(0, int(source_count or 0)),
        "tool_calls": max(0, int(tool_calls or 0)),
        "latency_ms": max(0, int(latency_ms)) if latency_ms is not None else None,
        "model_used": (model_used or "")[:50] or None,
        "input_tokens": max(0, int(input_tokens or 0)),
        "output_tokens": max(0, int(output_tokens or 0)),
        "cost_micros": max(0, int(cost_micros or 0)),
        "retrieved_chunk_ids": chunk_ids,
    }

    try:
        svc = get_service_client()
        await asyncio.to_thread(
            lambda: svc.table("query_logs").insert(row).execute()
        )
    except Exception as exc:
        # Never raise. Worst case we lose one history row.
        log.warning("query_log_insert_failed", error=str(exc), user_id=user_id)


# ── Reader (called from the /me/query-history router) ───────────────────────


def fetch_query_history(
    *,
    user_jwt: str,
    user_id: str,
    cursor: str | None = None,
    limit: int = 20,
    intent: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    """Cursor-paginated read scoped to the calling user via RLS.

    `cursor` is the ISO-8601 timestamp of the last seen row (returned in the
    previous page's `next_cursor`). Sort is `(created_at DESC, id DESC)` so
    a tied-microsecond pair stays stable across pages.

    This function is synchronous because supabase-py is sync; callers should
    wrap with `asyncio.to_thread`.
    """
    client = get_user_client(user_jwt)
    q = (
        client.table("query_logs")
        .select(
            "id, conversation_id, message_id, query_text, intent, "
            "response_length, source_count, tool_calls, latency_ms, "
            "model_used, created_at",
        )
        # RLS already restricts to the caller's rows; the explicit eq lets
        # PostgREST pick the (user_id, created_at DESC) index.
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .order("id", desc=True)
        .limit(min(limit, 100))
    )

    if intent and intent in _VALID_INTENTS:
        q = q.eq("intent", intent)
    if search:
        # Case-insensitive substring on the query text. The leading-wildcard
        # ILIKE doesn't hit the trgm index (we don't have one on query_logs)
        # but the user_id+created_at predicate already shrinks the scan to a
        # tiny window — fine at our scale.
        safe = search.replace("%", r"\%").replace(",", " ")[:80]
        if safe:
            q = q.ilike("query_text", f"%{safe}%")
    if cursor:
        # `lt` because the index is DESC — newer cursors are larger values.
        q = q.lt("created_at", cursor)

    res = q.execute()
    rows = list(getattr(res, "data", None) or [])

    next_cursor: str | None = None
    if len(rows) >= min(limit, 100):
        last = rows[-1]
        next_cursor = last.get("created_at")

    return {"entries": rows, "next_cursor": next_cursor}


# ── Retention (called from the weekly Inngest cron) ─────────────────────────


def delete_old_query_logs(*, older_than_days: int = 180) -> int:
    """Hard-delete query_logs older than `older_than_days`. Returns count deleted.

    Synchronous (supabase-py is sync). Used by the Inngest weekly cron via
    `asyncio.to_thread`. Service role only — never expose this directly.
    """
    from datetime import datetime, timedelta, timezone

    if older_than_days < 7:
        # Safety floor — anything < 7d is almost certainly a mis-configured cron.
        raise ValueError("Retention must be >= 7 days.")

    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
    svc = get_service_client()
    res = svc.table("query_logs").delete().lt("created_at", cutoff).execute()
    return len(getattr(res, "data", None) or [])
