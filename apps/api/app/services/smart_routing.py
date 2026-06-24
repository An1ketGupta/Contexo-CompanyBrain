"""Smart document routing — auto-suggested collection assignments (Agent2 Day 2 #33).

Collections in this codebase are tag-derived: a document is "in" collection C
iff ``documents.tags && C.tag_filters`` overlaps. So Smart Routing doesn't
need a new membership table; it proposes a *tag addition* on the document
that, once accepted, makes the membership derive automatically.

Algorithm:
    1. For each collection in the org, compute its centroid embedding =
       L2-normalized mean of ``documents.summary_embedding`` across its
       current members. Cached for 30 minutes per org to avoid recomputing
       for every doc upload.
    2. For the doc being routed, compute cosine similarity vs every centroid.
    3. The best-matching collection above DEFAULT_THRESHOLD becomes a
       ``routing_suggestions`` row with the collection's primary tag (the
       first entry in ``tag_filters``) as the proposed addition.

Why not LLM-classify the doc directly:
    Embeddings are already computed (summary_embedding from #14). Centroid
    cosine is essentially free at query time vs. a per-doc LLM classification
    call. The auto_tagger already covers "what topic is this about?" via
    LLM — Smart Routing focuses specifically on "does this fit one of the
    org's curated collections?" which is what centroids are good at.
"""
from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

from app.database import get_service_client
from app.services.langfuse import observe
from app.services.redis_cache import cache_get_json, cache_set_json

log = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 0.80
_CENTROID_CACHE_TTL = 60 * 30  # 30 min — invalidated implicitly by per-doc routing


def _centroid_cache_key(org_id: str) -> str:
    return f"smart_routing:centroids:{org_id}"


@observe(name="smart_routing.suggest_for_document")
async def suggest_for_document(
    *,
    document_id: str,
    org_id: str,
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    """Score a doc against every collection centroid; persist the top match.

    Returns the inserted/updated routing_suggestions row, or status='no_match'.
    Idempotent: if a pending suggestion for the same (doc, collection) pair
    already exists, the unique index lets the upsert no-op silently.
    """
    embedding = await _fetch_doc_embedding(document_id=document_id, org_id=org_id)
    if not embedding:
        return {"status": "no_embedding", "document_id": document_id}

    centroids = await _get_centroids(org_id=org_id)
    if not centroids:
        return {"status": "no_collections", "document_id": document_id}

    best: dict[str, Any] | None = None
    for c in centroids:
        sim = _cosine(embedding, c["centroid"])
        if sim < threshold:
            continue
        if best is None or sim > best["similarity"]:
            best = {**c, "similarity": sim}

    if best is None:
        return {"status": "no_match", "document_id": document_id}

    suggested_tag = (best.get("tag_filters") or [None])[0]
    if not suggested_tag:
        # Collection has no tags — nothing to propose adding. Treat as no match.
        return {"status": "no_tag_to_add", "document_id": document_id}

    inserted = await _insert_suggestion(
        org_id=org_id,
        document_id=document_id,
        collection_id=best["collection_id"],
        suggested_tag=suggested_tag,
        similarity=best["similarity"],
    )
    return {
        "status": "suggested" if inserted else "already_pending",
        "document_id": document_id,
        "collection_id": best["collection_id"],
        "collection_name": best.get("collection_name"),
        "similarity": best["similarity"],
        "suggested_tag": suggested_tag,
    }


# ── DB + cache helpers ──────────────────────────────────────────────────


async def _fetch_doc_embedding(
    *, document_id: str, org_id: str
) -> list[float] | None:
    svc = get_service_client()

    def _run() -> list[float] | None:
        res = (
            svc.table("documents")
            .select("summary_embedding")
            .eq("id", document_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        if not res or not res.data:
            return None
        emb = res.data.get("summary_embedding")
        # Supabase returns pgvector as a string '[1.2, 3.4, ...]' or as a list,
        # depending on the driver version. Handle both defensively.
        if isinstance(emb, str):
            try:
                import json

                return list(json.loads(emb))
            except Exception:
                return None
        if isinstance(emb, list):
            return [float(x) for x in emb]
        return None

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        log.warning("smart_routing_doc_embed_fetch_failed doc=%s err=%s", document_id, exc)
        return None


async def _get_centroids(*, org_id: str) -> list[dict[str, Any]]:
    """Return per-collection centroid + tag_filters. Cached 30 min.

    Each centroid is the L2-normalized mean of member docs' summary
    embeddings. Collections with zero members or all-NULL embeddings are
    skipped.
    """
    cached = await cache_get_json(_centroid_cache_key(org_id))
    if cached is not None:
        return cached  # type: ignore[return-value]

    svc = get_service_client()

    def _fetch_collections() -> list[dict[str, Any]]:
        res = (
            svc.table("collections")
            .select("id, name, tag_filters")
            .eq("org_id", org_id)
            .execute()
        )
        return res.data or []

    cols = await asyncio.to_thread(_fetch_collections)
    centroids: list[dict[str, Any]] = []
    for col in cols:
        tag_filters = col.get("tag_filters") or []
        if not tag_filters:
            continue
        # Pull summary embeddings of docs whose tags overlap the filter set.
        # We do this in Python rather than building a server-side aggregation
        # — `vector_avg` requires a Postgres extension we may not have
        # universally available; and the per-org membership count is small.
        members = await _fetch_member_embeddings(org_id=org_id, tag_filters=tag_filters)
        if not members:
            continue
        centroid = _l2_normalize(_mean(members))
        centroids.append(
            {
                "collection_id": col["id"],
                "collection_name": col["name"],
                "tag_filters": tag_filters,
                "centroid": centroid,
                "member_count": len(members),
            }
        )

    await cache_set_json(
        _centroid_cache_key(org_id), centroids, ttl_seconds=_CENTROID_CACHE_TTL
    )
    return centroids


async def _fetch_member_embeddings(
    *, org_id: str, tag_filters: list[str]
) -> list[list[float]]:
    svc = get_service_client()

    def _run() -> list[list[float]]:
        # PostgREST `overlaps` operator maps to `&&`. We select only docs
        # that already have a summary_embedding to avoid pulling rows that'd
        # contribute nothing to the centroid.
        res = (
            svc.table("documents")
            .select("summary_embedding")
            .eq("org_id", org_id)
            .eq("status", "ready")
            .not_.is_("summary_embedding", "null")
            .overlaps("tags", tag_filters)
            .limit(200)
            .execute()
        )
        rows = res.data or []
        out: list[list[float]] = []
        for r in rows:
            emb = r.get("summary_embedding")
            if isinstance(emb, str):
                import json as _json

                try:
                    out.append(list(_json.loads(emb)))
                except Exception:
                    continue
            elif isinstance(emb, list):
                out.append([float(x) for x in emb])
        return out

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        log.warning(
            "smart_routing_member_embed_fetch_failed org=%s err=%s", org_id, exc
        )
        return []


async def _insert_suggestion(
    *,
    org_id: str,
    document_id: str,
    collection_id: str,
    suggested_tag: str,
    similarity: float,
) -> bool:
    """Insert into routing_suggestions. Idempotent on the partial unique index.

    Returns True if a new row was created, False if a pending suggestion for
    the same (doc, collection) already exists.
    """
    svc = get_service_client()

    def _run() -> bool:
        try:
            res = (
                svc.table("routing_suggestions")
                .insert(
                    {
                        "org_id": org_id,
                        "document_id": document_id,
                        "collection_id": collection_id,
                        "suggested_tag": suggested_tag,
                        "similarity": similarity,
                    }
                )
                .execute()
            )
            return bool(res.data)
        except Exception as exc:
            # Unique violation = pending suggestion exists. Treat as a no-op.
            if "duplicate key" in str(exc).lower() or "23505" in str(exc):
                return False
            raise

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        log.warning(
            "smart_routing_insert_failed doc=%s coll=%s err=%s",
            document_id,
            collection_id,
            exc,
        )
        return False


# ── Math ────────────────────────────────────────────────────────────────


def _mean(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    n = len(vectors[0])
    acc = [0.0] * n
    for v in vectors:
        if len(v) != n:
            continue
        for i, x in enumerate(v):
            acc[i] += x
    m = len(vectors)
    return [x / m for x in acc]


def _l2_normalize(v: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in v))
    if norm == 0:
        return v
    return [x / norm for x in v]


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ── Cache invalidation hook ─────────────────────────────────────────────


async def invalidate_centroids(*, org_id: str) -> None:
    """Drop the centroid cache for an org.

    Call from anywhere collection membership might shift: collection edit,
    new doc with tags, accepted routing suggestion. Cheap (one Redis del).
    """
    from app.services.redis_cache import cache_delete

    try:
        await cache_delete(_centroid_cache_key(org_id))
    except Exception as exc:
        log.warning("smart_routing_invalidate_failed org=%s err=%s", org_id, exc)


# ── Decision endpoints (called from the admin router) ────────────────────


async def accept_suggestion(*, suggestion_id: str, accepted_by: str) -> dict[str, Any]:
    """Mark accepted + push the suggested tag onto the document's tags array.

    Postgres ``ARRAY_APPEND`` keeps the existing tags intact; the unique
    index on tags is at the GIN-array level so a duplicate tag is harmless
    but we de-dupe pre-update to keep the column clean.
    """
    svc = get_service_client()

    def _run() -> dict[str, Any] | None:
        # Read the suggestion.
        sugg = (
            svc.table("routing_suggestions")
            .select("*")
            .eq("id", suggestion_id)
            .maybe_single()
            .execute()
        )
        if not sugg or not sugg.data or sugg.data["status"] != "pending":
            return None
        s = sugg.data

        # Read current doc tags, then push the suggested tag if absent.
        doc = (
            svc.table("documents")
            .select("tags")
            .eq("id", s["document_id"])
            .single()
            .execute()
        )
        tags = list(doc.data.get("tags") or [])
        if s["suggested_tag"] not in tags:
            tags.append(s["suggested_tag"])
            svc.table("documents").update({"tags": tags}).eq("id", s["document_id"]).execute()

        # Mark the suggestion accepted.
        svc.table("routing_suggestions").update(
            {
                "status": "accepted",
                "decided_by": accepted_by,
                "decided_at": "now()",
            }
        ).eq("id", suggestion_id).execute()

        return {
            "suggestion_id": suggestion_id,
            "document_id": s["document_id"],
            "collection_id": s["collection_id"],
            "added_tag": s["suggested_tag"],
        }

    result = await asyncio.to_thread(_run)
    if result:
        # Tag membership changed → centroid drift → drop the cache.
        await invalidate_centroids(org_id=(await _org_id_of_suggestion(suggestion_id)) or "")
    return result or {"status": "not_found"}


async def reject_suggestion(*, suggestion_id: str, rejected_by: str) -> dict[str, Any]:
    svc = get_service_client()

    def _run() -> dict[str, Any] | None:
        res = (
            svc.table("routing_suggestions")
            .update(
                {
                    "status": "rejected",
                    "decided_by": rejected_by,
                    "decided_at": "now()",
                }
            )
            .eq("id", suggestion_id)
            .eq("status", "pending")
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None

    row = await asyncio.to_thread(_run)
    return row or {"status": "not_found"}


async def _org_id_of_suggestion(suggestion_id: str) -> str | None:
    svc = get_service_client()

    def _run() -> str | None:
        res = (
            svc.table("routing_suggestions")
            .select("org_id")
            .eq("id", suggestion_id)
            .maybe_single()
            .execute()
        )
        return (res.data or {}).get("org_id") if res else None

    try:
        return await asyncio.to_thread(_run)
    except Exception:
        return None
