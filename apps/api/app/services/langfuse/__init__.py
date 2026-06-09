"""LLM observability via Langfuse v3.

Single source of truth for tracing. The rest of the codebase imports:

    from app.services.observability import (
        langfuse_client, observe, current_trace_id, score_feedback,
    )

and stays decoupled from the SDK. When LANGFUSE_* env vars are empty (dev),
`langfuse_client` is None and `observe` becomes a no-op decorator.

Why v3:
    * OTel-based — `@observe` auto-captures inputs/outputs/exceptions and
      maintains the active span context for nested calls.
    * `update_current_generation(usage=...)` lets us patch in token usage from
      Gemini's response without reshaping our LLM client.
    * `langfuse.flush()` ensures spans are sent before the process exits
      (lambdas, short-running scripts).
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable, Iterator

from app.config import get_settings

log = logging.getLogger(__name__)

_settings = get_settings()

# Lazy singleton so importing this module is safe even if langfuse isn't
# installed (e.g. test envs). All consumers must check `langfuse_client`
# before using it; the helpers in this module do that for you.
langfuse_client: Any | None = None

try:
    if _settings.langfuse_public_key and _settings.langfuse_secret_key:
        from langfuse import Langfuse, get_client  # type: ignore[import-not-found]
        from langfuse import observe as _lf_observe  # type: ignore[import-not-found]

        # Initialize once — `get_client()` returns this instance afterward.
        Langfuse(
            public_key=_settings.langfuse_public_key,
            secret_key=_settings.langfuse_secret_key,
            host=_settings.langfuse_host,
            sample_rate=_settings.langfuse_sample_rate,
            environment=_settings.environment,
            release=_settings.release_version or None,
        )
        langfuse_client = get_client()
        log.info("Langfuse initialized: host=%s sample=%.2f", _settings.langfuse_host, _settings.langfuse_sample_rate)
    else:
        log.info("Langfuse disabled (LANGFUSE_PUBLIC_KEY/SECRET_KEY not set).")
        _lf_observe = None  # type: ignore[assignment]
except Exception as exc:  # langfuse not installed or init failed
    log.warning("Langfuse setup failed, continuing without tracing: %s", exc)
    langfuse_client = None
    _lf_observe = None  # type: ignore[assignment]


def observe(
    *,
    name: str | None = None,
    as_type: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Wrap a function with a Langfuse span. When tracing is disabled, returns
    the original function unchanged so there's zero runtime cost.

    `as_type='generation'` marks LLM calls so usage/cost columns light up in
    the dashboard. Use the default (a regular span) for retrieval, parsing, etc.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if langfuse_client is None or _lf_observe is None:
            return fn
        kwargs: dict[str, Any] = {}
        if name:
            kwargs["name"] = name
        if as_type:
            kwargs["as_type"] = as_type
        return _lf_observe(**kwargs)(fn)  # type: ignore[no-any-return]

    return decorator


def current_trace_id() -> str | None:
    """ID of the active root trace, or None if tracing is off / no trace exists.

    Routes capture this AFTER the LLM call so we can stamp it onto the persisted
    `messages.langfuse_trace_id` column for later feedback forwarding.
    """
    if langfuse_client is None:
        return None
    try:
        return langfuse_client.get_current_trace_id()
    except Exception as exc:
        log.debug("get_current_trace_id failed: %s", exc)
        return None


def update_current_trace(
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> None:
    """Attach context to the current trace. Called from execute_task once the
    org/user/conversation are known. Silently no-ops if tracing is disabled."""
    if langfuse_client is None:
        return
    try:
        kwargs: dict[str, Any] = {}
        if user_id is not None:
            kwargs["user_id"] = user_id
        if session_id is not None:
            kwargs["session_id"] = session_id
        if metadata is not None:
            kwargs["metadata"] = metadata
        if tags is not None:
            kwargs["tags"] = tags
        if kwargs:
            langfuse_client.update_current_trace(**kwargs)
    except Exception as exc:
        log.debug("update_current_trace failed: %s", exc)


def update_current_generation(
    *,
    model: str | None = None,
    usage: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Patch the active `as_type='generation'` span with token usage / model.
    Called from inside the LLM client right after we get Gemini's response."""
    if langfuse_client is None:
        return
    try:
        kwargs: dict[str, Any] = {}
        if model is not None:
            kwargs["model"] = model
        if usage is not None:
            # Langfuse v3 uses `usage_details` for the structured token counts.
            kwargs["usage_details"] = usage
        if metadata is not None:
            kwargs["metadata"] = metadata
        if kwargs:
            langfuse_client.update_current_generation(**kwargs)
    except Exception as exc:
        log.debug("update_current_generation failed: %s", exc)


def score_feedback(*, trace_id: str, value: int, comment: str | None = None) -> None:
    """Forward a thumbs-up (1) / thumbs-down (0) onto the trace that produced
    the rated message. No-op if tracing is disabled or trace_id is empty."""
    if langfuse_client is None or not trace_id:
        return
    try:
        langfuse_client.create_score(
            trace_id=trace_id,
            name="user_feedback",
            value=value,
            comment=comment,
            data_type="NUMERIC",
        )
    except Exception as exc:
        log.warning("langfuse_score_failed", extra={"trace_id": trace_id, "error": str(exc)})


def flush() -> None:
    """Synchronously flush queued spans. Call from app shutdown handlers."""
    if langfuse_client is None:
        return
    try:
        langfuse_client.flush()
    except Exception as exc:
        log.debug("langfuse_flush_failed: %s", exc)


@contextmanager
def trace_disabled() -> Iterator[None]:
    """No-op stub — kept for symmetry if we ever want a 'don't trace this block'
    escape hatch. Lets tests opt out without monkeypatching the decorator."""
    yield


@contextmanager
def start_trace_span(name: str, *, input: Any = None) -> Iterator[Any]:
    """Open a root span and make it active for the duration of the block.

    Async generators (execute_task) can't use `@observe` cleanly, so they wrap
    their loop with this and child `@observe`-decorated calls attach to it via
    OTel context propagation. Yields the span (or None when tracing is off) so
    callers can call `span.update(output=...)` before exit.
    """
    if langfuse_client is None:
        yield None
        return
    try:
        with langfuse_client.start_as_current_span(name=name, input=input) as span:
            yield span
    except Exception as exc:
        log.debug("start_trace_span failed: %s", exc)
        yield None


__all__ = [
    "langfuse_client",
    "observe",
    "current_trace_id",
    "update_current_trace",
    "update_current_generation",
    "score_feedback",
    "flush",
    "trace_disabled",
    "start_trace_span",
]
