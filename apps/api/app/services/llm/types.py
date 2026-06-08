"""Provider-agnostic LLM types.

These shapes are deliberately not Gemini-specific so the orchestrator can be
unit-tested against a FakeLLMClient and so swapping to Claude/OpenAI later is
a single-file change (per CLAUDE.md: "every external service has an adapter").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["user", "assistant", "tool"]


@dataclass(frozen=True)
class ToolCall:
    """A function-call request emitted by the LLM."""

    id: str               # provider-assigned (or synthesized) call ID
    name: str             # tool function name
    args: dict[str, Any]  # parsed arguments


@dataclass(frozen=True)
class ToolResult:
    """Our response back to the LLM after executing a ToolCall."""

    call_id: str
    name: str
    content: str          # serialized result; tool decides own format (we use plain text)


@dataclass(frozen=True)
class Message:
    """A single turn in the conversation passed to the LLM.

    For the `tool` role, `tool_result` is set and `content` is ignored.
    For the `assistant` role with tool calls, `tool_calls` is set.
    """

    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_result: ToolResult | None = None


@dataclass(frozen=True)
class LLMResponse:
    """A single completion from the LLM — either text, tool calls, or both.

    Gemini occasionally returns both: a thought-style preamble plus a function
    call. We surface both so the orchestrator can decide what to do.
    """

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str | None = None


@dataclass(frozen=True)
class StreamChunk:
    """One increment from a streaming completion."""

    kind: Literal["text", "tool_call", "done", "error"]
    text: str = ""
    tool_call: ToolCall | None = None
    error: str | None = None
