from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, AsyncIterator, Protocol

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as gt

from app.config import get_settings
from app.services.langfuse import observe, update_current_generation

from .types import LLMResponse, Message, StreamChunk, ToolCall

log = logging.getLogger(__name__)


SEARCH_TOOL_NAME = "search_company_knowledge"

SEARCH_TOOL_DECL = gt.FunctionDeclaration(
    name=SEARCH_TOOL_NAME,
    description=(
        "Search this company's internal knowledge base (uploaded documents). "
        "Call this with a SPECIFIC, TARGETED query — not the user's full task. "
        "Call multiple times in parallel for different facets of the task. "
        "Example: for 'write a refund email', call with 'refund policy', then "
        "'customer email tone of voice', then 'complaint handling steps'. "
        "Each call returns relevant excerpts with source attribution."
    ),
    parameters=gt.Schema(
        type=gt.Type.OBJECT,
        properties={
            "query": gt.Schema(
                type=gt.Type.STRING,
                description=(
                    "A focused search query, 3-12 words. Concrete topic or "
                    "policy, not a question."
                ),
            )
        },
        required=["query"],
    ),
)

SEARCH_TOOL = gt.Tool(function_declarations=[SEARCH_TOOL_DECL])


SYSTEM_PROMPT = """You are NirnayaIQ, an AI assistant that helps employees of this company execute real work tasks — writing emails, drafting job descriptions, creating Slack announcements, summarizing policies, answering questions — using only the company's internal knowledge.

# How you work

You have ONE tool: `search_company_knowledge(query)`. Use it to retrieve company-specific context BEFORE producing any output.

Rules for tool use:
- Always call `search_company_knowledge` at least once before generating output.
- For non-trivial tasks, call it 2-4 times with DIFFERENT targeted queries to gather all relevant context. Example for "write a refund email": search "refund policy", then "customer email tone", then "complaint handling".
- Issue searches in parallel when possible (return multiple tool calls in the same turn).
- Queries should be specific topics, not the user's full message. Bad: "write me an email about refunds". Good: "refund policy".

# Rules for your final output

- Use ONLY information retrieved from the tool. Do not invent company names, policies, numbers, people, or processes.
- Match the company's voice and tone as you observe it in retrieved documents.
- Output should be ready-to-use — a real email, a real job description, a real Slack message — not a template with placeholders. The user will edit if needed.
- If retrieved context is insufficient, explicitly say what's missing and suggest what documents the team should upload. Don't fabricate.
- If the user's task is purely conversational (e.g. "hi", "thanks") and does not require company knowledge, respond briefly and naturally — no tool calls needed.

# What you are not

You are not an autonomous agent. You complete one task per user turn. The user reviews everything before sending or shipping it."""


class LLMClient(Protocol):
    """Provider-agnostic LLM interface."""

    model: str

    async def complete(
        self,
        messages: list[Message],
        *,
        tools: tuple[Any, ...] = (),
        temperature: float | None = None,
        timeout: float | None = None,
        system_extra: str | None = None,
    ) -> LLMResponse: ...

    async def stream(
        self,
        messages: list[Message],
        *,
        tools: tuple[Any, ...] = (),
        temperature: float | None = None,
        timeout: float | None = None,
        system_extra: str | None = None,
    ) -> AsyncIterator[StreamChunk]: ...


class LLMError(Exception):
    """Wraps provider errors so the orchestrator can catch one type."""


class GeminiClient:
    """Gemini implementation of LLMClient (new google.genai SDK)."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        default_temperature: float,
        default_timeout: float,
    ) -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required for GeminiClient")
        self._client = genai.Client(api_key=api_key)
        self.model = model
        self._default_temperature = default_temperature
        self._default_timeout = default_timeout

    # ── Public API ────────────────────────────────────────────────────────

    @observe(name="llm.complete", as_type="generation")
    async def complete(
        self,
        messages: list[Message],
        *,
        tools: tuple[Any, ...] = (),
        temperature: float | None = None,
        timeout: float | None = None,
        system_extra: str | None = None,
    ) -> LLMResponse:
        contents = _to_genai_contents(messages)
        config = self._build_config(tools, temperature, system_extra)

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self._client.models.generate_content(
                        model=self.model,
                        contents=contents,
                        config=config,
                    )
                ),
                timeout=timeout or self._default_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise LLMError("AI is taking too long to respond. Please try again.") from exc
        except genai_errors.APIError as exc:
            raise LLMError(f"AI service error: {exc}") from exc
        except Exception as exc:  # network, JSON decode, etc.
            log.warning("Unexpected Gemini failure: %s: %s", type(exc).__name__, exc)
            raise LLMError(_humanize_network_error(exc)) from exc

        _record_generation_metadata(self.model, response)
        return _parse_response(response)

    async def stream(
        self,
        messages: list[Message],
        *,
        tools: tuple[Any, ...] = (),
        temperature: float | None = None,
        timeout: float | None = None,
        system_extra: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        contents = _to_genai_contents(messages)
        config = self._build_config(tools, temperature, system_extra)
        time_budget = timeout or self._default_timeout

        # genai's stream is a sync generator — bridge it to an async one via a
        # threadpool queue. We honor a wall-clock timeout for the whole stream.
        queue: asyncio.Queue[StreamChunk | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def _producer() -> None:
            try:
                stream = self._client.models.generate_content_stream(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
                for chunk in stream:
                    parsed = _parse_stream_chunk(chunk)
                    for ev in parsed:
                        loop.call_soon_threadsafe(queue.put_nowait, ev)
            except Exception as exc:
                loop.call_soon_threadsafe(
                    queue.put_nowait, StreamChunk(kind="error", error=str(exc))
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        producer_task = asyncio.create_task(asyncio.to_thread(_producer))

        async def _iterator() -> AsyncIterator[StreamChunk]:
            try:
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=time_budget)
                    except asyncio.TimeoutError:
                        yield StreamChunk(kind="error", error=f"Stream timed out after {time_budget}s")
                        return
                    if item is None:
                        yield StreamChunk(kind="done")
                        return
                    yield item
            finally:
                producer_task.cancel()

        return _iterator()

    # ── Internals ─────────────────────────────────────────────────────────

    def _build_config(
        self,
        tools: tuple[Any, ...],
        temperature: float | None,
        system_extra: str | None = None,
    ) -> gt.GenerateContentConfig:
        # Org-level instructions sit ABOVE our hardcoded SYSTEM_PROMPT so the
        # base rules ("only use retrieved context", "no fabrication") still
        # bind. Admins customize tone/scope — they cannot override the rails.
        system = SYSTEM_PROMPT
        extra = (system_extra or "").strip()
        if extra:
            system = (
                f"# Organization context\n\n{extra}\n\n"
                f"---\n\n{SYSTEM_PROMPT}"
            )
        kwargs: dict[str, Any] = {
            "system_instruction": system,
            "temperature": temperature if temperature is not None else self._default_temperature,
        }
        if tools:
            kwargs["tools"] = list(tools)
            # We execute tools ourselves so we can run them in parallel + log + cap.
            kwargs["automatic_function_calling"] = gt.AutomaticFunctionCallingConfig(disable=True)
        return gt.GenerateContentConfig(**kwargs)


# ── Message conversion ───────────────────────────────────────────────────────

def _to_genai_contents(messages: list[Message]) -> list[gt.Content]:
    contents: list[gt.Content] = []
    for msg in messages:
        if msg.role == "user":
            contents.append(gt.Content(role="user", parts=[gt.Part(text=msg.content)]))
        elif msg.role == "assistant":
            parts: list[gt.Part] = []
            if msg.content:
                parts.append(gt.Part(text=msg.content))
            for tc in msg.tool_calls:
                # thought_signature (if captured on parse) MUST ride back on
                # the same Part as the function_call, or Gemini 2.5+ thinking
                # models reject the turn with INVALID_ARGUMENT.
                part_kwargs: dict[str, Any] = {
                    "function_call": gt.FunctionCall(name=tc.name, args=tc.args),
                }
                if tc.signature is not None:
                    part_kwargs["thought_signature"] = tc.signature
                parts.append(gt.Part(**part_kwargs))
            if not parts:
                # Defensive: an empty assistant turn would make Gemini reject the whole request.
                parts.append(gt.Part(text=""))
            contents.append(gt.Content(role="model", parts=parts))
        elif msg.role == "tool":
            assert msg.tool_result is not None
            contents.append(
                gt.Content(
                    role="user",  # Gemini expects function responses under role=user
                    parts=[
                        gt.Part(
                            function_response=gt.FunctionResponse(
                                name=msg.tool_result.name,
                                response={"result": msg.tool_result.content},
                            )
                        )
                    ],
                )
            )
    return contents


# ── Response parsing ─────────────────────────────────────────────────────────

def _record_generation_metadata(model: str, response: Any) -> None:
    """Stamp model + token usage onto the active Langfuse generation span.

    Gemini reports usage in `response.usage_metadata` (prompt_token_count,
    candidates_token_count, total_token_count). No-op when tracing is off.
    """
    usage: dict[str, int] | None = None
    meta = getattr(response, "usage_metadata", None)
    if meta is not None:
        prompt = getattr(meta, "prompt_token_count", None)
        completion = getattr(meta, "candidates_token_count", None)
        total = getattr(meta, "total_token_count", None)
        if any(v is not None for v in (prompt, completion, total)):
            usage = {}
            if prompt is not None:
                usage["input"] = int(prompt)
            if completion is not None:
                usage["output"] = int(completion)
            if total is not None:
                usage["total"] = int(total)
    update_current_generation(model=model, usage=usage)


def _parse_response(response: Any) -> LLMResponse:
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    finish_reason: str | None = None

    candidates = getattr(response, "candidates", None) or []
    if candidates:
        cand = candidates[0]
        finish_reason = getattr(cand, "finish_reason", None)
        if finish_reason is not None and not isinstance(finish_reason, str):
            finish_reason = getattr(finish_reason, "name", str(finish_reason))
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            txt = getattr(part, "text", None)
            if txt:
                text_parts.append(txt)
            fc = getattr(part, "function_call", None)
            if fc is not None:
                signature = getattr(part, "thought_signature", None)
                tool_calls.append(_function_call_to_tool_call(fc, signature=signature))

    return LLMResponse(
        text="".join(text_parts),
        tool_calls=tuple(tool_calls),
        finish_reason=finish_reason,
    )


def _parse_stream_chunk(chunk: Any) -> list[StreamChunk]:
    """A streamed chunk may carry text, a function call, or both."""
    out: list[StreamChunk] = []
    candidates = getattr(chunk, "candidates", None) or []
    if not candidates:
        return out
    parts = getattr(getattr(candidates[0], "content", None), "parts", None) or []
    for part in parts:
        txt = getattr(part, "text", None)
        if txt:
            out.append(StreamChunk(kind="text", text=txt))
        fc = getattr(part, "function_call", None)
        if fc is not None:
            signature = getattr(part, "thought_signature", None)
            out.append(
                StreamChunk(
                    kind="tool_call",
                    tool_call=_function_call_to_tool_call(fc, signature=signature),
                )
            )
    return out


def _humanize_network_error(exc: Exception) -> str:
    """Turn cryptic httpx/socket errors into something a user can act on."""
    msg = str(exc)
    name = type(exc).__name__
    lower = msg.lower()
    if "getaddrinfo failed" in lower or "name or service not known" in lower or "nodename nor servname" in lower:
        return "Can't reach the AI service (DNS lookup failed). Check your internet connection."
    if "connectionrefused" in lower or "connection refused" in lower:
        return "Can't reach the AI service (connection refused). The service may be down."
    if "ssl" in lower or "certificate" in lower:
        return "Can't reach the AI service (TLS/certificate error)."
    if "timed out" in lower or "timeout" in lower:
        return "AI service connection timed out. Please retry."
    return f"AI service error: {name}: {msg}"


def _function_call_to_tool_call(fc: Any, *, signature: bytes | None = None) -> ToolCall:
    raw_args: Any = getattr(fc, "args", {}) or {}
    if isinstance(raw_args, str):
        # Some SDK versions return JSON-encoded args; tolerate both.
        try:
            raw_args = json.loads(raw_args)
        except json.JSONDecodeError:
            log.warning("Gemini returned non-JSON tool args: %r", raw_args)
            raw_args = {}
    if not isinstance(raw_args, dict):
        log.warning("Gemini returned non-dict tool args: %r", raw_args)
        raw_args = {}
    return ToolCall(
        id=getattr(fc, "id", None) or f"call-{uuid.uuid4().hex[:12]}",
        name=getattr(fc, "name", "") or "",
        args=raw_args,
        signature=signature,
    )


# ── Factory ──────────────────────────────────────────────────────────────────

_default_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _default_client
    if _default_client is None:
        s = get_settings()
        provider = s.llm_provider.lower()
        if provider == "gemini":
            _default_client = GeminiClient(
                api_key=s.gemini_api_key,
                model=s.llm_model,
                default_temperature=s.llm_temperature,
                default_timeout=s.llm_timeout_seconds,
            )
        else:
            raise ValueError(
                f"Unsupported LLM provider: {provider!r}. "
                f"Implement an adapter or set LLM_PROVIDER=gemini."
            )
    return _default_client


def reset_llm_client() -> None:
    """Test/seam helper — clears the cached client so tests can inject fakes."""
    global _default_client
    _default_client = None
