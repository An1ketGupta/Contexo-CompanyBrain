"""Tool-use orchestrator — the LLM brain of Company Brain.

Single entry point: `execute_task(...)`. The orchestrator wraps the
LLM↔retrieval loop and produces a stream of typed events that both the
non-streaming `/chat` endpoint and the streaming `/chat/stream` endpoint
consume. Driving both surfaces from one generator keeps their behavior
identical (no skew between what gets persisted and what the UI sees).

Loop sketch:
    user msg → LLM
        if tool_calls → run all hybrid_searches in parallel → feed back → loop
        if text       → done (emit `final`)

Guardrails (all from config, with sane defaults):
    * `chat_max_tool_rounds`   — caps recursion in case the model loops
    * `chat_max_searches`      — caps total Gemini-billed embeddings/searches
    * `chat_max_context_chunks`— caps how many sources we surface upward
    * `chat_max_message_chars` — input length guard (defense-in-depth)
    * 30s timeout per LLM call (inside `LLMClient`)
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal

from supabase import Client

from app.config import get_settings
from app.services.retrieval import SearchHit, hybrid_search

from .client import SEARCH_TOOL, SEARCH_TOOL_NAME, LLMClient, LLMError, get_llm_client
from .types import LLMResponse, Message, ToolCall, ToolResult

log = logging.getLogger(__name__)


# ── Event types yielded by the orchestrator ──────────────────────────────────

@dataclass(frozen=True)
class SearchingEvent:
    """Emitted just before a tool call is executed (UI shows progress)."""

    kind: Literal["searching"] = field(default="searching", init=False)
    query: str = ""


@dataclass(frozen=True)
class SearchedEvent:
    """Emitted after a tool call completes, with hit count."""

    kind: Literal["searched"] = field(default="searched", init=False)
    query: str = ""
    hit_count: int = 0


@dataclass(frozen=True)
class SourcesEvent:
    """Emitted once all retrieval is done — full citations payload."""

    kind: Literal["sources"] = field(default="sources", init=False)
    sources: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class TokenEvent:
    """Streaming token (only emitted by streaming orchestrator path)."""

    kind: Literal["token"] = field(default="token", init=False)
    text: str = ""


@dataclass(frozen=True)
class FinalEvent:
    """The full assistant message text, emitted at the end of either path."""

    kind: Literal["final"] = field(default="final", init=False)
    text: str = ""
    sources: list[dict] = field(default_factory=list)
    tool_calls_made: int = 0


@dataclass(frozen=True)
class ErrorEvent:
    """Surfaced for retrieval / LLM failures the orchestrator can't recover from."""

    kind: Literal["error"] = field(default="error", init=False)
    message: str = ""


OrchestratorEvent = (
    SearchingEvent | SearchedEvent | SourcesEvent | TokenEvent | FinalEvent | ErrorEvent
)


# ── Public API ───────────────────────────────────────────────────────────────

async def execute_task(
    *,
    user_message: str,
    org_id: str,
    db_client: Client,
    history: list[Message] | None = None,
    llm_client: LLMClient | None = None,
    stream: bool = False,
) -> AsyncIterator[OrchestratorEvent]:
    """Run one user task through the LLM+retrieval loop.

    Yields events in order: searching/searched* → sources → (token*) → final.
    On hard failure, yields a single ErrorEvent and stops.
    """
    settings = get_settings()
    llm = llm_client or get_llm_client()

    user_message = (user_message or "").strip()
    if not user_message:
        yield ErrorEvent(message="Message is empty.")
        return
    if len(user_message) > settings.chat_max_message_chars:
        yield ErrorEvent(
            message=f"Message too long (>{settings.chat_max_message_chars} chars). Please shorten."
        )
        return

    messages: list[Message] = list(history or [])
    messages.append(Message(role="user", content=user_message))

    all_hits: list[SearchHit] = []
    seen_queries: set[str] = set()
    searches_done = 0
    tool_call_total = 0

    started = time.perf_counter()
    final_text = ""

    for round_idx in range(settings.chat_max_tool_rounds + 1):
        # Last round: force no more tools, just final text.
        tools_for_this_round = (SEARCH_TOOL,) if round_idx < settings.chat_max_tool_rounds else ()

        try:
            response: LLMResponse = await llm.complete(
                messages,
                tools=tools_for_this_round,
            )
        except LLMError as exc:
            log.exception("LLM call failed in round %d: %s", round_idx, exc)
            yield ErrorEvent(message=f"AI service error: {exc}")
            return

        if not response.tool_calls:
            # In stream mode we deliberately keep final_text empty so the
            # streaming branch below kicks in (one extra LLM call, but the
            # user gets real token-by-token output). In non-stream mode we
            # take this text as the answer.
            if not stream:
                final_text = response.text.strip()
            break

        # Append the assistant turn containing the tool calls.
        messages.append(
            Message(role="assistant", content=response.text, tool_calls=response.tool_calls)
        )

        # Filter out unknown / duplicate / over-budget calls.
        valid_calls: list[ToolCall] = []
        for tc in response.tool_calls:
            if searches_done + len(valid_calls) >= settings.chat_max_searches:
                break
            if tc.name != SEARCH_TOOL_NAME:
                log.warning("Unknown tool from LLM: %r — skipping", tc.name)
                continue
            query = _extract_query(tc)
            if not query:
                continue
            key = query.lower()
            if key in seen_queries:
                continue
            seen_queries.add(key)
            valid_calls.append(tc)

        if not valid_calls:
            # Model insisted on tools but every call was rejected — stop gracefully.
            log.info("No valid tool calls this round; ending loop with last text.")
            if not stream:
                final_text = response.text.strip()
            break

        # Execute all valid searches in parallel.
        for tc in valid_calls:
            yield SearchingEvent(query=tc.args["query"])

        results: list[list[SearchHit] | BaseException] = await asyncio.gather(
            *[
                _run_search(tc.args["query"], org_id, db_client, settings.chat_search_k)
                for tc in valid_calls
            ],
            return_exceptions=True,
        )

        tool_call_total += len(valid_calls)
        searches_done += len(valid_calls)

        # Feed results back to the LLM as tool messages.
        for tc, res in zip(valid_calls, results):
            if isinstance(res, BaseException):
                log.warning("hybrid_search failed for %r: %s", tc.args["query"], res)
                hits: list[SearchHit] = []
            else:
                hits = res
            all_hits.extend(hits)
            yield SearchedEvent(query=tc.args["query"], hit_count=len(hits))
            messages.append(
                Message(
                    role="tool",
                    tool_result=ToolResult(
                        call_id=tc.id,
                        name=tc.name,
                        content=_format_context(hits),
                    ),
                )
            )

    # End of tool loop — emit sources first, then either final text (non-stream)
    # or stream the final generation if requested.
    sources = _dedupe_sources(all_hits, limit=settings.chat_max_context_chunks)
    yield SourcesEvent(sources=sources)

    if stream and not final_text:
        # We may have exited the loop because the LLM was about to speak. To
        # stream the final answer, re-issue the last LLM call WITHOUT tools
        # and consume it as a stream. (Gemini won't stream a turn that
        # produced tool_calls; calling again with the now-complete tool-result
        # history gives us a fresh streamable final turn.)
        full_text_parts: list[str] = []
        try:
            chunk_iter = await llm.stream(messages, tools=())
            async for chunk in chunk_iter:
                if chunk.kind == "text" and chunk.text:
                    full_text_parts.append(chunk.text)
                    yield TokenEvent(text=chunk.text)
                elif chunk.kind == "error":
                    yield ErrorEvent(message=f"Stream error: {chunk.error}")
                    return
                elif chunk.kind == "done":
                    break
        except LLMError as exc:
            yield ErrorEvent(message=f"AI service error: {exc}")
            return
        final_text = "".join(full_text_parts).strip()

    elapsed = time.perf_counter() - started
    log.info(
        "execute_task complete: rounds=%d searches=%d hits=%d elapsed=%.2fs",
        round_idx + 1,
        searches_done,
        len(all_hits),
        elapsed,
    )

    yield FinalEvent(text=final_text, sources=sources, tool_calls_made=tool_call_total)


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _run_search(query: str, org_id: str, client: Client, k: int) -> list[SearchHit]:
    return await hybrid_search(query, org_id, client, k=k)


def _extract_query(tc: ToolCall) -> str:
    raw = tc.args.get("query", "") if isinstance(tc.args, dict) else ""
    if not isinstance(raw, str):
        return ""
    return raw.strip()


def _format_context(hits: list[SearchHit]) -> str:
    """Render search hits into a flat text block for the LLM.

    We tag each chunk with a `[SOURCE: <doc>, p.<n>]` header so the LLM can
    name its sources naturally in prose if it wants — but we never depend on
    that for citations (those come from `SourcesEvent`).
    """
    if not hits:
        return "(no relevant chunks found)"
    parts: list[str] = []
    for h in hits:
        source = h.document_name
        if h.page_number is not None:
            source += f", p.{h.page_number}"
        if h.section_heading:
            source += f"  §{h.section_heading}"
        parts.append(f"[SOURCE: {source}]\n{h.content.strip()}")
    return "\n\n---\n\n".join(parts)


def _dedupe_sources(hits: list[SearchHit], *, limit: int) -> list[dict]:
    """Dedupe across all searches by chunk_id, keep best-ranked occurrence.

    Returns a UI-friendly shape: `{chunk_id, document_id, document_name,
    page_number, section_heading, excerpt, snippet}`. Caller (DB layer) decides
    what to persist in `messages.sources`.
    """
    by_chunk: dict[str, tuple[int, SearchHit]] = {}
    for i, h in enumerate(hits):
        existing = by_chunk.get(h.chunk_id)
        if existing is None or i < existing[0]:
            by_chunk[h.chunk_id] = (i, h)

    ordered = sorted(by_chunk.values(), key=lambda pair: pair[0])
    out: list[dict] = []
    for _, h in ordered[:limit]:
        out.append(
            {
                "chunk_id": h.chunk_id,
                "document_id": h.document_id,
                "document_name": h.document_name,
                "page_number": h.page_number,
                "section_heading": h.section_heading,
                "excerpt": (h.content or "").strip()[:280],
                "snippet": h.snippet,
            }
        )
    return out


# ── Convenience: collect events into a single response (non-streaming path) ─

@dataclass
class TaskResult:
    text: str
    sources: list[dict]
    tool_calls_made: int
    error: str | None = None


async def execute_task_blocking(
    *,
    user_message: str,
    org_id: str,
    db_client: Client,
    history: list[Message] | None = None,
    llm_client: LLMClient | None = None,
) -> TaskResult:
    text = ""
    sources: list[dict] = []
    tool_calls_made = 0
    error: str | None = None
    async for event in execute_task(
        user_message=user_message,
        org_id=org_id,
        db_client=db_client,
        history=history,
        llm_client=llm_client,
        stream=False,
    ):
        if isinstance(event, FinalEvent):
            text = event.text
            sources = event.sources
            tool_calls_made = event.tool_calls_made
        elif isinstance(event, ErrorEvent):
            error = event.message
    return TaskResult(text=text, sources=sources, tool_calls_made=tool_calls_made, error=error)
