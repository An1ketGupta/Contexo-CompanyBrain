"""Supabase Realtime broadcast adapter (Production Roadmap 2.7).

Server-side helper for emitting events onto a Supabase Realtime broadcast
channel. The Next.js frontend subscribes via the Supabase JS client:

    const channel = supabase.channel(`conversation:${id}`, {
        config: { broadcast: { self: false } },
    });
    channel.on('broadcast', { event: 'token' }, ({ payload }) => { ... });
    channel.subscribe();

Why broadcast vs. postgres_changes:
  * postgres_changes fires once per DB row insert; for streamed tokens that
    would mean inserting hundreds of rows per AI turn — burns IO and bloats
    the messages table. Broadcast is in-memory and free.
  * Token events are ephemeral by design — we never want to replay them.
    The final assistant message row hits the DB once at end-of-stream and
    THAT is what new joiners load on subscribe.

Auth:
  We use the service role key for the HTTP broadcast endpoint. Clients
  authenticate via the anon JWT (RLS-style); a future hardening pass should
  enable Realtime channel authorisation policies so only channel members can
  subscribe. For now, the channel name embeds the conversation_id, which is
  not enumerable (UUID v4) — the security boundary is "you must know the
  UUID," same as the existing `shared_outputs` token model.

Fire-and-forget:
  Broadcasts are best-effort. A failed broadcast must not break the streaming
  HTTP response — the initiating user always gets their SSE stream. We log
  but never raise.

Coalescing:
  Per-token events are common (~200 events per AI turn). We batch into 8ms
  windows via `BroadcastBatcher` so the actual HTTP fanout is bounded.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

_BATCH_WINDOW_MS = 40   # coalesce up to 40ms of events into one HTTP POST
_MAX_BATCH = 32         # cap on messages per HTTP call


def _broadcast_url() -> str:
    settings = get_settings()
    base = (settings.supabase_url or "").rstrip("/")
    return f"{base}/realtime/v1/api/broadcast"


def _headers() -> dict[str, str]:
    settings = get_settings()
    key = settings.supabase_service_role_key
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


async def broadcast_messages(messages: list[dict[str, Any]]) -> None:
    """POST a batch of broadcast messages.

    `messages` is a list of {topic, event, payload} dicts. Failures are
    logged but never raised — broadcasting is best-effort.
    """
    if not messages:
        return
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        log.warning("realtime_broadcast_misconfigured")
        return
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(4.0)) as client:
            resp = await client.post(
                _broadcast_url(),
                headers=_headers(),
                json={"messages": messages},
            )
        if resp.status_code >= 400:
            log.warning(
                "realtime_broadcast_failed status=%s body=%s",
                resp.status_code,
                resp.text[:200],
            )
    except Exception as exc:
        log.warning("realtime_broadcast_exception: %s", exc)


async def broadcast_event(
    *, topic: str, event: str, payload: dict[str, Any]
) -> None:
    """Convenience single-message broadcast. Prefer BroadcastBatcher in hot loops."""
    await broadcast_messages(
        [{"topic": topic, "event": event, "payload": payload}]
    )


class BroadcastBatcher:
    """Coalesces broadcasts into a background pump.

    Usage (inside the SSE generator):

        bb = BroadcastBatcher(topic=f"conversation:{conv_id}")
        await bb.start()
        try:
            ...
            bb.publish("token", {"text": "..."})
            ...
        finally:
            await bb.flush_and_close()

    The pump task reads from an in-process queue and posts every
    _BATCH_WINDOW_MS or whenever the buffer hits _MAX_BATCH. A flush_and_close
    drains the queue before returning so a `done` event is never lost.
    """

    def __init__(self, *, topic: str) -> None:
        self.topic = topic
        self._queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._pump())

    def publish(self, event: str, payload: dict[str, Any]) -> None:
        if self._stopping:
            return
        self._queue.put_nowait((event, payload))

    async def flush_and_close(self) -> None:
        self._stopping = True
        self._queue.put_nowait(None)
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()

    async def _pump(self) -> None:
        buffer: list[dict[str, Any]] = []
        last_flush = time.monotonic()
        while True:
            timeout = max(0.005, (_BATCH_WINDOW_MS / 1000.0) - (time.monotonic() - last_flush))
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            except TimeoutError:
                item = "tick"  # type: ignore[assignment]
            if item is None:
                if buffer:
                    await broadcast_messages(buffer)
                return
            if isinstance(item, tuple):
                event, payload = item
                buffer.append(
                    {"topic": self.topic, "event": event, "payload": payload}
                )
            if buffer and (len(buffer) >= _MAX_BATCH or item == "tick"):
                # Flush even on the tick path so we don't sit on a half-batch.
                to_send = buffer[:]
                buffer.clear()
                last_flush = time.monotonic()
                # Fire-and-forget — the pump should not block on slow HTTP.
                asyncio.create_task(broadcast_messages(to_send))


def conversation_topic(conversation_id: str) -> str:
    """Canonical channel name for multiplayer chat broadcasts."""
    return f"conversation:{conversation_id}"
