"""Idempotency-Key support for write endpoints with external side-effects.

The recruiting publish flow creates a real job in Greenhouse. A retry from
a flaky reverse proxy or a double-click in the UI must NOT create the job
twice. This module provides:

    1. A FastAPI dependency factory `idempotent(endpoint=...)` that:
         - returns None when no Idempotency-Key header is present
         - returns a previously-cached response when the key matches
         - records the new key + request_hash so subsequent retries hit
           the cache
    2. `record_response(...)` to write the response body after a successful
       endpoint completes — the next retry within 24h short-circuits.

Why a dedicated table over Redis: we want the cached response *body* across
process restarts; Redis would work too but every other persistent dedupe in
this codebase lives in Postgres, so we stay consistent.

Mismatched-payload behaviour: if the same Idempotency-Key arrives with a
*different* request body, we 409 — that's the RFC-recommended behaviour
(draft-ietf-httpapi-idempotency-key-header-05).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, status

from app.database import get_service_client

log = logging.getLogger(__name__)

_MAX_KEY_LEN = 200
_TTL_HOURS = 24


@dataclass
class IdempotencyHit:
    """Returned by `check_and_reserve` when the key matched a prior completed
    request. The router should return `response_body` directly with HTTP
    `response_status` instead of re-running the side-effect."""

    response_status: int
    response_body: dict[str, Any]


def _hash_request(body: dict[str, Any]) -> str:
    """Stable hash of the request body so we can detect mismatched-payload
    replays. JSON sort keys + ASCII-only to keep the hash deterministic across
    Python runs."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def check_and_reserve(
    *,
    org_id: str,
    request: Request,
    endpoint: str,
    body: dict[str, Any],
) -> IdempotencyHit | None:
    """Inspect the Idempotency-Key header. Returns:
        None             — no header, or a fresh key we've now reserved
        IdempotencyHit   — cached response from a prior call with the same key

    Raises 409 when the key matches a prior call but the request body differs.
    """
    key = request.headers.get("Idempotency-Key") or request.headers.get("idempotency-key")
    if not key:
        return None
    if len(key) > _MAX_KEY_LEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Idempotency-Key exceeds {_MAX_KEY_LEN} chars.",
        )

    request_hash = _hash_request(body)
    svc = get_service_client()

    def _fetch() -> dict[str, Any] | None:
        res = (
            svc.table("idempotency_keys")
            .select("*")
            .eq("org_id", org_id)
            .eq("key", key)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    existing = await asyncio.to_thread(_fetch)
    if existing:
        if existing.get("endpoint") != endpoint:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency-Key already used for a different endpoint.",
            )
        if existing.get("request_hash") != request_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency-Key reused with a different request body.",
            )
        if existing.get("response_body") is not None:
            return IdempotencyHit(
                response_status=int(existing.get("response_status") or 200),
                response_body=existing["response_body"],
            )
        # Row exists but no response yet — a concurrent request is in flight.
        # We refuse the second call rather than racing the side-effect.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A request with this Idempotency-Key is still in flight.",
        )

    # Reserve the key with a NULL response so a concurrent retry sees the
    # in-flight marker above. Insert may race; the PK collision converts to
    # a 409 on the second arrival.
    row = {
        "org_id": org_id,
        "key": key,
        "endpoint": endpoint,
        "request_hash": request_hash,
        "response_status": None,
        "response_body": None,
    }
    try:
        await asyncio.to_thread(
            lambda: svc.table("idempotency_keys").insert(row).execute()
        )
    except Exception as exc:
        # Most likely a tight PK race — re-check and resolve.
        if "duplicate" in str(exc).lower() or "23505" in str(exc):
            existing = await asyncio.to_thread(_fetch)
            if existing and existing.get("response_body") is not None:
                return IdempotencyHit(
                    response_status=int(existing.get("response_status") or 200),
                    response_body=existing["response_body"],
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A request with this Idempotency-Key is in flight.",
            ) from exc
        raise
    return None


async def record_response(
    *,
    org_id: str,
    request: Request,
    response_status: int,
    response_body: dict[str, Any],
) -> None:
    """Persist the completed response so subsequent retries return it verbatim.

    No-op when the request didn't carry an Idempotency-Key — most calls don't.
    """
    key = request.headers.get("Idempotency-Key") or request.headers.get("idempotency-key")
    if not key:
        return
    svc = get_service_client()
    try:
        await asyncio.to_thread(
            lambda: svc.table("idempotency_keys")
            .update(
                {
                    "response_status": response_status,
                    "response_body": response_body,
                }
            )
            .eq("org_id", org_id)
            .eq("key", key)
            .execute()
        )
    except Exception as exc:
        log.warning("idempotency.record_failed key=%s err=%s", key[:16], exc)
