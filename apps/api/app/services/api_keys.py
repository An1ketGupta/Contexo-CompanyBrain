"""API key service (Day 15 / #47).

Token format:
    cb_live_<43 url-safe-base64 chars>   total length 51

    "cb"      brand prefix
    "live"    environment marker (room for future cb_test_*)
    The body is 32 bytes of entropy from secrets.token_urlsafe(32).

Storage:
    SHA-256(full_key) goes into api_keys.key_hash. First 12 chars of the
    plaintext go into key_prefix for display ("cb_live_xxxx…"). The
    plaintext is shown to the admin exactly once at creation.

Verification on every API call: SHA-256 → indexed lookup → cheap.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.database import get_service_client

log = logging.getLogger(__name__)

KEY_PREFIX = "cb_live_"
DISPLAY_PREFIX_LEN = 12  # "cb_live_" (8) + 4 chars of body


@dataclass(frozen=True)
class IssuedKey:
    """What the create endpoint returns once and never again."""

    id: str
    name: str
    scope: str
    full_key: str       # show-once
    key_prefix: str
    created_at: str


@dataclass(frozen=True)
class ApiKeyContext:
    """The auth context derived from a valid API key on each request."""

    id: str
    org_id: str
    created_by: str | None
    scope: str
    name: str


def generate_key() -> tuple[str, str, str]:
    """Returns (full_key, key_hash, key_prefix)."""
    body = secrets.token_urlsafe(32)
    full = f"{KEY_PREFIX}{body}"
    h = hashlib.sha256(full.encode("utf-8")).hexdigest()
    return full, h, full[:DISPLAY_PREFIX_LEN]


def hash_key(full_key: str) -> str:
    return hashlib.sha256(full_key.encode("utf-8")).hexdigest()


# ── CRUD ────────────────────────────────────────────────────────────────────

async def create_key(
    *,
    org_id: str,
    user_id: str,
    name: str,
    scope: str = "org",
) -> IssuedKey:
    if scope not in ("org", "user"):
        raise ValueError("scope must be 'org' or 'user'")
    full, key_hash, key_prefix = generate_key()
    svc = get_service_client()
    row = {
        "org_id": org_id,
        "created_by": user_id,
        "name": name,
        "key_hash": key_hash,
        "key_prefix": key_prefix,
        "scope": scope,
    }
    result = await asyncio.to_thread(
        lambda: svc.table("api_keys").insert(row).execute()
    )
    db_row = (result.data or [None])[0]
    if not db_row:
        raise RuntimeError("API key insert returned no row.")
    return IssuedKey(
        id=db_row["id"],
        name=db_row["name"],
        scope=db_row["scope"],
        full_key=full,
        key_prefix=key_prefix,
        created_at=db_row["created_at"],
    )


async def list_keys(*, org_id: str) -> list[dict[str, Any]]:
    svc = get_service_client()
    result = await asyncio.to_thread(
        lambda: svc.table("api_keys")
        .select(
            "id, name, scope, key_prefix, last_used_at, created_at, revoked_at, created_by"
        )
        .eq("org_id", org_id)
        .order("created_at", desc=True)
        .execute()
    )
    return result.data or []


async def revoke_key(*, org_id: str, key_id: str) -> bool:
    svc = get_service_client()
    result = await asyncio.to_thread(
        lambda: svc.table("api_keys")
        .update({"revoked_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", key_id)
        .eq("org_id", org_id)
        .is_("revoked_at", "null")
        .execute()
    )
    return bool(result.data)


# ── Auth ────────────────────────────────────────────────────────────────────

async def verify_key(authorization: str | None) -> ApiKeyContext:
    """Map a Bearer header to a live ApiKeyContext.

    Raises ValueError on any failure (router catches → 401). Updates
    last_used_at as a best-effort side effect so admins can see which keys
    are still in use without polling logs.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ValueError("missing_bearer")
    raw = authorization.split(" ", 1)[1].strip()
    if not raw.startswith(KEY_PREFIX):
        raise ValueError("malformed_key")

    key_hash = hash_key(raw)
    svc = get_service_client()
    result = await asyncio.to_thread(
        lambda: svc.table("api_keys")
        .select("id, org_id, created_by, scope, name, revoked_at")
        .eq("key_hash", key_hash)
        .maybe_single()
        .execute()
    )
    row = result.data if result else None
    # Single failure path covers both "key not found" and "key revoked" so
    # an attacker can't distinguish whether a hash is in the keyspace.
    # Both branches do the same minimal work (one DB lookup already happened
    # above) so there's no measurable timing differential. The discriminator
    # string ("unknown_key" / "revoked_key") is logged server-side via the
    # router's log.info call before being collapsed to a uniform 401.
    if not row:
        raise ValueError("unknown_key")
    if row.get("revoked_at"):
        raise ValueError("revoked_key")

    # Bump last_used_at off the hot path so a slow DB doesn't block auth.
    asyncio.create_task(_touch_last_used(row["id"]))

    return ApiKeyContext(
        id=row["id"],
        org_id=row["org_id"],
        created_by=row.get("created_by"),
        scope=row["scope"],
        name=row["name"],
    )


async def _touch_last_used(key_id: str) -> None:
    svc = get_service_client()
    try:
        await asyncio.to_thread(
            lambda: svc.table("api_keys")
            .update({"last_used_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", key_id)
            .execute()
        )
    except Exception as exc:
        log.warning("api_key_touch_failed: %s", exc)
