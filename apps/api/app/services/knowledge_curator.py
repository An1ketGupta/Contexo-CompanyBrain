"""Knowledge Curator Agent — weekly autonomous scan (Agent2 Day 3 #24).

This module is the orchestrator behind the weekly Knowledge Health Report.
It does four things, all read-only against the corpus except for inserting
draft stubs into the existing ``document_drafts`` review queue:

    1. find_outdated_documents       — flag docs older than the org's
                                       configured cutoff.
    2. find_broken_links             — SSRF-guarded HEAD probes against
                                       http(s) URLs in document summaries.
    3. find_merge_clusters           — pairwise similarity via Day 2's
                                       find_similar_documents RPC at a lower
                                       threshold than upload-time duplicate
                                       detection. Clusters via union-find.
    4. find_knowledge_gaps           — combine the existing knowledge_gaps
                                       table with `query_logs.source_count=0`
                                       in the last 7 days, deduped by topic.
    5. draft_gap_stubs               — for the top N un-drafted gaps, ask
                                       the LLM to write a stub via
                                       execute_task and write to the
                                       existing `document_drafts` table.

Why we orchestrate rather than fanning into separate Inngest steps:
    The whole scan is bounded — a few RPC calls + N HEAD probes + a handful
    of LLM calls. Total time is comfortably under a single Inngest step
    duration even for a large org. Splitting into multiple steps would mean
    inventing inter-step state coordination (which findings have been
    rolled up?) for no resilience win — the entire scan re-runs cleanly on
    retry because we always write a fresh `knowledge_health_reports` row.

Idempotency / retries:
    * Outdated/broken/merge findings are derived purely from current DB
      state — re-running just produces the same output.
    * Draft creation is protected by the unique partial index
      `uq_document_drafts_pending_topic` (migration 027). A racing retry
      that tries to write a second draft for the same gap_topic gets
      rejected at the index level; we treat that as success.

Cost guards:
    * Broken-link probes are capped at MAX_BROKEN_LINK_CHECKS per scan and
      run with a small concurrency limit + tight per-request timeout to
      protect against pathological documents (1000+ URLs).
    * Stub drafting is capped at MAX_STUBS_PER_SCAN per scan so a backlog
      of 200 gaps doesn't generate 200 Gemini calls in a single morning.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.database import get_service_client
from app.services.langfuse import observe
from app.services.network_security import UnsafeURLError, validate_outbound_url

log = logging.getLogger(__name__)


# ── Cost / sanity caps ───────────────────────────────────────────────────

# Outdated detection caps how many flagged rows we surface per scan.
# The dashboard pages — admins won't scroll past 50.
MAX_OUTDATED_RESULTS = 50

# Broken-link checking is the most expensive part of the scan. Two caps:
#   - MAX_BROKEN_LINK_CHECKS: hard ceiling on URLs we probe per scan.
#   - BROKEN_LINK_CONCURRENCY: in-flight HEAD requests at once.
# Per-request timeout deliberately tight (5s); customer docs reference real
# user-facing URLs, not slow internal services.
MAX_BROKEN_LINK_CHECKS = 200
BROKEN_LINK_CONCURRENCY = 8
BROKEN_LINK_TIMEOUT_SECONDS = 5.0

# Merge-cluster discovery caps how many source docs we expand similarity
# from. With M docs and N matches each we get at most M*N pairs to union-
# find, and we want the per-scan compute to stay bounded.
MAX_DOCS_FOR_MERGE_SCAN = 500
MAX_MERGE_MATCHES_PER_DOC = 5

# Knowledge gap window — match the existing knowledge_gap_threshold window
# (7 days) for consistency with the existing pipeline.
GAP_WINDOW_DAYS = 7

# Cap the number of stubs we draft in one scan. Each stub is one LLM call.
MAX_STUBS_PER_SCAN = 5


# ── Orchestrator ─────────────────────────────────────────────────────────


@observe(name="knowledge_curator.run_scan")
async def run_curator_scan(
    *,
    org_id: str,
    triggered_by: str | None = None,
) -> dict[str, Any]:
    """Run a full Curator scan for one org. Returns the report payload.

    Always writes a `knowledge_health_reports` row — pending at start, then
    completed/failed on exit. The caller (Inngest worker) doesn't need to
    track scan state separately; the row IS the state.
    """
    svc = get_service_client()
    cfg = await _fetch_org_config(org_id)
    if not cfg.get("curator_enabled", True):
        log.info("knowledge_curator.disabled org_id=%s", org_id)
        return {"status": "skipped", "reason": "disabled"}

    report_id = await _create_pending_report(svc, org_id=org_id, triggered_by=triggered_by)

    try:
        outdated = await find_outdated_documents(
            org_id=org_id, cutoff_days=int(cfg["curator_outdated_days"])
        )
        merge_clusters = await find_merge_clusters(
            org_id=org_id, threshold=float(cfg["curator_merge_threshold"])
        )
        gaps = await find_knowledge_gaps(org_id=org_id, window_days=GAP_WINDOW_DAYS)

        # Broken-link probing is bypassable per-org because it can be noisy
        # for orgs whose docs reference intranet URLs.
        if cfg.get("curator_check_broken_links", True):
            broken_links = await find_broken_links(org_id=org_id)
        else:
            broken_links = []

        # Drafting stubs is the last and most expensive step. If it fails
        # we still want the rest of the report saved, so we wrap separately.
        try:
            drafted_stubs = await draft_gap_stubs(org_id=org_id, gaps=gaps)
        except Exception as exc:
            log.exception("knowledge_curator.stub_drafting_failed org_id=%s err=%s", org_id, exc)
            drafted_stubs = []

        payload = {
            "outdated_documents": outdated,
            "broken_links": broken_links,
            "merge_clusters": merge_clusters,
            "knowledge_gaps": gaps,
            "drafted_stubs": drafted_stubs,
            "counts": {
                "outdated": len(outdated),
                "broken_links": len(broken_links),
                "merge_clusters": len(merge_clusters),
                "gaps": len(gaps),
                "stubs": len(drafted_stubs),
            },
            "config_snapshot": {
                "outdated_days": int(cfg["curator_outdated_days"]),
                "merge_threshold": float(cfg["curator_merge_threshold"]),
                "broken_link_check": bool(cfg.get("curator_check_broken_links", True)),
            },
        }

        await _mark_report_completed(svc, report_id=report_id, payload=payload)
        await _notify_admins_of_report(org_id=org_id, report_id=report_id, payload=payload)
        return {"status": "completed", "report_id": report_id, **payload["counts"]}
    except Exception as exc:
        log.exception("knowledge_curator.scan_failed org_id=%s err=%s", org_id, exc)
        await _mark_report_failed(svc, report_id=report_id, error=str(exc)[:2000])
        raise


# ── Outdated documents ───────────────────────────────────────────────────


async def find_outdated_documents(
    *,
    org_id: str,
    cutoff_days: int,
) -> list[dict[str, Any]]:
    """List documents whose `created_at` is older than ``cutoff_days``.

    The schema doesn't carry an updated_at on documents — created_at is the
    closest proxy (version uploads create new rows via document_versions).
    Excludes documents still pending/processing (no point flagging
    half-ingested files as 'outdated') and documents soft-deleted via the
    is_archived flag where present.
    """
    cutoff = (datetime.now(UTC) - timedelta(days=cutoff_days)).isoformat()
    svc = get_service_client()

    def _run() -> list[dict[str, Any]]:
        res = (
            svc.table("documents")
            .select("id, name, created_at, source, status")
            .eq("org_id", org_id)
            .eq("status", "ready")
            .lte("created_at", cutoff)
            .order("created_at", desc=False)
            .limit(MAX_OUTDATED_RESULTS)
            .execute()
        )
        return res.data or []

    rows = await asyncio.to_thread(_run)
    now = datetime.now(UTC)
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            updated = datetime.fromisoformat((r["created_at"] or "").replace("Z", "+00:00"))
            age_days = max(0, (now - updated).days)
        except (KeyError, ValueError):
            age_days = None
        out.append(
            {
                "id": r["id"],
                "name": r.get("name"),
                "updated_at": r.get("created_at"),
                "source": r.get("source"),
                "age_days": age_days,
            }
        )
    return out


# ── Broken links ─────────────────────────────────────────────────────────

# Match http(s) URLs in document content. Trailing punctuation (",.;!?)")
# is stripped manually so prose like "see https://example.com." doesn't
# break the URL.
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_URL_TRAILING_PUNCT = ".,;:!?\")]"


def _extract_urls(text: str | None) -> list[str]:
    if not text:
        return []
    found = []
    for m in _URL_RE.finditer(text):
        url = m.group(0)
        while url and url[-1] in _URL_TRAILING_PUNCT:
            url = url[:-1]
        if url:
            found.append(url)
    return found


async def find_broken_links(*, org_id: str) -> list[dict[str, Any]]:
    """HEAD-probe http(s) URLs found in document summaries. SSRF-guarded.

    Why summaries-only and not full chunks: the summary is a compact
    representation of the doc; URLs in the summary are typically the
    important canonical references (the policy URL, the playbook source).
    Scanning every chunk would mean probing thousands of URLs per scan,
    most of them transient links inside the body text. P2: opt-in
    "deep link scan" that walks all chunks.
    """
    svc = get_service_client()

    def _load_documents() -> list[dict[str, Any]]:
        # documents.metadata.summary is where the auto-summary text lives.
        # We don't ALTER ORDER BY because we want a stable URL sample —
        # we'll dedupe URLs across documents anyway.
        res = (
            svc.table("documents")
            .select("id, name, metadata")
            .eq("org_id", org_id)
            .eq("status", "ready")
            .limit(1000)  # ceiling; even 1000 docs is < 200 ms in Postgres
            .execute()
        )
        return res.data or []

    docs = await asyncio.to_thread(_load_documents)

    # Build the list of (document_id, document_name, url) tuples, dedupe
    # by URL so we don't probe https://google.com 50 times when it's cited
    # in many docs. Keep the first sighting's doc_id for surfacing.
    candidates: list[tuple[str, str | None, str]] = []
    seen: dict[str, tuple[str, str | None]] = {}
    for d in docs:
        meta = d.get("metadata") or {}
        summary = meta.get("summary") if isinstance(meta, dict) else None
        for url in _extract_urls(summary):
            if url in seen:
                continue
            seen[url] = (d["id"], d.get("name"))
            candidates.append((d["id"], d.get("name"), url))
            if len(candidates) >= MAX_BROKEN_LINK_CHECKS:
                break
        if len(candidates) >= MAX_BROKEN_LINK_CHECKS:
            break

    if not candidates:
        return []

    sem = asyncio.Semaphore(BROKEN_LINK_CONCURRENCY)

    async def _check_one(doc_id: str, doc_name: str | None, url: str) -> dict[str, Any] | None:
        # SSRF guard up front. If the URL resolves to a private IP we don't
        # touch it — and we don't report it as a "broken" link either,
        # since that would surface internal-network detail to the admin UI.
        try:
            validate_outbound_url(url)
        except UnsafeURLError as exc:
            log.info("knowledge_curator.url_unsafe doc=%s reason=%s", doc_id, exc)
            return None
        async with sem:
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(BROKEN_LINK_TIMEOUT_SECONDS, connect=2.0),
                    follow_redirects=True,
                    headers={"User-Agent": "NirnayaIQ-LinkChecker/1.0"},
                ) as client:
                    # HEAD first; many CDNs respond to HEAD differently than
                    # GET. Fall through to GET if HEAD is rejected (405).
                    resp = await client.head(url)
                    if resp.status_code == 405:
                        resp = await client.get(url)
            except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPError) as exc:
                return {
                    "document_id": doc_id,
                    "document_name": doc_name,
                    "url": url,
                    "status_code": None,
                    "reason": type(exc).__name__,
                }
        if resp.status_code >= 400:
            return {
                "document_id": doc_id,
                "document_name": doc_name,
                "url": url,
                "status_code": resp.status_code,
                "reason": f"http_{resp.status_code}",
            }
        return None

    results = await asyncio.gather(
        *(_check_one(d, n, u) for d, n, u in candidates), return_exceptions=False
    )
    return [r for r in results if r is not None]


# ── Merge candidate clusters ─────────────────────────────────────────────


async def find_merge_clusters(
    *,
    org_id: str,
    threshold: float = 0.75,
) -> list[dict[str, Any]]:
    """Cluster documents whose summary embeddings are within ``threshold``.

    Uses union-find over the edges produced by calling the
    `find_similar_documents` RPC for each candidate source document. The
    RPC already enforces a similarity floor, status='ready', and per-org
    isolation.

    Returns clusters with >= 2 members, sorted by average similarity
    descending so admins see the strongest candidates first.
    """
    svc = get_service_client()

    def _load_doc_ids() -> list[dict[str, Any]]:
        res = (
            svc.table("documents")
            .select("id, name")
            .eq("org_id", org_id)
            .eq("status", "ready")
            .not_.is_("summary_embedding", "null")
            .limit(MAX_DOCS_FOR_MERGE_SCAN)
            .execute()
        )
        return res.data or []

    docs = await asyncio.to_thread(_load_doc_ids)
    if len(docs) < 2:
        return []

    names: dict[str, str | None] = {d["id"]: d.get("name") for d in docs}

    # Union-find. Edges come from RPC calls.
    parent: dict[str, str] = {d["id"]: d["id"] for d in docs}
    similarities: dict[tuple[str, str], float] = {}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # Concurrency cap so a 500-doc org doesn't fan 500 RPC calls at once.
    sem = asyncio.Semaphore(8)

    async def _query_neighbours(doc_id: str) -> list[dict[str, Any]]:
        async with sem:
            def _rpc() -> list[dict[str, Any]]:
                res = svc.rpc(
                    "find_similar_documents",
                    {
                        "p_org_id": org_id,
                        "p_document_id": doc_id,
                        "p_threshold": threshold,
                        "p_limit": MAX_MERGE_MATCHES_PER_DOC,
                    },
                ).execute()
                return res.data or []

            try:
                return await asyncio.to_thread(_rpc)
            except Exception as exc:
                log.warning("knowledge_curator.merge_rpc_failed doc=%s err=%s", doc_id, exc)
                return []

    neighbours_by_doc = await asyncio.gather(*(_query_neighbours(d["id"]) for d in docs))

    for d, matches in zip(docs, neighbours_by_doc):
        a = d["id"]
        for m in matches:
            b = m["doc_id"]
            if b not in parent:
                # Match returned a doc outside our top-MAX_DOCS_FOR_MERGE_SCAN
                # — skip it for cluster purposes; we'll still link via its own
                # row if it falls within the cap.
                continue
            key = (min(a, b), max(a, b))
            similarities[key] = max(similarities.get(key, 0.0), float(m["similarity"]))
            union(a, b)

    # Group by root, drop singletons, compute avg pairwise similarity.
    groups: dict[str, list[str]] = defaultdict(list)
    for doc_id in parent:
        groups[find(doc_id)].append(doc_id)

    clusters: list[dict[str, Any]] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        # Average over the edges we actually observed within this cluster.
        edge_sims = []
        member_set = set(members)
        for (a, b), sim in similarities.items():
            if a in member_set and b in member_set:
                edge_sims.append(sim)
        avg = sum(edge_sims) / len(edge_sims) if edge_sims else 0.0
        clusters.append(
            {
                "members": [{"id": m, "name": names.get(m)} for m in members],
                "size": len(members),
                "avg_similarity": round(avg, 4),
            }
        )

    clusters.sort(key=lambda c: c["avg_similarity"], reverse=True)
    return clusters


# ── Knowledge gaps ───────────────────────────────────────────────────────


async def find_knowledge_gaps(
    *,
    org_id: str,
    window_days: int = GAP_WINDOW_DAYS,
) -> list[dict[str, Any]]:
    """Aggregate the org's knowledge gaps over the last ``window_days``.

    Combines two signals:
      1. Rows in the dedicated `knowledge_gaps` table (written by the
         knowledge_gap_detected Inngest function).
      2. `query_logs` rows where source_count = 0 (zero retrieval results).

    We dedupe (1) by topic and treat (2) as supplementary noise data —
    we count query_logs rows for the same window to give admins a sense
    of zero-retrieval volume even when no explicit topic was extracted.
    """
    cutoff = (datetime.now(UTC) - timedelta(days=window_days)).isoformat()
    svc = get_service_client()

    def _load_gaps() -> list[dict[str, Any]]:
        res = (
            svc.table("knowledge_gaps")
            .select("topic, query, user_id, conversation_id, created_at")
            .eq("org_id", org_id)
            .gte("created_at", cutoff)
            .limit(2000)
            .execute()
        )
        return res.data or []

    def _zero_source_count() -> int:
        res = (
            svc.table("query_logs")
            .select("id", count="exact", head=True)
            .eq("org_id", org_id)
            .eq("source_count", 0)
            .gte("created_at", cutoff)
            .execute()
        )
        return int(res.count or 0)

    def _has_pending_draft_topics(topics: list[str]) -> set[str]:
        if not topics:
            return set()
        res = (
            svc.table("document_drafts")
            .select("gap_topic")
            .eq("org_id", org_id)
            .eq("status", "pending_review")
            .in_("gap_topic", topics)
            .execute()
        )
        return {r["gap_topic"] for r in (res.data or []) if r.get("gap_topic")}

    gaps = await asyncio.to_thread(_load_gaps)
    zero_source_total = await asyncio.to_thread(_zero_source_count)

    # Dedupe by topic, keep first 3 sample queries.
    topic_buckets: dict[str, dict[str, Any]] = {}
    topic_counter: Counter[str] = Counter()
    for g in gaps:
        topic = (g.get("topic") or "").strip()
        if not topic:
            continue
        topic_counter[topic] += 1
        bucket = topic_buckets.setdefault(
            topic, {"topic": topic, "count": 0, "sample_queries": []}
        )
        bucket["count"] = topic_counter[topic]
        if g.get("query") and len(bucket["sample_queries"]) < 3:
            bucket["sample_queries"].append(g["query"])

    topics = list(topic_buckets.keys())
    drafted_topics: set[str] = set()
    if topics:
        drafted_topics = await asyncio.to_thread(_has_pending_draft_topics, topics)

    out = sorted(topic_buckets.values(), key=lambda b: b["count"], reverse=True)
    for entry in out:
        entry["has_pending_draft"] = entry["topic"] in drafted_topics

    if not out and zero_source_total == 0:
        return []

    # When no explicit topics exist but zero-source queries do, surface a
    # single synthetic row so admins notice retrieval is failing even
    # without the topic extractor naming the gap.
    if not out and zero_source_total > 0:
        out.append(
            {
                "topic": "(uncategorised zero-result queries)",
                "count": zero_source_total,
                "sample_queries": [],
                "has_pending_draft": False,
                "synthetic": True,
            }
        )

    return out


# ── Stub drafting ────────────────────────────────────────────────────────


@observe(name="knowledge_curator.draft_stubs")
async def draft_gap_stubs(
    *,
    org_id: str,
    gaps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Draft stub documents for the highest-volume undrafted gaps.

    Reuses the existing `document_drafts` table and the `execute_task`
    LLM pipeline. Caps at MAX_STUBS_PER_SCAN per scan to control LLM cost
    and avoid drowning the admin review queue.

    Returns a list of {draft_id, topic, gap_count} for each stub written.
    Skips topics that already have a pending draft (the unique partial
    index on document_drafts(org_id, gap_topic) WHERE status='pending_review'
    is the DB-level guard; checking here avoids the LLM call entirely).
    """
    candidates = [
        g for g in gaps
        if not g.get("has_pending_draft")
        and not g.get("synthetic")
        and (g.get("topic") or "").strip()
    ][:MAX_STUBS_PER_SCAN]

    if not candidates:
        return []

    drafted: list[dict[str, Any]] = []
    for gap in candidates:
        try:
            stub = await _generate_stub(
                org_id=org_id, topic=gap["topic"], count=int(gap.get("count") or 0)
            )
        except Exception as exc:
            log.warning(
                "knowledge_curator.stub_generation_failed topic=%s err=%s",
                gap.get("topic"),
                exc,
            )
            continue

        draft_id = await _insert_draft(
            org_id=org_id,
            topic=gap["topic"],
            count=int(gap.get("count") or 0),
            title=stub["title"],
            content=stub["content"],
        )
        if draft_id:
            drafted.append(
                {
                    "draft_id": draft_id,
                    "topic": gap["topic"],
                    "gap_count": int(gap.get("count") or 0),
                }
            )
    return drafted


async def _generate_stub(*, org_id: str, topic: str, count: int) -> dict[str, str]:
    """Use the existing execute_task pipeline so the stub draws on any
    partial context that already exists in the org's KB and surfaces real
    citations the admin can verify against.
    """
    from app.services.llm.task_chain import execute_task_blocking
    from supabase import Client as SupabaseClient

    prompt = (
        f"Write a stub knowledge-base document about: \"{topic}\".\n\n"
        f"Context: the Knowledge Curator detected {count} unanswered queries "
        "about this topic this week. Your job is to draft a document an "
        "internal subject-matter expert can quickly review and edit, not a "
        "finished policy.\n\n"
        "Structure:\n"
        "1. One-line summary of what this doc covers.\n"
        "2. 3-6 questions / sub-topics the doc should cover.\n"
        "3. For anything you DO have org context on, draft a short answer "
        "and cite the source.\n"
        "4. For anything you DON'T, write `[NEEDS INPUT: ...]` so the admin "
        "sees exactly what's missing.\n\n"
        "Neutral, internal tone. No marketing copy."
    )

    svc: SupabaseClient = get_service_client()
    try:
        result = await execute_task_blocking(
            user_message=prompt, org_id=org_id, db_client=svc
        )
    except Exception as exc:
        log.warning("knowledge_curator.stub_llm_failed org=%s err=%s", org_id, exc)
        return {
            "title": f"[STUB] {topic[:160]}",
            "content": (
                f"# {topic}\n\n"
                f"_AI-drafted stub could not be generated automatically — please "
                f"write this document manually. The Knowledge Curator detected "
                f"{count} unanswered queries about this topic this week._\n"
            ),
        }

    body = result.text or ""
    if result.sources:
        names = sorted({s.get("document_name") for s in result.sources if s.get("document_name")})
        if names:
            body += "\n\n---\n_Source documents consulted: " + ", ".join(names) + "_"

    return {
        "title": f"[STUB] {topic[:160]}",
        "content": body.strip()
        or (
            f"# {topic}\n\n"
            f"_The AI couldn't draft this stub. {count} unanswered queries this week._\n"
        ),
    }


def _insert_draft_sync(
    *,
    org_id: str,
    topic: str,
    count: int,
    title: str,
    content: str,
) -> str | None:
    svc = get_service_client()
    row = {
        "org_id": org_id,
        "title": title,
        "content": content,
        "source": "knowledge_curator_scan",
        "gap_topic": topic[:500],
        "gap_count": count,
        "status": "pending_review",
    }
    try:
        res = svc.table("document_drafts").insert(row).execute()
        if res.data:
            return res.data[0]["id"]
        return None
    except Exception as exc:
        # Unique-partial-index race — another worker beat us. That's the
        # protection we want; treat as success.
        if "duplicate key" in str(exc).lower():
            log.info("knowledge_curator.draft_race org=%s topic=%s", org_id, topic)
            return None
        raise


async def _insert_draft(
    *,
    org_id: str,
    topic: str,
    count: int,
    title: str,
    content: str,
) -> str | None:
    return await asyncio.to_thread(
        _insert_draft_sync,
        org_id=org_id,
        topic=topic,
        count=count,
        title=title,
        content=content,
    )


# ── Report persistence ───────────────────────────────────────────────────


async def _fetch_org_config(org_id: str) -> dict[str, Any]:
    svc = get_service_client()

    def _run() -> dict[str, Any]:
        res = (
            svc.table("organizations")
            .select(
                "id, name, curator_enabled, curator_outdated_days, "
                "curator_check_broken_links, curator_merge_threshold"
            )
            .eq("id", org_id)
            .maybe_single()
            .execute()
        )
        return (res.data if res else {}) or {}

    return await asyncio.to_thread(_run)


async def _create_pending_report(
    svc: Any, *, org_id: str, triggered_by: str | None
) -> str:
    def _run() -> str:
        res = (
            svc.table("knowledge_health_reports")
            .insert(
                {
                    "org_id": org_id,
                    "status": "pending",
                    "triggered_by": triggered_by,
                    "payload": {},
                }
            )
            .execute()
        )
        rows = res.data or []
        if not rows:
            raise RuntimeError("knowledge_health_reports insert returned no rows")
        return rows[0]["id"]

    return await asyncio.to_thread(_run)


async def _mark_report_completed(svc: Any, *, report_id: str, payload: dict[str, Any]) -> None:
    def _run() -> None:
        svc.table("knowledge_health_reports").update(
            {
                "status": "completed",
                "payload": payload,
                "completed_at": "now()",
            }
        ).eq("id", report_id).execute()

    await asyncio.to_thread(_run)


async def _mark_report_failed(svc: Any, *, report_id: str, error: str) -> None:
    def _run() -> None:
        svc.table("knowledge_health_reports").update(
            {
                "status": "failed",
                "error_message": error,
                "completed_at": "now()",
            }
        ).eq("id", report_id).execute()

    await asyncio.to_thread(_run)


async def _notify_admins_of_report(
    *, org_id: str, report_id: str, payload: dict[str, Any]
) -> None:
    """In-app notification to admins (no email — link to the dashboard).

    Per the day-3 decision: keep the notification surface tight. The
    dashboard is the source of truth; admins get one in-app ping per
    weekly scan.
    """
    counts = payload.get("counts") or {}
    n_findings = (
        int(counts.get("outdated", 0))
        + int(counts.get("broken_links", 0))
        + int(counts.get("merge_clusters", 0))
        + int(counts.get("gaps", 0))
    )
    if n_findings == 0:
        # Don't pester admins with "all clear" notifications. The dashboard
        # still has the new report visible if they want to look.
        return

    from app.services.notifications import create_notification

    svc = get_service_client()

    def _admins() -> list[str]:
        res = (
            svc.table("users")
            .select("id")
            .eq("org_id", org_id)
            .eq("role", "admin")
            .execute()
        )
        return [r["id"] for r in (res.data or [])]

    admin_ids = await asyncio.to_thread(_admins)
    body_parts = []
    if counts.get("outdated"):
        body_parts.append(f"{counts['outdated']} outdated doc(s)")
    if counts.get("broken_links"):
        body_parts.append(f"{counts['broken_links']} broken link(s)")
    if counts.get("merge_clusters"):
        body_parts.append(f"{counts['merge_clusters']} merge candidate(s)")
    if counts.get("gaps"):
        body_parts.append(f"{counts['gaps']} knowledge gap(s)")
    if counts.get("stubs"):
        body_parts.append(f"{counts['stubs']} stub draft(s) ready for review")

    body = "Knowledge health scan completed: " + ", ".join(body_parts) + "."

    for admin_id in admin_ids:
        await create_notification(
            org_id=org_id,
            user_id=admin_id,
            type="knowledge_health_report",
            title="Weekly knowledge health report ready",
            body=body,
            link_url=f"/admin/knowledge-health?report={report_id}",
            metadata={"report_id": report_id, "counts": counts},
            dedupe_key=f"knowledge_health_report:{report_id}",
        )
