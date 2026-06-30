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
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal

from supabase import Client

from app.config import get_settings
from app.services.competitor_detector import (
    CompetitorMatch,
    detect_competitors,
    get_competitor_terms,
)
from app.services.intent import (
    QueryIntent,
    classify_intent,
)
from app.services.intent import (
    overlay_for as intent_overlay_for,
)
from app.services.langfuse import (
    current_trace_id,
    observe,
    start_trace_span,
    update_current_trace,
)
from app.services.llm_cost import TurnUsage, end_turn, start_turn
from app.services.org_config import (
    ConfidenceThresholds,
    get_org_config,
)
from app.services.retrieval import SearchHit, hybrid_search_cached

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
class IntentEvent:
    """Emitted once at the very start of the task, before any retrieval.

    Tells the UI which prompt mode the orchestrator picked so it can show a
    subtle "Writing mode" / "Analysis mode" badge on the in-flight bubble.
    The label is informational only — the orchestrator already applied the
    intent-specific system prompt overlay before this fires.
    """

    kind: Literal["intent"] = field(default="intent", init=False)
    intent: str = ""


@dataclass(frozen=True)
class FinalEvent:
    """The full assistant message text, emitted at the end of either path."""

    kind: Literal["final"] = field(default="final", init=False)
    text: str = ""
    sources: list[dict] = field(default_factory=list)
    tool_calls_made: int = 0
    # Langfuse trace id for this turn (or None when tracing is disabled).
    # Persisted on `messages.langfuse_trace_id` so feedback scores can be
    # attached back to the trace that produced the answer.
    trace_id: str | None = None
    # The intent classifier's label for the user message that produced this
    # answer. Persisted on `messages.metadata.intent` so we can slice metrics
    # by intent later (which mode is the slow one? which has lower thumbs?).
    intent: str = ""
    # Competitor watchlist hits in `text`. Empty tuple when no watchlist is
    # configured or no matches. Carried on the event (rather than re-scanned
    # at persistence time) so the chat router writes the same matches the UI
    # already rendered, with no race against a mid-flight watchlist edit.
    competitor_matches: tuple[CompetitorMatch, ...] = ()
    # Per-turn LLM token + cost totals. Populated from the TurnUsage
    # ContextVar drained at the end of execute_task. The chat router stamps
    # these onto the query_logs row for the LLM cost dashboard. None when
    # no LLM calls happened (defensive — shouldn't occur in production).
    usage: TurnUsage | None = None
    # Chunk-ids of every retrieval result surfaced this turn (cited + uncited).
    # Powers Day-4 training-pair hard-negative collection. Length matches
    # `len(all_hits)` after dedupe, capped by chat_max_context_chunks.
    retrieved_chunk_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ErrorEvent:
    """Surfaced for retrieval / LLM failures the orchestrator can't recover from."""

    kind: Literal["error"] = field(default="error", init=False)
    message: str = ""


@dataclass(frozen=True)
class ConfidenceEvent:
    """Surfaces a UI-facing confidence band for the assistant message.

    Computed from raw vector cosine of the chunks that survived into the final
    sources list (i.e. the chunks the orchestrator actually surfaced upward).
    RRF/FTS-only hits without a cosine are excluded — they're informative for
    ranking but not for absolute "how good a match" thresholding.

    `level` is a coarse high/medium/low band the UI maps to colour; `score`
    is the same average expressed on a 0–10 scale so the UI can render the
    number alongside the badge.
    """

    kind: Literal["confidence"] = field(default="confidence", init=False)
    level: Literal["high", "medium", "low"] = "low"
    score: float = 0.0
    chunks_considered: int = 0


@dataclass(frozen=True)
class CompetitorWarningEvent:
    """Surfaces watchlist hits found in the just-generated assistant text.

    Carries enough for the UI to render an inline banner ("Mentions: Acme,
    Globex") and gate outbound integration sends with a confirm dialog.
    The same payload — minus the snippets — is persisted to
    `competitor_mentions` after the assistant message row is saved.
    """

    kind: Literal["competitor_warning"] = field(default="competitor_warning", init=False)
    matches: tuple[CompetitorMatch, ...] = ()


@dataclass(frozen=True)
class KnowledgeGapEvent:
    """Emitted once after all retrieval is done if every search returned zero
    chunks. Triggers the inline "limited context found" warning in the chat UI
    so users learn to fill the gap (upload a doc) rather than chase a bad answer.

    `topics` lists the actual queries the LLM issued — those are what we'd
    suggest documents for, not whatever regex'd noun phrase we'd guess from
    the user's prompt.
    """

    kind: Literal["knowledge_gap"] = field(default="knowledge_gap", init=False)
    topics: tuple[str, ...] = ()


OrchestratorEvent = (
    SearchingEvent
    | SearchedEvent
    | SourcesEvent
    | TokenEvent
    | FinalEvent
    | ErrorEvent
    | KnowledgeGapEvent
    | ConfidenceEvent
    | IntentEvent
    | CompetitorWarningEvent
)


def _compute_confidence(
    sources: list[dict],
    hits: list[SearchHit],
    *,
    knowledge_gap: bool,
    thresholds: ConfidenceThresholds,
) -> ConfidenceEvent:
    """Average the raw vector cosine across cited chunks → confidence band.

    Thresholds are per-org (Agent Day 3) — admins tune them from the
    confidence settings page when their corpus produces too many false
    "low" badges (heterogeneous docs) or too many "high" (too-similar docs).
    """
    if not sources or knowledge_gap:
        return ConfidenceEvent(level="low", score=0.0, chunks_considered=0)

    cited_ids = {s.get("chunk_id") for s in sources if s.get("chunk_id")}
    cosines: list[float] = []
    for h in hits:
        if h.chunk_id in cited_ids and h.vector_similarity is not None:
            cosines.append(h.vector_similarity)
            cited_ids.discard(h.chunk_id)

    if not cosines:
        return ConfidenceEvent(level="low", score=0.0, chunks_considered=0)

    avg = sum(cosines) / len(cosines)
    if avg >= thresholds.high:
        level: Literal["high", "medium", "low"] = "high"
    elif avg >= thresholds.medium:
        level = "medium"
    else:
        level = "low"
    return ConfidenceEvent(
        level=level,
        score=round(avg * 10, 1),
        chunks_considered=len(cosines),
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
    user_id: str | None = None,
    conversation_id: str | None = None,
    scoped_document_id: str | None = None,
    scoped_tags: list[str] | None = None,
    conversation_summary: str | None = None,
    # ── Production Roadmap 1.5 / 1.9 — per-turn budget overrides ───────────
    # When set, these win over the per-org / per-settings defaults for THIS
    # task only. Two callers use them:
    #   * Quick Answer (1.9): the intent classifier auto-detects bounded
    #     factual lookups and caps tool rounds + searches for sub-2s latency.
    #   * Search broader/deeper (1.5): the chat UI exposes two retry buttons
    #     on low-confidence answers — "broader" drops scope constraints,
    #     "deeper" doubles the search budget.
    # Quick-answer auto-bound is SKIPPED when any explicit override is set,
    # so an explicit "deeper" retry on a quick-answer-classified message
    # actually goes deeper. None = no override.
    search_k_override: int | None = None,
    max_searches_override: int | None = None,
    max_tool_rounds_override: int | None = None,
    # Production Roadmap 1.5 — when True, drop scoped_document_id and
    # scoped_tags so the retry can search across the full knowledge base.
    # The intent classifier's QUICK_ANSWER detection does NOT toggle this;
    # only the explicit "Search broader" retry button does.
    ignore_scope: bool = False,
) -> AsyncIterator[OrchestratorEvent]:
    """Run one user task through the LLM+retrieval loop.

    Yields events in order: searching/searched* → sources → (token*) → final.
    On hard failure, yields a single ErrorEvent and stops.

    The `user_id` / `conversation_id` arguments are optional metadata that flow
    through to Langfuse for dashboard filtering. They have no effect on chat
    behavior beyond observability.
    """
    settings = get_settings()
    llm = llm_client or get_llm_client()

    # Production Roadmap 1.5 — broader-scope retry strips scope constraints.
    # We do this BEFORE the scope-resolution / system-prompt-note section so
    # nothing downstream sees the dropped scope.
    if ignore_scope:
        scoped_document_id = None
        scoped_tags = None

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

    # Bind a fresh per-turn token/cost accumulator. Every LLM call inside this
    # task will add to it via `record_usage` in the client. We drain it on
    # FinalEvent so the chat router can persist it onto query_logs.
    start_turn()

    # Day 4 #51 — intent classification. Zero-cost keyword pass; the result
    # drives a per-intent system-prompt overlay and flows to the UI so it
    # can render a mode badge. Emitted *before* any retrieval so the UI can
    # paint the badge alongside the "Thinking…" indicator.
    intent_result = classify_intent(user_message)
    yield IntentEvent(intent=intent_result.intent.value)

    # ── Per-turn budget resolution ──────────────────────────────────────────
    # Order of precedence (highest to lowest):
    #   1. Explicit kwargs (search_k_override, max_searches_override, ...)
    #      — used by Search broader/deeper retries (1.5).
    #   2. QUICK_ANSWER auto-bound — only applied when NO explicit override
    #      is supplied. A "deeper" retry on a quick-answer message correctly
    #      bypasses the auto-bound and goes full depth (1.9 + 1.5 compose).
    #   3. settings.* defaults.
    explicit_budget_override = (
        search_k_override is not None
        or max_searches_override is not None
        or max_tool_rounds_override is not None
    )
    quick_answer_mode = (
        intent_result.intent == QueryIntent.QUICK_ANSWER and not explicit_budget_override
    )
    effective_search_k = search_k_override or settings.chat_search_k
    effective_max_searches = max_searches_override or settings.chat_max_searches
    effective_max_tool_rounds = max_tool_rounds_override or settings.chat_max_tool_rounds
    effective_max_context_chunks = settings.chat_max_context_chunks
    if quick_answer_mode:
        # Aggressive caps for sub-2s perceived latency on simple lookups.
        # Tuned to the case "what's our holiday policy" — one search, one
        # round-trip, the LLM speaks. If the LLM somehow needs more than
        # this, the answer will simply be best-effort within the budget;
        # the user can retry with "Search deeper" to escalate (1.5).
        effective_max_tool_rounds = 1
        effective_max_searches = 2
        effective_max_context_chunks = min(effective_max_context_chunks, 8)
        effective_search_k = min(effective_search_k, 6)

    all_hits: list[SearchHit] = []
    seen_queries: set[str] = set()
    searches_done = 0
    tool_call_total = 0
    # Track every query the LLM ran AND its hit count. We use this both for
    # the SearchedEvent stream and for the post-loop knowledge-gap signal:
    # if every search returned 0, we emit a KnowledgeGapEvent so the UI can
    # nudge the user to upload more documents.
    search_attempts: list[tuple[str, int]] = []

    # Org-level config (Day 9 / #67 + Agent Day 3). One TTL-cached lookup pulls
    # both the system-prompt prefix AND per-org confidence thresholds so we
    # don't double-cache or double-fetch.
    try:
        cfg = await get_org_config(org_id)
        org_instructions = cfg.ai_instructions
        confidence_thresholds = cfg.confidence
    except Exception as exc:
        log.warning("org_config_lookup_failed: %s", exc)
        org_instructions = None
        confidence_thresholds = ConfidenceThresholds.default()

    # Scope-aware system note. When a conversation is pinned to one document,
    # tell the LLM so it doesn't generalize from a single source like it had
    # the whole knowledge base. Cheap one-line lookup; cached implicitly by
    # PostgREST connection reuse.
    if scoped_document_id:
        scope_note = await _scope_system_note(db_client, scoped_document_id)
        if scope_note:
            org_instructions = (
                f"{org_instructions}\n\n{scope_note}" if org_instructions else scope_note
            )

    # Tag-scoped chat (V3 #19). Resolve the tag set to a concrete doc-id list
    # exactly once per task — passing the tag array down through every search
    # would either mean an extra query per search call (wasteful) or pushing
    # the tag→doc join into the search SQL (touches the tuned index path).
    # The resolved list is small (at most N matching docs) and stable for the
    # life of this task.
    scoped_document_ids: list[str] | None = None
    if scoped_tags:
        scoped_document_ids = await _resolve_tag_scope(
            db_client, org_id=org_id, tags=scoped_tags
        )
        tag_note = _tag_scope_system_note(scoped_tags, scoped_document_ids)
        if tag_note:
            org_instructions = (
                f"{org_instructions}\n\n{tag_note}" if org_instructions else tag_note
            )

    # Conversation summary (Day 12 / #53). Injected as a system note ahead of
    # the live history window so the LLM stays coherent on long threads
    # without us re-sending hundreds of earlier-turn tokens.
    if conversation_summary:
        summary_note = (
            "EARLIER CONVERSATION (summary of turns not in live history):\n"
            f"{conversation_summary.strip()}"
        )
        org_instructions = (
            f"{org_instructions}\n\n{summary_note}" if org_instructions else summary_note
        )

    # Agent2 Day 2 #39 — user-pinned conversation context. Comes BEFORE the
    # intent overlay so it's framed as durable background, not the most
    # immediate instruction. Cap at 2000 chars enforced in DB.
    if conversation_id:
        pinned_context = await _fetch_pinned_context(db_client, conversation_id)
        if pinned_context:
            pin_note = (
                "PINNED CONTEXT (the user has fixed this for the conversation; "
                "honor it across every turn):\n"
                f"{pinned_context.strip()}"
            )
            org_instructions = (
                f"{org_instructions}\n\n{pin_note}" if org_instructions else pin_note
            )

    # Day 4 #51 — append the intent overlay last so it sits closest to the
    # user turn. Order matters: org rules → summary → mode-specific guidance.
    intent_overlay = intent_overlay_for(intent_result.intent)
    org_instructions = (
        f"{org_instructions}\n\n{intent_overlay}" if org_instructions else intent_overlay
    )

    # Agent2 Day 2 #37 — persona overlay. Feature 1.11 extends this to
    # custom (per-user) and org-curated personas. The fetcher returns the
    # overlay text directly so the dispatch logic stays in one place.
    if user_id:
        persona_note = await _resolve_persona_overlay(db_client, user_id)
        if persona_note:
            org_instructions = (
                f"{org_instructions}\n\n{persona_note}" if org_instructions else persona_note
            )

    # Sign-as hint, gated on TASK_GENERATION. Other intents (Q&A, analysis,
    # search) don't need a signature, so we save the tokens + the extra DB
    # roundtrip on those turns. The hint is opinionated about placeholders
    # because the model defaults to `[Your Name]` when it doesn't know who's
    # writing — we'd rather have it sign with whatever we know than leave a
    # template hole the user has to hand-edit.
    if intent_result.intent == QueryIntent.TASK_GENERATION and user_id:
        display_name = await _resolve_display_name(db_client, user_id)
        if display_name:
            sign_as_note = (
                f"SIGN AS: {display_name}. When the deliverable is an email, "
                f"message, or any signed correspondence, sign with this exact "
                f"name. Never use placeholders like [Your Name], [Name], or "
                f"[Signature]."
            )
            org_instructions = (
                f"{org_instructions}\n\n{sign_as_note}" if org_instructions else sign_as_note
            )

    started = time.perf_counter()
    final_text = ""

    # One Langfuse trace per turn. Nested @observe-decorated LLM and search
    # calls attach as child spans via OTel context propagation. When tracing
    # is disabled the context manager is a no-op.
    with start_trace_span(
        name="execute_task",
        input={"user_message": user_message, "stream": stream},
    ):
        update_current_trace(
            user_id=user_id,
            session_id=conversation_id,
            metadata={
                "org_id": org_id,
                "history_turns": len(history or []),
                "stream": stream,
                "intent": intent_result.intent.value,
                "intent_keywords": list(intent_result.matched_patterns),
            },
            tags=["chat", f"intent:{intent_result.intent.value}"],
        )

        for round_idx in range(effective_max_tool_rounds + 1):
            # Last round: force no more tools, just final text.
            tools_for_this_round = (SEARCH_TOOL,) if round_idx < effective_max_tool_rounds else ()

            try:
                response: LLMResponse = await llm.complete(
                    messages,
                    tools=tools_for_this_round,
                    system_extra=org_instructions,
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
                if searches_done + len(valid_calls) >= effective_max_searches:
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
                    _run_search(
                        tc.args["query"],
                        org_id,
                        db_client,
                        effective_search_k,
                        document_id=scoped_document_id,
                        document_ids=scoped_document_ids,
                    )
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
                search_attempts.append((tc.args["query"], len(hits)))
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
        sources = _dedupe_sources(all_hits, limit=effective_max_context_chunks)
        await _attach_review_due(sources, org_id=org_id, db_client=db_client)
        yield SourcesEvent(sources=sources)

        # Knowledge-gap signal: every search the LLM ran returned 0 hits.
        # Skip if no searches happened (pure-conversational turn like "thanks")
        # or if any search produced anything — we trust the LLM to communicate
        # uncertainty itself in the partial-coverage case.
        knowledge_gap = bool(search_attempts) and all(count == 0 for _, count in search_attempts)
        if knowledge_gap:
            gap_topics = tuple(q for q, _ in search_attempts)
            yield KnowledgeGapEvent(topics=gap_topics)
            # Persist the gap signal so admins can act on it (Agent Day 5).
            # Fire-and-forget through Inngest — a failed enqueue must not
            # block the answer streaming back to the user.
            try:
                await _enqueue_knowledge_gap(
                    org_id=org_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    user_message=user_message,
                    topics=gap_topics,
                )
            except Exception as exc:
                log.warning("knowledge_gap_enqueue_failed: %s", exc)

        # Confidence — emitted whenever the LLM consulted retrieval. Skipping
        # zero-source turns means a "hi how are you" doesn't render a "low
        # confidence" badge on a chat that wasn't asking for facts.
        confidence_event: ConfidenceEvent | None = None
        if sources:
            confidence_event = _compute_confidence(
                sources,
                all_hits,
                knowledge_gap=knowledge_gap,
                thresholds=confidence_thresholds,
            )
            yield confidence_event

        # Low-confidence gap signal (Gap-v2). A turn that *answered* but did
        # so on thin retrieval (conf<6 AND sources<2) is the bulk of platform
        # quality misses — the zero-hit path catches the obvious failures,
        # this one catches "we have a doc but it's stale / off-topic". We
        # fire one event per search the LLM ran so the worker can debounce
        # per-topic the same way it does for zero-hit.
        if (
            not knowledge_gap
            and search_attempts
            and confidence_event is not None
            and confidence_event.score < 6.0
            and len(sources) < 2
        ):
            low_conf_topics = tuple(q for q, _ in search_attempts)
            try:
                await _enqueue_knowledge_gap(
                    org_id=org_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    user_message=user_message,
                    topics=low_conf_topics,
                    signal_type="low_confidence",
                    confidence_score=confidence_event.score,
                    sources_count=len(sources),
                )
            except Exception as exc:
                log.warning("low_confidence_gap_enqueue_failed: %s", exc)

        if stream and not final_text:
            # We may have exited the loop because the LLM was about to speak. To
            # stream the final answer, re-issue the last LLM call WITHOUT tools
            # and consume it as a stream. (Gemini won't stream a turn that
            # produced tool_calls; calling again with the now-complete tool-result
            # history gives us a fresh streamable final turn.)
            full_text_parts: list[str] = []
            try:
                chunk_iter = await llm.stream(messages, tools=(), system_extra=org_instructions)
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

        # Competitor watchlist scan. Runs after generation so the regex
        # never sees in-flight tokens; fires regardless of confidence /
        # knowledge_gap because a mention is a mention. Empty watchlist
        # short-circuits inside detect_competitors (one tuple check).
        competitor_matches: tuple[CompetitorMatch, ...] = ()
        if final_text:
            try:
                terms = await get_competitor_terms(org_id=org_id, user_id=user_id)
                if terms.has_any:
                    hits = detect_competitors(
                        final_text,
                        org_terms=terms.org_terms,
                        user_terms=terms.user_terms,
                    )
                    competitor_matches = tuple(hits)
                    if competitor_matches:
                        yield CompetitorWarningEvent(matches=competitor_matches)
            except Exception as exc:
                log.warning("competitor_detection_failed: %s", exc)

        trace_id = current_trace_id()
        # Drain the per-turn accumulator. `end_turn()` detaches the ContextVar
        # so any stray LLM calls after FinalEvent (none expected) don't bleed
        # into the next turn's totals.
        turn_usage = end_turn()
        yield FinalEvent(
            text=final_text,
            sources=sources,
            tool_calls_made=tool_call_total,
            trace_id=trace_id,
            intent=intent_result.intent.value,
            competitor_matches=competitor_matches,
            usage=turn_usage,
            retrieved_chunk_ids=tuple(s["chunk_id"] for s in sources if s.get("chunk_id")),
        )


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _enqueue_knowledge_gap(
    *,
    org_id: str,
    user_id: str | None,
    conversation_id: str | None,
    user_message: str,
    topics: tuple[str, ...],
    signal_type: str = "zero_hit",
    confidence_score: float | None = None,
    sources_count: int | None = None,
) -> None:
    """Emit a knowledge/gap-detected event for the Inngest worker.

    Each search the LLM ran becomes its own gap row — that's the right
    granularity for the admin alert (a topic that's hit 3x is more
    actionable than a query that ran once). The worker handles dedupe +
    threshold + auto-draft.

    `signal_type` distinguishes the two production triggers:
      * 'zero_hit'         — all searches returned no chunks (the v1 case)
      * 'low_confidence'   — answer surfaced, but on thin retrieval (Gap-v2)
    Worker uses signal_type to decide whether to write an AI-stub draft
    (zero_hit only — low-confidence drafts would mostly fight existing docs)
    or just record for the weekly clustering report.
    """
    if not topics:
        return
    import inngest as _inngest_pkg

    from app.inngest.client import get_inngest_client

    client = get_inngest_client()
    for topic in topics:
        # Each topic is its own event so the worker can debounce per
        # (org, topic) cleanly. The event id includes a hash of the topic
        # to absorb tight duplicate fires from concurrent chat windows.
        await client.send(
            _inngest_pkg.Event(
                name="knowledge/gap-detected",
                data={
                    "org_id": org_id,
                    "topic": topic,
                    "query": user_message,
                    "user_id": user_id,
                    "conversation_id": conversation_id,
                    "signal_type": signal_type,
                    "confidence_score": confidence_score,
                    "sources_count": sources_count,
                },
            )
        )


async def _resolve_display_name(client: Client, user_id: str) -> str | None:
    """Fetch the user's display_name. Returns None when missing/blank so the
    caller can skip injecting an empty signature hint."""
    try:
        result = await asyncio.to_thread(
            lambda: client.table("users")
            .select("display_name")
            .eq("id", user_id)
            .maybe_single()
            .execute()
        )
    except Exception:
        return None
    name = (getattr(result, "data", None) or {}).get("display_name") if result else None
    if not name or not str(name).strip():
        return None
    return str(name).strip()


async def _fetch_pinned_context(client: Client, conversation_id: str) -> str | None:
    """Agent2 Day 2 #39 — read conversations.pinned_context.

    Returns None on miss / empty. RLS-scoped read; if the caller doesn't own
    the conversation, we get nothing back (same shape as not-pinned). Never
    fails the turn.
    """
    try:
        result = await asyncio.to_thread(
            lambda: client.table("conversations")
            .select("pinned_context")
            .eq("id", conversation_id)
            .maybe_single()
            .execute()
        )
    except Exception:
        return None
    val = (getattr(result, "data", None) or {}).get("pinned_context") if result else None
    if not val or not str(val).strip():
        return None
    return str(val).strip()


async def _fetch_user_persona_row(
    client: Client, user_id: str
) -> dict[str, Any] | None:
    """Feature 1.11 — read the persona triple from users in one round-trip.

    Returns the row's {persona, custom_persona_name, custom_persona_instructions}
    or None on any failure. Soft failures are silent because persona is an
    optimization, not a correctness requirement — chat should still answer.
    """
    try:
        result = await asyncio.to_thread(
            lambda: client.table("users")
            .select("persona, custom_persona_name, custom_persona_instructions")
            .eq("id", user_id)
            .maybe_single()
            .execute()
        )
    except Exception:
        return None
    return (getattr(result, "data", None) or None) if result else None


async def _fetch_org_persona(
    client: Client, persona_id: str
) -> dict[str, Any] | None:
    """Feature 1.11 — load an org_personas row by id. Returns None if the
    persona has been archived, deleted, or RLS denies access."""
    try:
        result = await asyncio.to_thread(
            lambda: client.table("org_personas")
            .select("id, name, instructions, is_archived")
            .eq("id", persona_id)
            .maybe_single()
            .execute()
        )
    except Exception:
        return None
    row = (getattr(result, "data", None) or None) if result else None
    if not row or row.get("is_archived"):
        return None
    return row


# Persona overlays — kept here so the prompt-engineering surface for chat
# lives in one file. Adding a new persona = update the DB CHECK + this dict.
_PERSONA_OVERLAYS: dict[str, str] = {
    "hr": (
        "USER ROLE: HR. Bias retrieval toward policy / handbook / compliance "
        "documents. Cite policy names and effective dates explicitly when "
        "relevant. Use a formal, compliance-conscious tone."
    ),
    "sales": (
        "USER ROLE: Sales. Bias retrieval toward competitor analysis, pricing, "
        "case studies, and product positioning. Format answers as scannable "
        "talking points where possible. Favor brevity and persuasive framing."
    ),
    "engineering": (
        "USER ROLE: Engineering. Bias retrieval toward technical specs, "
        "architecture docs, and runbooks. Include code snippets and exact "
        "technical terminology. Favor precision over brevity."
    ),
    "finance": (
        "USER ROLE: Finance. Bias retrieval toward budget, forecast, and "
        "financial-policy documents. Surface specific figures with citations "
        "to the source documents. Flag stale data explicitly."
    ),
    "operations": (
        "USER ROLE: Operations. Bias retrieval toward process / SOP / runbook "
        "documents. Format multi-step answers as numbered procedures. Call out "
        "decision points and prerequisites."
    ),
    "executive": (
        "USER ROLE: Executive. Synthesize across collections. Lead with a 2-3 "
        "sentence executive summary. Favor strategic framing — risks, "
        "opportunities, decisions needed — over operational detail."
    ),
}


_ORG_PERSONA_PREFIX = "org:"
_CUSTOM_INSTRUCTION_CAP = 2000  # matches users + org_personas CHECK


def _format_custom_overlay(name: str | None, instructions: str) -> str:
    """Wrap user-supplied instructions in the same `USER ROLE: …` framing the
    built-ins use so retrieval bias hints land in a predictable slot. Caller
    is responsible for upper-bounding `instructions` length."""
    body = instructions.strip()
    if not body:
        return ""
    label = (name or "Custom").strip() or "Custom"
    return f"USER ROLE: {label}. {body}"


def _persona_overlay(persona: str | None) -> str | None:
    """Resolve a BUILT-IN persona string to its system-prompt overlay.

    Kept for tests + callers that only need the static map. Custom/org
    personas go through `_resolve_persona_overlay` which makes I/O.
    """
    if not persona:
        return None
    return _PERSONA_OVERLAYS.get(persona)


async def _resolve_persona_overlay(client: Client, user_id: str) -> str | None:
    """Feature 1.11 — full persona resolution.

    Dispatch:
      * NULL           — no overlay.
      * Built-in key   — return _PERSONA_OVERLAYS[key].
      * 'custom'       — return formatted users.custom_persona_instructions.
      * 'org:<uuid>'   — load org_personas row, return its instructions.

    Falls back to None on any data anomaly (e.g. persona='custom' but
    instructions are NULL) so chat still works without a stale overlay.
    """
    row = await _fetch_user_persona_row(client, user_id)
    if not row:
        return None
    persona = row.get("persona")
    persona_str = str(persona).strip() if persona else None
    if not persona_str:
        return None

    if persona_str == "custom":
        instr = row.get("custom_persona_instructions")
        if not instr or not str(instr).strip():
            return None
        text = str(instr)[:_CUSTOM_INSTRUCTION_CAP]
        formatted = _format_custom_overlay(
            row.get("custom_persona_name"), text
        )
        return formatted or None

    if persona_str.startswith(_ORG_PERSONA_PREFIX):
        persona_id = persona_str[len(_ORG_PERSONA_PREFIX):]
        org_persona = await _fetch_org_persona(client, persona_id)
        if not org_persona:
            return None
        instr = org_persona.get("instructions")
        if not instr or not str(instr).strip():
            return None
        text = str(instr)[:_CUSTOM_INSTRUCTION_CAP]
        formatted = _format_custom_overlay(org_persona.get("name"), text)
        return formatted or None

    return _PERSONA_OVERLAYS.get(persona_str)


async def _scope_system_note(client: Client, document_id: str) -> str | None:
    """One-line scope reminder injected into the system prompt."""
    try:
        result = await asyncio.to_thread(
            lambda: client.table("documents")
            .select("name")
            .eq("id", document_id)
            .maybe_single()
            .execute()
        )
    except Exception:
        return None
    name = (getattr(result, "data", None) or {}).get("name") if result else None
    if not name:
        return None
    return (
        f"SCOPE: All retrieval for this conversation is restricted to the single "
        f"document \"{name}\". If the document doesn't cover what the user asks, "
        f"say so explicitly — do not draw on prior knowledge of the broader org."
    )


@observe(name="hybrid_search")
async def _run_search(
    query: str,
    org_id: str,
    client: Client,
    k: int,
    *,
    document_id: str | None = None,
    document_ids: list[str] | None = None,
) -> list[SearchHit]:
    # V3 #80 — cached variant. Skips embedding + RRF on cache hit; the
    # wrapper handles fail-open semantics if Upstash is unreachable. Document-
    # scoped (single doc) searches still bypass the cache inside the wrapper
    # because per-conversation hit rates are too low to justify the bookkeeping.
    #
    # V5 #106: bind the org context so the embedder factory can swap in a
    # fine-tuned model if this org has one deployed. Token-based unbind in
    # finally so concurrent searches in another task don't see stale state.
    from app.services.ingestion.embedder import (
        bind_org_for_embedding,
        reset_org_embedding,
    )

    token = bind_org_for_embedding(org_id)
    try:
        return await hybrid_search_cached(
            query,
            org_id,
            client,
            k=k,
            document_id=document_id,
            document_ids=document_ids,
        )
    finally:
        reset_org_embedding(token)


async def _resolve_tag_scope(
    client: Client, *, org_id: str, tags: list[str]
) -> list[str]:
    """Look up document_ids for all docs in `org_id` carrying any of `tags`.

    Semantics: OR across tags (a doc with ANY of the requested tags counts).
    We picked OR over AND because the chat scope selector is a multi-pick
    "search in: HR or Legal or Finance" affordance, not an intersection
    filter. Empty result is meaningful — it means the scope yields no docs,
    and downstream retrieval correctly short-circuits to "no results".
    """
    clean = [t.strip().lower() for t in tags if t and t.strip()]
    if not clean:
        return []
    try:
        res = await asyncio.to_thread(
            lambda: client.table("documents")
            .select("id")
            .eq("org_id", org_id)
            .overlaps("tags", clean)
            .execute()
        )
    except Exception as exc:
        log.warning("tag_scope_resolution_failed: %s", exc)
        # Fail open to the empty set, not to "unscoped" — leaking docs the
        # user explicitly excluded would be worse than returning nothing.
        return []
    rows = getattr(res, "data", None) or []
    return [r["id"] for r in rows if r.get("id")]


def _tag_scope_system_note(
    tags: list[str], document_ids: list[str] | None
) -> str | None:
    """Tell the LLM about the active tag scope, mirroring the doc-scope note."""
    if not tags:
        return None
    tag_csv = ", ".join(f'"{t}"' for t in tags[:8])
    if not document_ids:
        return (
            f"SCOPE: This conversation is restricted to documents tagged "
            f"{tag_csv}, but no documents currently carry those tags. Tell the "
            f"user so explicitly — do not draw on the rest of the knowledge "
            f"base or prior knowledge."
        )
    return (
        f"SCOPE: All retrieval for this conversation is restricted to the "
        f"{len(document_ids)} document(s) tagged {tag_csv}. If those documents "
        f"don't cover what the user asks, say so explicitly."
    )


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

    Returns a UI-friendly shape plus two scores used downstream:
        similarity        — the canonical (RRF-fused) score from retrieval
        vector_similarity — the raw cosine, when the vector branch contributed
    Both feed `record_citations` so the analytics layer can rank chunks by
    quality, and `vector_similarity` is what drives the confidence band.
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
                "document_version_id": h.document_version_id,
                "version_number": h.version_number,
                "page_number": h.page_number,
                "section_heading": h.section_heading,
                "excerpt": (h.content or "").strip()[:280],
                "snippet": h.snippet,
                "similarity": h.similarity,
                "vector_similarity": h.vector_similarity,
            }
        )
    return out


async def _attach_review_due(
    sources: list[dict], *, org_id: str, db_client: Client
) -> None:
    """Stamp each source dict with its document's `review_due_at`.

    Powers the in-chat "may be outdated" banner. Best-effort: a failed read
    leaves sources untouched and the banner simply won't render.
    """
    doc_ids = {s["document_id"] for s in sources if s.get("document_id")}
    if not doc_ids:
        return
    try:
        result = await asyncio.to_thread(
            lambda: db_client.table("documents")
            .select("id, review_due_at")
            .eq("org_id", org_id)
            .in_("id", list(doc_ids))
            .execute()
        )
    except Exception as exc:
        log.warning("attach_review_due failed: %s", exc)
        return
    by_id = {row["id"]: row.get("review_due_at") for row in (result.data or [])}
    for s in sources:
        doc_id = s.get("document_id")
        if doc_id in by_id:
            s["review_due_at"] = by_id[doc_id]


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


# ── Autoflow generation entry point ──────────────────────────────────────
#
# Used by the generate_output action in app/services/autoflow_actions.py. We
# expose a dedicated function rather than have the action call execute_task
# directly because:
#   1. Autoflow runs aren't tied to a user/conversation — passing in the
#      conversation-shaped args would be misleading.
#   2. The action needs the confidence score (a number, not just a band) to
#      decide whether to gate on approval; the helper threads that out.
#   3. We get a single source-of-truth file for "what an autoflow looks like
#      as a chat turn" — important when we evolve the prompt template.

async def run_autoflow_generation(
    *,
    org_id: str,
    prompt: str,
    scope_tags: list[str] | None = None,
    intent_hint: str | None = None,  # currently unused; reserved for forced-intent overrides
    llm_client: LLMClient | None = None,
) -> dict:
    """Single-shot KB-backed generation for an autoflow action.

    Returns
        {"text": str, "sources": [...], "confidence": float | None}

    Sources are the same shape as chat citations (doc_name, chunk_id, page_number,
    document_id). Confidence is the cosine-averaged retrieval confidence on a
    0..1 scale, or None when no chunks were cited.
    """
    from app.database import get_service_client

    # Service-role client: autoflows run in the background with no user JWT,
    # but every query already carries an explicit org_id, so RLS would be a
    # no-op anyway. Matches how Inngest workers talk to the DB elsewhere.
    db_client = get_service_client()

    text = ""
    sources: list[dict] = []
    confidence: float | None = None

    async for event in execute_task(
        user_message=prompt,
        org_id=org_id,
        db_client=db_client,
        history=None,
        llm_client=llm_client,
        stream=False,
        scoped_tags=scope_tags or None,
    ):
        if isinstance(event, FinalEvent):
            text = event.text
            sources = event.sources
        elif isinstance(event, ConfidenceEvent):
            # ConfidenceEvent.score is on a 0..10 scale; normalise back to 0..1
            # so the threshold on autoflows.confidence_threshold (also 0..1)
            # compares apples-to-apples.
            confidence = round(event.score / 10.0, 4)
        elif isinstance(event, ErrorEvent):
            raise RuntimeError(f"autoflow_generation_failed: {event.message}")

    return {"text": text, "sources": sources, "confidence": confidence}
