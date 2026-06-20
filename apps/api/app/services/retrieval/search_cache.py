"""Cached hybrid_search wrapper — V3 #80.

Stores chunk IDs + search-computed scores in Redis; refetches chunk text +
doc metadata from Postgres on cache hit. Cuts the (embedding call + RRF
compute) cost without holding heavy chunk text in Upstash — the typical
~20 chunks × 1KB content stays in Postgres where it already lives.

Cache key: `search:{org_id}:v{N}:{hash(query, scope, k)}`
TTL:       60s for hits, 10s for empty results (so a doc upload propagates).
Stampede protection: ±10% jitter on TTL.

Invalidation: piggybacks on the docs-list cache version. When `documents_cache.
invalidate_document_caches(org_id)` bumps `cv:{org_id}`, both the doc list
and every cached search for that org become unreachable.

Fail-open: any Upstash error falls through to a fresh `hybrid_search()` call.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any

from supabase import Client

from app.observability import get_logger
from app.services.redis_cache import (
    cache_get_json,
    cache_set_json,
    get_cache_version,
    hash_for_key,
    jittered_ttl,
)

from .hybrid_search import DEFAULT_FINAL_K, hybrid_search
from .vector_search import SearchHit

log = get_logger(__name__)

_SEARCH_TTL_SECONDS = 60
_SEARCH_NEGATIVE_TTL_SECONDS = 10  # empty result sets — short so uploads propagate
_FIELDS_STORED_IN_CACHE = (
    # search-computed metadata that's NOT in the chunks table; refetching from
    # Postgres would require recomputing the search itself, defeating the cache.
    "chunk_id",
    "similarity",
    "snippet",
    "match_sources",
    "vector_rank",
    "fts_rank",
    "rrf_score",
    "vector_similarity",
)


def _scope_signature(
    document_id: str | None,
    document_ids: list[str] | None,
) -> tuple[Any, ...]:
    """Canonicalise scope params so {doc_ids=[a,b]} and {doc_ids=[b,a]} key the same."""
    doc_ids_normalised = tuple(sorted(document_ids)) if document_ids else None
    return (document_id, doc_ids_normalised)


def _hit_to_cacheable(hit: SearchHit) -> dict[str, Any]:
    """Serialise the subset of SearchHit we cache (chunks-table fields excluded)."""
    return {
        "chunk_id": hit.chunk_id,
        "similarity": hit.similarity,
        "snippet": hit.snippet,
        "match_sources": list(hit.match_sources),
        "vector_rank": hit.vector_rank,
        "fts_rank": hit.fts_rank,
        "rrf_score": hit.rrf_score,
        "vector_similarity": hit.vector_similarity,
    }


async def _refetch_chunk_payloads(
    chunk_ids: list[str],
    org_id: str,
    client: Client,
) -> dict[str, dict[str, Any]]:
    """Fetch (content, document_id, document_name, version, chunk_index, page, heading)
    for `chunk_ids`. Returns a chunk_id → fields dict; missing chunks are
    silently dropped (the doc was deleted between cache write and read).

    Single Postgres round-trip with PostgREST's nested-select on the FK to
    documents — avoids an N+1 over chunks → documents.
    """
    if not chunk_ids:
        return {}

    import asyncio as _asyncio

    try:
        res = await _asyncio.to_thread(
            lambda: client.table("chunks")
            .select(
                "id, content, document_id, document_version_id, chunk_index, page_number, section_heading, "
                "documents(name), document_versions(version_number)"
            )
            .in_("id", chunk_ids)
            .eq("org_id", org_id)
            .execute()
        )
    except Exception as exc:
        log.warning("search_cache_refetch_failed", error=str(exc))
        return {}

    out: dict[str, dict[str, Any]] = {}
    for row in (getattr(res, "data", None) or []):
        doc_join = row.get("documents") or {}
        version_join = row.get("document_versions") or {}
        out[row["id"]] = {
            "content": row.get("content") or "",
            "document_id": row.get("document_id") or "",
            "document_name": (doc_join.get("name") if isinstance(doc_join, dict) else "") or "",
            "document_version_id": row.get("document_version_id"),
            "version_number": (
                version_join.get("version_number") if isinstance(version_join, dict) else None
            ),
            "chunk_index": int(row.get("chunk_index") or 0),
            "page_number": row.get("page_number"),
            "section_heading": row.get("section_heading"),
        }
    return out


async def _rehydrate_hits(
    cached: list[dict[str, Any]],
    *,
    org_id: str,
    client: Client,
) -> list[SearchHit] | None:
    """Turn cached (chunk_id+scores) rows back into SearchHits.

    Returns None if anything looks off (refetch missed too many chunks → the
    cache is stale; let the caller fall through to a fresh search).
    """
    if not cached:
        return []

    chunk_ids = [row.get("chunk_id") for row in cached if row.get("chunk_id")]
    payloads = await _refetch_chunk_payloads(chunk_ids, org_id, client)

    # If >25% of the cached chunks have been deleted since the cache entry was
    # written, treat the entry as stale and force a fresh search. The version-
    # bump invalidator usually beats us to it; this is the safety net for the
    # rare race where a doc was deleted just after a search was cached.
    if chunk_ids and len(payloads) < len(chunk_ids) * 0.75:
        log.info(
            "search_cache_stale_dropping",
            cached=len(chunk_ids),
            refetched=len(payloads),
            org_id=org_id,
        )
        return None

    hits: list[SearchHit] = []
    for row in cached:
        cid = row.get("chunk_id")
        payload = payloads.get(cid)
        if not payload:
            continue
        hits.append(
            SearchHit(
                chunk_id=cid,
                content=payload["content"],
                document_id=payload["document_id"],
                document_name=payload["document_name"],
                document_version_id=payload.get("document_version_id"),
                version_number=payload.get("version_number"),
                chunk_index=payload["chunk_index"],
                page_number=payload.get("page_number"),
                section_heading=payload.get("section_heading"),
                similarity=float(row.get("similarity") or 0.0),
                snippet=row.get("snippet"),
                match_sources=tuple(row.get("match_sources") or ()),
                vector_rank=row.get("vector_rank"),
                fts_rank=row.get("fts_rank"),
                rrf_score=row.get("rrf_score"),
                vector_similarity=row.get("vector_similarity"),
            )
        )
    return hits


async def hybrid_search_cached(
    query: str,
    org_id: str,
    client: Client,
    *,
    k: int = DEFAULT_FINAL_K,
    document_id: str | None = None,
    document_ids: list[str] | None = None,
) -> list[SearchHit]:
    """Drop-in replacement for `hybrid_search` with versioned-key caching.

    Always returns a list (possibly empty). Never raises on cache errors —
    falls through to a fresh search on any Upstash hiccup.
    """
    # Don't cache document-scoped searches with a SINGLE pinned doc — the
    # cache hit rate is low (each conversation has its own doc) and the search
    # is already filtered down to a small corpus so it's cheap to run live.
    # We DO cache multi-doc (tag-scope) searches because those are shared.
    if document_id and not document_ids:
        return await hybrid_search(
            query,
            org_id,
            client,
            k=k,
            document_id=document_id,
            document_ids=document_ids,
        )

    version = await get_cache_version(org_id)
    scope_sig = _scope_signature(document_id, document_ids)
    key_hash = hash_for_key(query.strip(), scope_sig, k)
    cache_key = f"search:{org_id}:v{version}:{key_hash}"

    cached = await cache_get_json(cache_key)
    if isinstance(cached, list):
        rehydrated = await _rehydrate_hits(cached, org_id=org_id, client=client)
        if rehydrated is not None:
            log.debug("search_cache_hit", org_id=org_id, query_len=len(query))
            return rehydrated

    log.debug("search_cache_miss", org_id=org_id, query_len=len(query))
    hits = await hybrid_search(
        query,
        org_id,
        client,
        k=k,
        document_id=document_id,
        document_ids=document_ids,
    )

    # Serialise + write-through. Empty results get a shorter TTL so a fresh
    # document upload becomes visible to chat within ~10s, not 60s.
    serialised = [_hit_to_cacheable(h) for h in hits]
    ttl_base = _SEARCH_NEGATIVE_TTL_SECONDS if not hits else _SEARCH_TTL_SECONDS
    await cache_set_json(cache_key, serialised, ttl_seconds=jittered_ttl(ttl_base))
    return hits
