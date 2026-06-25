"""Knowledge Graph snapshot builder (Feature 2.1).

Computes a clustered map of an org's documents by their `summary_embedding`,
labels each cluster with a short LLM-generated name, and persists the result
to `knowledge_graph_snapshots`. The admin /knowledge-graph page reads the
latest `status='ok'` row.

We implement k-means in pure Python — numpy isn't a dependency and the
matrices stay small (≤ a few thousand docs × 768 dims × ≤12 clusters × 20
iterations). For larger orgs we'd want a real numerical lib, but at that
point we'd also want approximate / online clustering anyway.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import random
from datetime import UTC, datetime
from typing import Any

from app.database import get_service_client
from app.services.llm import Message, get_llm_client

log = logging.getLogger(__name__)


# Cluster-naming prompt. The model gets a label + N representative document
# titles + summaries; it must return JSON with a short label and a one-line
# description of the cluster's theme. Capped at 8 docs per cluster in the
# prompt because the model only needs a sense of the theme, not exhaustion.
_NAMING_PROMPT = """You are labeling a cluster of related documents from a company's knowledge base. Given the document titles and summaries below, respond with ONLY a JSON object — no markdown fences, no preamble.

Format:
{"label": "<2-4 word title>", "description": "<one-sentence theme>", "color": "<hex like #4F46E5>"}

Guidelines:
- The label should describe what the cluster is about, not generic ("Product Specs", not "Documents").
- Pick a color from this palette only: #6366F1, #EC4899, #10B981, #F59E0B, #8B5CF6, #06B6D4, #EF4444, #14B8A6, #F97316, #84CC16, #A855F7, #0EA5E9.
- The description must be one sentence, max 120 chars, in title case-ish prose.

Cluster {n} documents:
"""

# Tuning. These constants are pragmatic, not optimal — the goal is a
# visually-usable graph that updates nightly, not a research-grade clustering.
MAX_DOCS = 2_500          # hard cap per snapshot — orgs larger than this still
                          #   get a meaningful sample (most-recent-N by created_at)
MIN_DOCS = 6              # below this, snapshot returns one "All docs" cluster
KMEANS_ITERS = 20
NAMING_DOCS_PER_CLUSTER = 8
FALLBACK_COLORS = (
    "#6366F1", "#EC4899", "#10B981", "#F59E0B",
    "#8B5CF6", "#06B6D4", "#EF4444", "#14B8A6",
    "#F97316", "#84CC16", "#A855F7", "#0EA5E9",
)


# ── Public entrypoint ──────────────────────────────────────────────────────


async def refresh_org_snapshot(org_id: str) -> dict[str, Any]:
    """Build a fresh snapshot for the given org. Returns the inserted row.

    Idempotent in spirit — every call appends a new row; the read path picks
    the latest `status='ok'`. Old snapshots are pruned by
    `prune_old_kg_snapshots()`, called from the cron after the per-org work.
    """
    svc = get_service_client()

    started_at = datetime.now(UTC).isoformat()

    # Step 1: pull docs with summary_embedding present.
    docs = await _load_docs_with_embeddings(org_id)
    if len(docs) < MIN_DOCS:
        return await _persist_trivial_snapshot(
            org_id=org_id,
            docs=docs,
            started_at=started_at,
        )

    # Step 2: choose k. Heuristic: sqrt(N/2) capped at 12 — enough to feel
    # like a graph without overwhelming the legend or the LLM-name budget.
    k = max(3, min(12, int(round(math.sqrt(len(docs) / 2)))))

    # Step 3: run k-means. `assignments` maps doc index → cluster index.
    embeddings = [d["embedding"] for d in docs]
    centroids, assignments = _kmeans(embeddings, k=k, iters=KMEANS_ITERS)

    # Step 4: build per-cluster member lists + representative picks.
    members_by_cluster: dict[int, list[int]] = {i: [] for i in range(k)}
    for idx, c in enumerate(assignments):
        members_by_cluster[c].append(idx)

    # Step 5: LLM-name each cluster. Best-effort; fall back to "Cluster N" so
    # one timeout doesn't kill the whole snapshot.
    cluster_names = await _name_clusters(
        docs=docs,
        members_by_cluster=members_by_cluster,
    )

    # Step 6: assemble payload.
    clusters_json: list[dict[str, Any]] = []
    nodes_json: list[dict[str, Any]] = []
    for ci in range(k):
        member_idxs = members_by_cluster.get(ci, [])
        if not member_idxs:
            continue
        # Centroid neighbors — the closest 3 doc IDs serve as a quick preview
        # in the UI panel. They're picked by re-running cosine against the
        # cluster centroid (cheap; member-count is tiny per cluster).
        ranked = _rank_by_centroid(
            indexes=member_idxs,
            embeddings=embeddings,
            centroid=centroids[ci],
        )
        preview_ids = [docs[i]["id"] for i in ranked[:3]]

        meta = cluster_names.get(ci, {})
        color = meta.get("color") or FALLBACK_COLORS[ci % len(FALLBACK_COLORS)]
        clusters_json.append({
            "id": f"c{ci}",
            "label": meta.get("label") or f"Cluster {ci + 1}",
            "description": meta.get("description") or "",
            "color": color,
            "doc_count": len(member_idxs),
            "preview_doc_ids": preview_ids,
        })

        for idx in member_idxs:
            d = docs[idx]
            nodes_json.append({
                "id": d["id"],
                "name": d["name"],
                "cluster_id": f"c{ci}",
                "file_type": d.get("file_type"),
                "health_label": d.get("health_label") or "unscored",
                "citation_count": d.get("citation_count") or 0,
            })

    # Step 7: insert the snapshot row.
    payload = {
        "org_id": org_id,
        "doc_count": len(docs),
        "cluster_count": len(clusters_json),
        "status": "ok",
        "clusters": clusters_json,
        "nodes": nodes_json,
        # Edges left empty for now — the graph is laid out by cluster color +
        # force simulation, which doesn't need explicit links.
        "edges": [],
        "params": {
            "k": k,
            "iters": KMEANS_ITERS,
            "embedding_model": "google-text-embedding-004",
            "started_at": started_at,
            "completed_at": datetime.now(UTC).isoformat(),
        },
    }

    def _insert() -> Any:
        return (
            svc.table("knowledge_graph_snapshots")
            .insert(payload)
            .execute()
        )

    res = await asyncio.to_thread(_insert)
    inserted = (res.data or [None])[0]

    # Step 8: prune old snapshots (best-effort; never fail the refresh on this).
    try:
        await asyncio.to_thread(lambda: svc.rpc("prune_old_kg_snapshots").execute())
    except Exception as exc:
        log.warning("kg_prune_failed org=%s err=%s", org_id, exc)

    log.info(
        "kg_snapshot_ok org=%s docs=%d clusters=%d",
        org_id,
        len(docs),
        len(clusters_json),
    )
    return inserted or payload


# ── Data loading ───────────────────────────────────────────────────────────


async def _load_docs_with_embeddings(org_id: str) -> list[dict[str, Any]]:
    """Pull every ready doc with a `summary_embedding`.

    The embedding column is `vector(768)` in Postgres but supabase-py returns
    it as a JSON string ("[0.123, …]"). We parse and normalize once here so
    downstream code works on plain `list[float]`.
    """
    svc = get_service_client()

    def _query() -> list[dict[str, Any]]:
        res = (
            svc.table("documents")
            .select(
                "id, name, file_type, health_label, citation_count, "
                "created_at, summary_embedding, metadata"
            )
            .eq("org_id", org_id)
            .eq("status", "ready")
            .not_.is_("summary_embedding", "null")
            .order("created_at", desc=True)
            .limit(MAX_DOCS)
            .execute()
        )
        return list(res.data or [])

    rows = await asyncio.to_thread(_query)

    out: list[dict[str, Any]] = []
    for r in rows:
        emb = _coerce_embedding(r.get("summary_embedding"))
        if emb is None:
            continue
        # Pull summary from metadata.summary if present — used by the
        # naming LLM pass. Cheap defensive cast in case metadata is null.
        meta = r.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {}
        out.append({
            "id": r["id"],
            "name": r["name"],
            "file_type": r.get("file_type"),
            "health_label": r.get("health_label"),
            "citation_count": r.get("citation_count") or 0,
            "embedding": emb,
            "summary": meta.get("summary") or "",
        })
    return out


def _coerce_embedding(value: Any) -> list[float] | None:
    """Accept either a list, a JSON-encoded list, or None and return list[float]."""
    if value is None:
        return None
    if isinstance(value, list):
        try:
            return [float(x) for x in value]
        except (TypeError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return None
        if isinstance(parsed, list):
            try:
                return [float(x) for x in parsed]
            except (TypeError, ValueError):
                return None
    return None


# ── K-means ────────────────────────────────────────────────────────────────


def _kmeans(
    points: list[list[float]], *, k: int, iters: int
) -> tuple[list[list[float]], list[int]]:
    """Vanilla k-means++ initialization + Lloyd's iterations.

    Returns (centroids, assignments). For small k and dims=768 this runs
    in O(N * k * dims) per iter — fine for N up to ~3000.
    """
    if not points:
        return [], []
    n = len(points)
    if k >= n:
        # Trivial — each point is its own cluster.
        return [list(p) for p in points], list(range(n))

    rng = random.Random(0xC0FFEE)
    # k-means++ init: first centroid random, subsequent ones biased toward
    # points far from existing centroids.
    centroids: list[list[float]] = [list(points[rng.randrange(n)])]
    while len(centroids) < k:
        # Distance² from each point to the nearest existing centroid.
        d2 = [
            min(_dist_sq(p, c) for c in centroids)
            for p in points
        ]
        total = sum(d2)
        if total <= 0:
            # All remaining points coincide with centroids; pick at random.
            centroids.append(list(points[rng.randrange(n)]))
            continue
        r = rng.random() * total
        acc = 0.0
        for i, w in enumerate(d2):
            acc += w
            if acc >= r:
                centroids.append(list(points[i]))
                break

    assignments = [0] * n
    for _it in range(iters):
        # Assignment step.
        changed = False
        for i, p in enumerate(points):
            best_c, best_d = 0, float("inf")
            for ci, c in enumerate(centroids):
                d = _dist_sq(p, c)
                if d < best_d:
                    best_c, best_d = ci, d
            if assignments[i] != best_c:
                assignments[i] = best_c
                changed = True
        if not changed:
            break

        # Update step.
        sums: list[list[float]] = [[0.0] * len(points[0]) for _ in range(k)]
        counts = [0] * k
        for i, p in enumerate(points):
            ci = assignments[i]
            counts[ci] += 1
            row = sums[ci]
            for j, v in enumerate(p):
                row[j] += v
        for ci in range(k):
            if counts[ci] == 0:
                # Empty cluster — re-seed to a random point.
                centroids[ci] = list(points[rng.randrange(n)])
            else:
                inv = 1.0 / counts[ci]
                centroids[ci] = [v * inv for v in sums[ci]]
    return centroids, assignments


def _dist_sq(a: list[float], b: list[float]) -> float:
    total = 0.0
    for i in range(len(a)):
        d = a[i] - b[i]
        total += d * d
    return total


def _rank_by_centroid(
    *, indexes: list[int], embeddings: list[list[float]], centroid: list[float]
) -> list[int]:
    """Sort `indexes` by ascending distance² to `centroid`."""
    return sorted(indexes, key=lambda i: _dist_sq(embeddings[i], centroid))


# ── LLM cluster naming ─────────────────────────────────────────────────────


async def _name_clusters(
    *,
    docs: list[dict[str, Any]],
    members_by_cluster: dict[int, list[int]],
) -> dict[int, dict[str, str]]:
    """Generate one label per cluster. Returns {cluster_idx: {label, description, color}}."""

    async def name_one(ci: int, member_idxs: list[int]) -> tuple[int, dict[str, str]]:
        if not member_idxs:
            return ci, {}
        sample = member_idxs[:NAMING_DOCS_PER_CLUSTER]
        lines: list[str] = []
        for idx in sample:
            d = docs[idx]
            title = (d.get("name") or "").strip()[:80]
            summary = (d.get("summary") or "").strip()[:240]
            lines.append(f"- {title}: {summary or '(no summary)'}")
        prompt_body = _NAMING_PROMPT.format(n=ci + 1) + "\n".join(lines)
        try:
            client = get_llm_client()
            res = await client.complete(
                messages=[Message(role="user", content=prompt_body)],
                temperature=0.1,
                timeout=20.0,
            )
            parsed = _parse_naming_response(getattr(res, "text", "") or "")
            return ci, parsed
        except Exception as exc:
            log.warning("kg_name_cluster_failed ci=%d err=%s", ci, exc)
            return ci, {}

    # Sequential — k is small (≤12), keeps free-tier rate limits happy.
    out: dict[int, dict[str, str]] = {}
    for ci, member_idxs in members_by_cluster.items():
        ci_, meta = await name_one(ci, member_idxs)
        out[ci_] = meta
    return out


def _parse_naming_response(text: str) -> dict[str, str]:
    """Robust-ish JSON extraction. Strips markdown fences, takes first {...}."""
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`")
        nl = body.find("\n")
        if nl > 0:
            body = body[nl + 1:]
    start = body.find("{")
    end = body.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        obj = json.loads(body[start: end + 1])
    except (TypeError, ValueError):
        return {}
    if not isinstance(obj, dict):
        return {}
    label = str(obj.get("label") or "").strip()[:60]
    description = str(obj.get("description") or "").strip()[:160]
    color = str(obj.get("color") or "").strip()
    out: dict[str, str] = {}
    if label:
        out["label"] = label
    if description:
        out["description"] = description
    if color and color.startswith("#"):
        out["color"] = color
    return out


# ── Trivial snapshot (org too small to cluster) ────────────────────────────


async def _persist_trivial_snapshot(
    *,
    org_id: str,
    docs: list[dict[str, Any]],
    started_at: str,
) -> dict[str, Any]:
    """For orgs with too few docs, write a single-cluster snapshot. Lets
    the UI render meaningfully instead of an error state."""
    svc = get_service_client()
    nodes = [
        {
            "id": d["id"],
            "name": d["name"],
            "cluster_id": "c0",
            "file_type": d.get("file_type"),
            "health_label": d.get("health_label") or "unscored",
            "citation_count": d.get("citation_count") or 0,
        }
        for d in docs
    ]
    clusters = [{
        "id": "c0",
        "label": "All documents",
        "description": "Too few documents to cluster meaningfully — add more to enable a richer map.",
        "color": FALLBACK_COLORS[0],
        "doc_count": len(docs),
        "preview_doc_ids": [d["id"] for d in docs[:3]],
    }] if docs else []
    payload = {
        "org_id": org_id,
        "doc_count": len(docs),
        "cluster_count": len(clusters),
        "status": "ok",
        "clusters": clusters,
        "nodes": nodes,
        "edges": [],
        "params": {
            "k": 1 if clusters else 0,
            "started_at": started_at,
            "completed_at": datetime.now(UTC).isoformat(),
            "reason": "too_few_docs",
        },
    }
    res = await asyncio.to_thread(
        lambda: svc.table("knowledge_graph_snapshots").insert(payload).execute()
    )
    return (res.data or [None])[0] or payload
