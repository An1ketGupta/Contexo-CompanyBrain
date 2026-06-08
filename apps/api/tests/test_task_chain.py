"""Tool-use orchestrator tests.

Uses a FakeLLMClient + a fake hybrid_search so we exercise the full loop
without burning Gemini credits or hitting Supabase. The orchestrator's
contract is: events stream in order, caps are enforced, errors degrade
gracefully.
"""
from __future__ import annotations

from typing import Any, AsyncIterator
from unittest.mock import patch

import pytest

from app.services.llm import LLMResponse, Message, StreamChunk, ToolCall
from app.services.llm.client import SEARCH_TOOL_NAME
from app.services.llm.task_chain import (
    ErrorEvent,
    FinalEvent,
    SearchedEvent,
    SearchingEvent,
    SourcesEvent,
    TokenEvent,
    execute_task,
)
from app.services.retrieval import SearchHit


# ── Fakes ─────────────────────────────────────────────────────────────────────

class FakeLLMClient:
    """Scripted LLM. Each .complete() pops the next response. .stream() yields
    StreamChunks from the next response's text."""

    model = "fake"

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.complete_calls: list[list[Message]] = []
        self.stream_calls: list[list[Message]] = []

    async def complete(self, messages: list[Message], **_: Any) -> LLMResponse:
        self.complete_calls.append(list(messages))
        if not self._responses:
            raise AssertionError("FakeLLMClient ran out of scripted responses")
        return self._responses.pop(0)

    async def stream(self, messages: list[Message], **_: Any) -> AsyncIterator[StreamChunk]:
        self.stream_calls.append(list(messages))
        text = self._responses.pop(0).text if self._responses else ""

        async def _gen() -> AsyncIterator[StreamChunk]:
            # Chunk the text into small pieces so the stream test can observe ordering.
            for word in text.split(" "):
                if word:
                    yield StreamChunk(kind="text", text=word + " ")
            yield StreamChunk(kind="done")
        return _gen()


def _hit(chunk_id: str, doc_name: str = "Handbook.pdf", page: int | None = 1) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        content=f"content of {chunk_id}",
        document_id=f"doc-{chunk_id}",
        document_name=doc_name,
        chunk_index=0,
        page_number=page,
        section_heading=None,
        similarity=0.5,
    )


def _tool_call(query: str, name: str = SEARCH_TOOL_NAME, call_id: str | None = None) -> ToolCall:
    return ToolCall(id=call_id or f"call-{query}", name=name, args={"query": query})


def _collect_events(task) -> list:
    """Helper to drain an async generator in a sync test."""
    import asyncio

    async def _drain() -> list:
        out = []
        async for ev in task:
            out.append(ev)
        return out

    return asyncio.run(_drain())


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_no_tool_calls_returns_text_immediately():
    llm = FakeLLMClient([LLMResponse(text="Hello there.")])
    events = _collect_events(
        execute_task(
            user_message="hi",
            org_id="org-1",
            db_client=object(),  # never touched
            llm_client=llm,
            stream=False,
        )
    )
    final = [e for e in events if isinstance(e, FinalEvent)]
    assert len(final) == 1
    assert final[0].text == "Hello there."
    assert final[0].sources == []
    assert final[0].tool_calls_made == 0
    assert len(llm.complete_calls) == 1


def test_single_search_round_then_text():
    llm = FakeLLMClient(
        [
            LLMResponse(tool_calls=(_tool_call("refund policy"),)),
            LLMResponse(text="Per our refund policy, ..."),
        ]
    )

    async def fake_search(query, org_id, client, k=8):
        return [_hit("c1")]

    with patch("app.services.llm.task_chain.hybrid_search", new=fake_search):
        events = _collect_events(
            execute_task(
                user_message="write refund email",
                org_id="org-1",
                db_client=object(),
                llm_client=llm,
                stream=False,
            )
        )

    kinds = [type(e).__name__ for e in events]
    assert kinds[:2] == ["SearchingEvent", "SearchedEvent"]
    assert any(isinstance(e, SourcesEvent) for e in events)
    final = next(e for e in events if isinstance(e, FinalEvent))
    assert final.tool_calls_made == 1
    assert len(final.sources) == 1
    assert final.sources[0]["chunk_id"] == "c1"


def test_parallel_searches_in_one_turn():
    llm = FakeLLMClient(
        [
            LLMResponse(
                tool_calls=(
                    _tool_call("refund policy"),
                    _tool_call("customer tone"),
                    _tool_call("complaint handling"),
                )
            ),
            LLMResponse(text="Done."),
        ]
    )

    calls_seen: list[str] = []

    async def fake_search(query, org_id, client, k=8):
        calls_seen.append(query)
        return [_hit(f"c-{query}")]

    with patch("app.services.llm.task_chain.hybrid_search", new=fake_search):
        events = _collect_events(
            execute_task(
                user_message="x",
                org_id="org-1",
                db_client=object(),
                llm_client=llm,
                stream=False,
            )
        )

    searching = [e for e in events if isinstance(e, SearchingEvent)]
    assert len(searching) == 3
    assert {e.query for e in searching} == {"refund policy", "customer tone", "complaint handling"}
    final = next(e for e in events if isinstance(e, FinalEvent))
    assert final.tool_calls_made == 3
    assert len(final.sources) == 3
    assert set(calls_seen) == {"refund policy", "customer tone", "complaint handling"}


def test_dedup_duplicate_queries_within_turn():
    llm = FakeLLMClient(
        [
            LLMResponse(
                tool_calls=(_tool_call("vacation policy"), _tool_call("Vacation Policy "))
            ),
            LLMResponse(text="Final."),
        ]
    )

    async def fake_search(query, org_id, client, k=8):
        return [_hit("c1")]

    with patch("app.services.llm.task_chain.hybrid_search", new=fake_search):
        events = _collect_events(
            execute_task(
                user_message="x",
                org_id="org-1",
                db_client=object(),
                llm_client=llm,
                stream=False,
            )
        )

    final = next(e for e in events if isinstance(e, FinalEvent))
    # Second query is case-insensitive duplicate of first → skipped.
    assert final.tool_calls_made == 1


def test_unknown_tool_name_skipped():
    llm = FakeLLMClient(
        [
            LLMResponse(
                tool_calls=(
                    _tool_call("Q", name="some_other_tool"),
                    _tool_call("refund policy"),
                )
            ),
            LLMResponse(text="OK"),
        ]
    )

    async def fake_search(query, org_id, client, k=8):
        return [_hit("c1")]

    with patch("app.services.llm.task_chain.hybrid_search", new=fake_search):
        events = _collect_events(
            execute_task(
                user_message="x",
                org_id="org-1",
                db_client=object(),
                llm_client=llm,
                stream=False,
            )
        )

    final = next(e for e in events if isinstance(e, FinalEvent))
    assert final.tool_calls_made == 1  # only the valid one


def test_empty_query_skipped():
    """When all tool calls in a round are invalid (e.g. empty query), the
    orchestrator exits gracefully without looping — preventing the model from
    burning rounds on broken tool calls."""
    llm = FakeLLMClient(
        [LLMResponse(text="thinking out loud", tool_calls=(_tool_call("   "),))]
    )

    async def fake_search(query, org_id, client, k=8):
        return [_hit("c1")]

    with patch("app.services.llm.task_chain.hybrid_search", new=fake_search):
        events = _collect_events(
            execute_task(
                user_message="x",
                org_id="org-1",
                db_client=object(),
                llm_client=llm,
                stream=False,
            )
        )

    final = next(e for e in events if isinstance(e, FinalEvent))
    # We salvage any preamble text the LLM emitted alongside the bad tool call.
    assert final.text == "thinking out loud"
    assert final.tool_calls_made == 0


def test_max_rounds_cap_forces_final_text_without_tools():
    # Model keeps requesting tools; orchestrator should cut off and force a
    # tool-less final round. Stream of responses:
    #   round 0..3 → tool call each
    #   round 4 (final, no tools) → text
    llm = FakeLLMClient(
        [
            LLMResponse(tool_calls=(_tool_call(f"q{i}"),))
            for i in range(4)
        ]
        + [LLMResponse(text="capped final text")]
    )

    async def fake_search(query, org_id, client, k=8):
        return [_hit(f"c-{query}")]

    with patch("app.services.llm.task_chain.hybrid_search", new=fake_search):
        events = _collect_events(
            execute_task(
                user_message="x",
                org_id="org-1",
                db_client=object(),
                llm_client=llm,
                stream=False,
            )
        )

    final = next(e for e in events if isinstance(e, FinalEvent))
    assert final.text == "capped final text"
    assert final.tool_calls_made == 4


def test_dedup_sources_across_searches_by_chunk_id():
    llm = FakeLLMClient(
        [
            LLMResponse(
                tool_calls=(_tool_call("a"), _tool_call("b"))
            ),
            LLMResponse(text="ok"),
        ]
    )

    async def fake_search(query, org_id, client, k=8):
        # Both searches return the same chunk c1 plus a unique one
        unique = _hit(f"u-{query}")
        return [_hit("c1"), unique]

    with patch("app.services.llm.task_chain.hybrid_search", new=fake_search):
        events = _collect_events(
            execute_task(
                user_message="x",
                org_id="org-1",
                db_client=object(),
                llm_client=llm,
                stream=False,
            )
        )

    sources = next(e for e in events if isinstance(e, SourcesEvent)).sources
    chunk_ids = [s["chunk_id"] for s in sources]
    assert chunk_ids.count("c1") == 1
    assert set(chunk_ids) == {"c1", "u-a", "u-b"}


def test_search_failure_does_not_abort_loop():
    llm = FakeLLMClient(
        [
            LLMResponse(tool_calls=(_tool_call("good"), _tool_call("bad"))),
            LLMResponse(text="recovered"),
        ]
    )

    async def fake_search(query, org_id, client, k=8):
        if query == "bad":
            raise RuntimeError("transient retrieval error")
        return [_hit("c1")]

    with patch("app.services.llm.task_chain.hybrid_search", new=fake_search):
        events = _collect_events(
            execute_task(
                user_message="x",
                org_id="org-1",
                db_client=object(),
                llm_client=llm,
                stream=False,
            )
        )

    final = next(e for e in events if isinstance(e, FinalEvent))
    assert final.text == "recovered"
    # One source (from the successful "good" search)
    assert len(final.sources) == 1


def test_llm_error_emits_error_event():
    class BrokenLLM:
        model = "fake"

        async def complete(self, messages, **_):
            from app.services.llm import LLMError

            raise LLMError("model is on fire")

        async def stream(self, messages, **_):
            raise AssertionError("should not be called")

    events = _collect_events(
        execute_task(
            user_message="x",
            org_id="org-1",
            db_client=object(),
            llm_client=BrokenLLM(),
            stream=False,
        )
    )
    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(errors) == 1
    assert "model is on fire" in errors[0].message


def test_empty_message_rejected():
    llm = FakeLLMClient([])
    events = _collect_events(
        execute_task(
            user_message="   ",
            org_id="org-1",
            db_client=object(),
            llm_client=llm,
            stream=False,
        )
    )
    assert any(isinstance(e, ErrorEvent) for e in events)


def test_streaming_path_emits_tokens():
    # Round 0: complete() returns tool calls (consumed by tool loop).
    # Round 1: complete() returns text-only (final_text stays empty in stream mode).
    # Then orchestrator calls stream() — pops the next response as token source.
    llm = FakeLLMClient(
        [
            LLMResponse(tool_calls=(_tool_call("vacation"),)),
            LLMResponse(text="ignored placeholder"),       # consumed by .complete() in round 1
            LLMResponse(text="streamed final answer here"),  # consumed by .stream()
        ]
    )

    async def fake_search(query, org_id, client, k=8):
        return [_hit("c1")]

    with patch("app.services.llm.task_chain.hybrid_search", new=fake_search):
        events = _collect_events(
            execute_task(
                user_message="x",
                org_id="org-1",
                db_client=object(),
                llm_client=llm,
                stream=True,
            )
        )

    tokens = [e for e in events if isinstance(e, TokenEvent)]
    assert len(tokens) >= 2  # multiple words
    assembled = "".join(t.text for t in tokens).strip()
    assert "streamed final answer here" in assembled

    final = next(e for e in events if isinstance(e, FinalEvent))
    assert "streamed final answer here" in final.text
