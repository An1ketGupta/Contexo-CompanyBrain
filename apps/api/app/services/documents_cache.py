"""Cache helpers for the documents-list endpoint (V3 Day 5 #80).

The pattern is "version-stamped keys" — every cached entry's key embeds the
current per-org cache version (`cv:{org_id}`). Invalidation is one INCR;
old keys become unreachable without scanning Redis.

Why this lives in its own module instead of inline in the router:
  * The Inngest document/process function also needs to invalidate when a doc
    flips to ready/failed — keeping the bump call in one named helper means
    both call sites use the same namespace.
  * Tests can patch a small surface (this module) instead of poking Upstash.

This module is fail-open — every helper swallows Upstash errors and degrades
to "uncached" behaviour. A Redis outage must never break document listing.
"""
from __future__ import annotations

from typing import Any

from app.observability import get_logger
from app.services.redis_cache import (
    bump_cache_version,
    cache_get_json,
    cache_set_json,
    get_cache_version,
    hash_for_key,
    jittered_ttl,
)

log = get_logger(__name__)

_DOC_LIST_TTL_SECONDS = 30
_DOC_LIST_NEGATIVE_TTL_SECONDS = 10  # keep empties briefer so uploads propagate fast


def _key(org_id: str, version: int, filter_hash: str) -> str:
    return f"docs:list:{org_id}:v{version}:{filter_hash}"


async def get_cached_document_list(
    *,
    org_id: str,
    filter_signature: tuple[Any, ...],
) -> dict[str, Any] | None:
    """Return the cached list payload for the (org, filters) tuple, or None.

    `filter_signature` should be every query parameter that affects the
    result set — status, file_type, sorted tags, search, sort_by, sort_dir,
    limit, offset. Caller is responsible for canonicalising (sort the list,
    lowercase strings) so two semantically-equal requests hash the same.
    """
    if not org_id:
        return None
    version = await get_cache_version(org_id)
    key = _key(org_id, version, hash_for_key(*filter_signature))
    return await cache_get_json(key)


async def set_cached_document_list(
    *,
    org_id: str,
    filter_signature: tuple[Any, ...],
    payload: dict[str, Any],
) -> None:
    if not org_id:
        return
    version = await get_cache_version(org_id)
    key = _key(org_id, version, hash_for_key(*filter_signature))
    # Negative cache: when the org has no documents (or the filter matched
    # nothing), use a much shorter TTL so the first upload becomes visible
    # within ~10s without needing an invalidation hook on the empty path.
    is_empty = not payload.get("documents")
    ttl = _DOC_LIST_NEGATIVE_TTL_SECONDS if is_empty else _DOC_LIST_TTL_SECONDS
    await cache_set_json(key, payload, ttl_seconds=jittered_ttl(ttl))


async def invalidate_document_caches(org_id: str) -> None:
    """Bump the org's cache version, invalidating every cached doc list AND
    every cached search result in one O(1) operation.

    Used everywhere a document's catalogue-visible state changes: upload
    complete, delete, bulk-delete, tag patch, review patch, Inngest pipeline
    flips status to 'ready' or 'failed'.
    """
    if not org_id:
        return
    new_version = await bump_cache_version(org_id)
    log.debug("doc_cache_invalidated", org_id=org_id, new_version=new_version)
