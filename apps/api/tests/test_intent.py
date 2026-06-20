"""V3 Day 4 #51 - intent classification + orchestrator wiring.

These tests close the gap between "the code exists" and "the Day 4 contract
is production-safe":

* classifier recognizes all four labels with stable priority
* execute_task injects the per-intent overlay into the LLM system prompt
* execute_task emits the intent event before any streamed content
"""
from __future__ import annotations

import os
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

os.environ["DEBUG"] = "false"

from app.services.intent import QueryIntent, classify_intent, overlay_for
from app.services.llm.client import SEARCH_TOOL
from app.services.llm.task_chain import FinalEvent, execute_task
from app.services.llm.types import LLMResponse, StreamChunk


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("What is our refund policy?", QueryIntent.FACTUAL_QA),
        ("Write a customer apology email for a delayed shipment", QueryIntent.TASK_GENERATION),
        ("Compare onboarding and offboarding steps", QueryIntent.ANALYSIS),
        ("Find the PTO policy document", QueryIntent.SEARCH),
    ],
)
def test_classify_intent_covers_all_four_types(query: str, expected: QueryIntent):
    result = classify_intent(query)
    assert result.intent is expected
    assert isinstance(result.matched_patterns, tuple)


def test_classify_intent_prefers_write_over_analysis_for_mixed_requests():
    result = classify_intent("Summarize the policy and write a rollout email")
    assert result.intent is QueryIntent.TASK_GENERATION


@pytest.mark.asyncio
async def test_execute_task_appends_intent_overlay_to_system_prompt(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeLLM:
        model = "fake-llm"

        async def complete(self, messages, *, tools=(), temperature=None, timeout=None, system_extra=None):
            captured["messages"] = messages
            captured["tools"] = tools
            captured["system_extra"] = system_extra
            return LLMResponse(text="Here is the comparison.")

        async def stream(self, messages, *, tools=(), temperature=None, timeout=None, system_extra=None):
            raise AssertionError("stream() should not be called in non-stream mode")

    async def _fake_get_org_config(_org_id: str):
        return SimpleNamespace(
            ai_instructions="Org style: be precise.",
            confidence=SimpleNamespace(high=0.85, medium=0.65),
        )

    async def _fake_get_competitor_terms(**_kwargs):
        return SimpleNamespace(has_any=False, org_terms=(), user_terms=())

    monkeypatch.setattr(
        "app.services.llm.task_chain.get_org_config",
        _fake_get_org_config,
    )
    monkeypatch.setattr("app.services.llm.task_chain.start_trace_span", lambda **_: nullcontext())
    monkeypatch.setattr("app.services.llm.task_chain.update_current_trace", lambda **_: None)
    monkeypatch.setattr("app.services.llm.task_chain.current_trace_id", lambda: None)
    monkeypatch.setattr(
        "app.services.llm.task_chain.get_competitor_terms",
        _fake_get_competitor_terms,
    )

    events = [
        event
        async for event in execute_task(
            user_message="Compare the onboarding and offboarding process",
            org_id="org-123",
            db_client=object(),
            llm_client=_FakeLLM(),
            stream=False,
        )
    ]

    assert events[0].kind == "intent"
    assert events[-1].kind == "final"
    assert isinstance(events[-1], FinalEvent)
    assert events[-1].intent == QueryIntent.ANALYSIS.value
    assert captured["tools"] == (SEARCH_TOOL,)
    assert "Org style: be precise." in str(captured["system_extra"])
    assert overlay_for(QueryIntent.ANALYSIS) in str(captured["system_extra"])


@pytest.mark.asyncio
async def test_execute_task_emits_intent_before_stream_tokens(monkeypatch):
    class _FakeLLM:
        model = "fake-llm"

        async def complete(self, messages, *, tools=(), temperature=None, timeout=None, system_extra=None):
            # Force the streaming branch: no tool calls, no final text yet.
            return LLMResponse(text="")

        async def stream(self, messages, *, tools=(), temperature=None, timeout=None, system_extra=None):
            async def _iterator():
                yield StreamChunk(kind="text", text="First")
                yield StreamChunk(kind="text", text=" second")
                yield StreamChunk(kind="done")

            return _iterator()

    async def _fake_get_org_config(_org_id: str):
        return SimpleNamespace(
            ai_instructions=None,
            confidence=SimpleNamespace(high=0.85, medium=0.65),
        )

    async def _fake_get_competitor_terms(**_kwargs):
        return SimpleNamespace(has_any=False, org_terms=(), user_terms=())

    monkeypatch.setattr(
        "app.services.llm.task_chain.get_org_config",
        _fake_get_org_config,
    )
    monkeypatch.setattr("app.services.llm.task_chain.start_trace_span", lambda **_: nullcontext())
    monkeypatch.setattr("app.services.llm.task_chain.update_current_trace", lambda **_: None)
    monkeypatch.setattr("app.services.llm.task_chain.current_trace_id", lambda: None)
    monkeypatch.setattr(
        "app.services.llm.task_chain.get_competitor_terms",
        _fake_get_competitor_terms,
    )

    events = [
        event
        async for event in execute_task(
            user_message="Find the holiday calendar",
            org_id="org-123",
            db_client=object(),
            llm_client=_FakeLLM(),
            stream=True,
        )
    ]

    event_kinds = [event.kind for event in events]
    assert event_kinds[0] == "intent"
    assert event_kinds[-1] == "final"
    assert "token" in event_kinds
    assert event_kinds.index("intent") < event_kinds.index("token")
    assert events[0].intent == QueryIntent.SEARCH.value
    assert events[-1].text == "First second"
