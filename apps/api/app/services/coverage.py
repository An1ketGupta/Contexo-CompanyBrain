"""V4 Day 4: Knowledge-Base Coverage Score.

For each of eight canonical business categories, decide whether the org's
knowledge base "covers" it. The roadmap proposes pure keyword matching on
document names + tags; that's brittle ("sales-playbook-v2.pdf" doesn't say
"sales" verbatim, "vendor-master-list.xlsx" doesn't say "procurement"). We
do a hybrid:

  1. Fast keyword scan against `documents.name` + `documents.tags` per
     category. If we already see ≥2 keyword hits, the category is covered
     (cheap, deterministic, easy to debug).
  2. Otherwise, embed the category's canonical query once and use the
     existing pgvector RPC to fetch the top-K nearest chunks. If ≥3
     chunks come back above a similarity threshold, the category is
     considered semantically covered.

Eight categories × at most one embedding call each ≈ a few hundred ms on
a cold compute, and it's cached on a DB row (`coverage_scores`) with a
1-hour TTL plus eager invalidation when a document flips to `ready`.

This file is the single source of truth for the canonical category set —
the frontend doesn't ship a copy.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from supabase import Client

from app.database import get_service_client
from app.services.ingestion.embedder import get_embedder

log = logging.getLogger(__name__)

# How long a cached coverage_scores row is considered fresh before we
# recompute. Coverage shifts on the timescale of "uploaded a new HR doc",
# not seconds, so 1h is plenty.
CACHE_TTL = timedelta(hours=1)

# Semantic-coverage thresholds. Tuned empirically against the existing
# Gemini-768d embeddings:
#   - 0.55 is the rough boundary between "topically related" and "noise"
#     for L2-normalized cosine on this embedding model.
#   - Requiring 3 hits at-or-above this avoids declaring coverage on a
#     single tangential mention.
_SEMANTIC_K = 8
_SEMANTIC_MIN_HITS = 3
_SEMANTIC_MIN_SIM = 0.55

# Keyword-coverage threshold. ≥2 matches keeps us from false-positives on
# orgs whose docs happen to mention a single canonical keyword in passing.
_KEYWORD_MIN_HITS = 2


# Canonical categories. Order is presentation order in the admin UI.
#
# Each entry has:
#   id                 — stable identifier (used as React key, never user-visible)
#   label              — admin UI heading
#   description        — short subheader rendered under the label
#   detection_keywords — substrings checked against doc name + tags (lowercased)
#   semantic_query     — single sentence used for embedding similarity. Written
#                        as a question because Gemini RETRIEVAL_QUERY embeddings
#                        match better against chunk text than label fragments do.
#   example_questions  — surfaced in the "questions your team can't answer" panel
#
# Adding a category here is the *only* change required end-to-end.
CANONICAL_CATEGORIES: list[dict[str, Any]] = [
    {
        "id": "hr_policies",
        "label": "HR & People Policies",
        "description": "Vacation, leave, benefits, onboarding, performance reviews.",
        "detection_keywords": [
            "vacation", "leave", "pto", "benefit", "hr",
            "employee", "handbook", "onboarding", "performance", "payroll",
        ],
        "semantic_query": (
            "Company HR policy covering vacation, paid leave, benefits, "
            "employee handbook, onboarding, and performance reviews."
        ),
        "example_questions": [
            "What is our vacation policy?",
            "How do I request parental leave?",
            "How does the performance review cycle work?",
        ],
    },
    {
        "id": "product_overview",
        "label": "Product & Pricing",
        "description": "What you sell, who it's for, and how it's priced.",
        "detection_keywords": [
            "product", "pricing", "plan", "feature", "tier",
            "subscription", "enterprise", "roadmap", "spec",
        ],
        "semantic_query": (
            "Description of the product, its key features, target users, "
            "pricing plans, and subscription tiers."
        ),
        "example_questions": [
            "What does our product do in one sentence?",
            "What are our pricing tiers?",
            "Which features are gated behind the enterprise plan?",
        ],
    },
    {
        "id": "customer_support",
        "label": "Customer Support & Refunds",
        "description": "Refund policy, support SLAs, escalation process.",
        "detection_keywords": [
            "refund", "return", "support", "sla", "ticket",
            "customer", "complaint", "chargeback", "escalation",
        ],
        "semantic_query": (
            "Customer support process, refund policy, response-time SLA, "
            "ticket escalation steps."
        ),
        "example_questions": [
            "What is our refund policy?",
            "What is our first-response SLA?",
            "When should a ticket be escalated to engineering?",
        ],
    },
    {
        "id": "sales_process",
        "label": "Sales Process",
        "description": "Discovery, objection handling, competitive positioning.",
        "detection_keywords": [
            "sales", "prospect", "objection", "demo", "contract",
            "negotiation", "competitor", "playbook", "discovery",
        ],
        "semantic_query": (
            "Sales playbook, discovery questions, objection handling, "
            "competitive positioning, contract negotiation."
        ),
        "example_questions": [
            "How do we handle the 'too expensive' objection?",
            "How do we position against our top competitor?",
            "What's the right discovery question for a CTO?",
        ],
    },
    {
        "id": "brand_voice",
        "label": "Brand & Communication",
        "description": "Tone of voice, brand guidelines, messaging.",
        "detection_keywords": [
            "brand", "voice", "tone", "messaging", "style guide",
            "communication", "marketing", "copy",
        ],
        "semantic_query": (
            "Brand voice and tone, style guide, messaging principles, "
            "how we communicate with customers."
        ),
        "example_questions": [
            "What is our brand voice?",
            "Should we use the Oxford comma?",
            "How do we describe ourselves in 25 words?",
        ],
    },
    {
        "id": "legal_compliance",
        "label": "Legal & Compliance",
        "description": "Privacy, terms, GDPR, SOC2, data handling.",
        "detection_keywords": [
            "privacy", "gdpr", "compliance", "legal", "terms",
            "data protection", "security", "soc2", "dpa", "policy",
        ],
        "semantic_query": (
            "Privacy policy, terms of service, GDPR compliance, "
            "SOC2 controls, data handling and security practices."
        ),
        "example_questions": [
            "Where is customer data stored?",
            "Are we GDPR compliant?",
            "Do we have a DPA template?",
        ],
    },
    {
        "id": "engineering_process",
        "label": "Engineering & Tech",
        "description": "Architecture, runbooks, deployment process, tech stack.",
        "detection_keywords": [
            "engineering", "architecture", "runbook", "api", "deploy",
            "infrastructure", "tech stack", "incident", "postmortem",
        ],
        "semantic_query": (
            "Engineering architecture, deployment runbook, API design, "
            "infrastructure, incident response, technology stack."
        ),
        "example_questions": [
            "How do we deploy to production?",
            "What's our tech stack?",
            "Where is the incident response runbook?",
        ],
    },
    {
        "id": "finance_ops",
        "label": "Finance & Operations",
        "description": "Expenses, invoicing, vendors, budgets.",
        "detection_keywords": [
            "expense", "invoice", "finance", "budget", "procurement",
            "vendor", "operations", "reimburs", "accounting",
        ],
        "semantic_query": (
            "Expense policy, invoicing process, vendor management, "
            "budget approvals, financial operations."
        ),
        "example_questions": [
            "How do I submit an expense?",
            "What's our vendor onboarding process?",
            "Who approves a $5k budget request?",
        ],
    },
]


# ── Public API ───────────────────────────────────────────────────────────────

async def get_or_compute_coverage(
    org_id: str,
    *,
    force_refresh: bool = False,
    client: Client | None = None,
) -> dict[str, Any]:
    """Return a fresh-enough coverage payload for the org.

    Reads the cache row; recomputes if older than `CACHE_TTL` or missing or
    `force_refresh`. Writes back via upsert. Stale-while-error: if the
    recompute fails but a stale row exists, return the stale row rather
    than 500ing the admin page.
    """
    client = client or get_service_client()
    cached = await _read_cached(org_id, client=client)

    if cached and not force_refresh:
        computed_at = cached.get("computed_at")
        if _is_fresh(computed_at):
            payload = cached["score_json"]
            payload["cached"] = True
            payload["computed_at"] = computed_at
            return payload

    try:
        payload = await compute_coverage(org_id, client=client)
    except Exception:
        log.exception("coverage recompute failed for org=%s", org_id)
        if cached:
            payload = cached["score_json"]
            payload["cached"] = True
            payload["computed_at"] = cached.get("computed_at")
            payload["stale"] = True
            return payload
        raise

    await _write_cached(org_id, payload, client=client)
    payload["cached"] = False
    return payload


async def invalidate_coverage(
    org_id: str,
    *,
    client: Client | None = None,
) -> None:
    """Drop the cached coverage row for an org. Idempotent.

    Called from the ingestion pipeline the moment a document flips to
    `ready`, so the admin "Coverage Score" page reflects new uploads
    on the next visit instead of waiting up to an hour for the TTL.
    """
    client = client or get_service_client()
    try:
        await asyncio.to_thread(
            lambda: client.table("coverage_scores").delete().eq("org_id", org_id).execute()
        )
    except Exception as exc:
        # Cache invalidation must never break the ingestion path. Log and move on;
        # the worst case is the admin page is stale for up to CACHE_TTL.
        log.warning("coverage cache invalidate failed for org=%s: %s", org_id, exc)


# ── Compute path ─────────────────────────────────────────────────────────────

async def compute_coverage(
    org_id: str,
    *,
    client: Client | None = None,
) -> dict[str, Any]:
    """Run the keyword + embedding pass and return the full payload.

    Public for tests / forced refreshes; production callers should prefer
    `get_or_compute_coverage` so the result lands in the cache.
    """
    client = client or get_service_client()

    docs = await _fetch_ready_documents(org_id, client=client)
    total_docs = len(docs)
    if total_docs == 0:
        # No documents → no coverage. Skip the embedding RPCs entirely.
        categories = [_empty_category(c) for c in CANONICAL_CATEGORIES]
        return _payload(categories, total_docs=0)

    # Build a single lowercased "haystack" per category to scan once instead
    # of per-document-per-keyword. Includes the name and any tags so a doc
    # tagged "vendors" but named "Q3-master-list.xlsx" still counts toward
    # finance_ops via the tag.
    haystacks = [_doc_haystack(d) for d in docs]
    joined = " || ".join(haystacks)

    # Categories we couldn't satisfy with keywords — embed only these.
    needs_semantic: list[tuple[int, dict[str, Any]]] = []
    results: list[dict[str, Any] | None] = [None] * len(CANONICAL_CATEGORIES)

    for idx, cat in enumerate(CANONICAL_CATEGORIES):
        keyword_hits = _count_keyword_hits(cat["detection_keywords"], joined)
        matched_doc_names = (
            _doc_names_matching(cat["detection_keywords"], docs, haystacks)
            if keyword_hits else []
        )

        if keyword_hits >= _KEYWORD_MIN_HITS:
            results[idx] = _build_category(
                cat,
                covered=True,
                evidence="keyword",
                keyword_hits=keyword_hits,
                semantic_hits=0,
                avg_similarity=None,
                matched_doc_names=matched_doc_names,
            )
            continue

        # Stash for the embedding pass. We still keep keyword_hits because
        # it's useful debugging info even when sub-threshold.
        needs_semantic.append((idx, {**cat, "_keyword_hits": keyword_hits, "_doc_names": matched_doc_names}))

    if needs_semantic:
        semantic_results = await _semantic_coverage(
            [c for _, c in needs_semantic], org_id=org_id, client=client
        )
        for (idx, cat), sem in zip(needs_semantic, semantic_results, strict=True):
            results[idx] = _build_category(
                cat,
                covered=sem["covered"],
                evidence="semantic" if sem["covered"] else "none",
                keyword_hits=cat["_keyword_hits"],
                semantic_hits=sem["hits"],
                avg_similarity=sem["avg_similarity"],
                matched_doc_names=(
                    sem["doc_names"] if sem["covered"] else cat["_doc_names"]
                ),
            )

    # All slots filled by construction.
    final_categories = [r for r in results if r is not None]
    assert len(final_categories) == len(CANONICAL_CATEGORIES)
    return _payload(final_categories, total_docs=total_docs)


# ── Internals ────────────────────────────────────────────────────────────────

def _is_fresh(computed_at_iso: str | None) -> bool:
    if not computed_at_iso:
        return False
    try:
        computed_at = datetime.fromisoformat(computed_at_iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    return datetime.now(UTC) - computed_at < CACHE_TTL


async def _read_cached(org_id: str, *, client: Client) -> dict[str, Any] | None:
    res = await asyncio.to_thread(
        lambda: client.table("coverage_scores")
        .select("score_json, overall_score, computed_at")
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    return res.data if res and res.data else None


async def _write_cached(
    org_id: str,
    payload: dict[str, Any],
    *,
    client: Client,
) -> None:
    row = {
        "org_id": org_id,
        "score_json": payload,
        "overall_score": payload["overall_score"],
        "computed_at": datetime.now(UTC).isoformat(),
    }
    await asyncio.to_thread(
        lambda: client.table("coverage_scores").upsert(row, on_conflict="org_id").execute()
    )


async def _fetch_ready_documents(org_id: str, *, client: Client) -> list[dict[str, Any]]:
    """All 'ready' documents in the org. Capped at 5k — past that, keyword
    coverage is already saturated and we don't want to load the world.
    """
    res = await asyncio.to_thread(
        lambda: client.table("documents")
        .select("id, name, tags")
        .eq("org_id", org_id)
        .eq("status", "ready")
        .limit(5000)
        .execute()
    )
    return res.data or []


def _doc_haystack(doc: dict[str, Any]) -> str:
    name = (doc.get("name") or "").lower()
    tags = doc.get("tags") or []
    tags_lower = " ".join(str(t).lower() for t in tags)
    return f"{name} {tags_lower}"


def _count_keyword_hits(keywords: list[str], joined_haystack: str) -> int:
    """Count distinct keywords (not occurrences) that appear in the haystack.

    Using distinct keywords avoids inflating the score when one keyword
    repeats across many docs ("vendor" appearing in 30 file names should
    still count as 1 toward finance_ops).
    """
    return sum(1 for kw in keywords if kw in joined_haystack)


def _doc_names_matching(
    keywords: list[str],
    docs: list[dict[str, Any]],
    haystacks: list[str],
) -> list[str]:
    """Return up to 5 doc names whose haystack contained any keyword."""
    matches: list[str] = []
    for doc, hay in zip(docs, haystacks, strict=True):
        if any(kw in hay for kw in keywords):
            name = doc.get("name")
            if name:
                matches.append(name)
        if len(matches) >= 5:
            break
    return matches


async def _semantic_coverage(
    categories: list[dict[str, Any]],
    *,
    org_id: str,
    client: Client,
) -> list[dict[str, Any]]:
    """For each category, embed its semantic_query and look up nearest chunks.

    Returns one result per input category, in order. On embedding failure
    (quota, network), returns 'not covered' for every passed category and
    logs — better than failing the whole admin page.
    """
    embedder = get_embedder()
    queries = [c["semantic_query"] for c in categories]
    try:
        vectors = await embedder.embed_texts(queries, task_type="RETRIEVAL_QUERY")
    except Exception as exc:
        log.warning("coverage embedding failed for org=%s: %s", org_id, exc)
        return [{"covered": False, "hits": 0, "avg_similarity": None, "doc_names": []} for _ in categories]

    if len(vectors) != len(categories):
        log.warning(
            "coverage embedding count mismatch: sent %d, got %d", len(categories), len(vectors)
        )
        return [{"covered": False, "hits": 0, "avg_similarity": None, "doc_names": []} for _ in categories]

    async def _one(vec: list[float]) -> dict[str, Any]:
        try:
            res = await asyncio.to_thread(
                lambda: client.rpc(
                    "vector_search",
                    {
                        "query_embedding": vec,
                        "match_org_id": org_id,
                        "match_count": _SEMANTIC_K,
                    },
                ).execute()
            )
        except Exception as exc:
            log.warning("coverage vector_search failed for org=%s: %s", org_id, exc)
            return {"covered": False, "hits": 0, "avg_similarity": None, "doc_names": []}

        rows = res.data or []
        passing = [r for r in rows if float(r.get("similarity") or 0) >= _SEMANTIC_MIN_SIM]
        avg = (
            sum(float(r["similarity"]) for r in passing) / len(passing)
            if passing else None
        )
        # Dedupe by document_name (multiple chunks from the same doc count as one).
        names: list[str] = []
        seen: set[str] = set()
        for r in passing:
            n = r.get("document_name")
            if n and n not in seen:
                seen.add(n)
                names.append(n)
        return {
            "covered": len(passing) >= _SEMANTIC_MIN_HITS,
            "hits": len(passing),
            "avg_similarity": round(avg, 3) if avg is not None else None,
            "doc_names": names[:5],
        }

    # Embedding-RPC calls are independent; run them concurrently. 8 calls
    # against pgvector in parallel is well within Supabase's pooled limits.
    return await asyncio.gather(*(_one(v) for v in vectors))


def _build_category(
    cat: dict[str, Any],
    *,
    covered: bool,
    evidence: str,
    keyword_hits: int,
    semantic_hits: int,
    avg_similarity: float | None,
    matched_doc_names: list[str],
) -> dict[str, Any]:
    gap_message = None
    if not covered:
        kw_preview = ", ".join(cat["detection_keywords"][:3])
        gap_message = (
            f"No documents found covering {cat['label'].lower()}. "
            f"Consider uploading docs about: {kw_preview}."
        )
    return {
        "id": cat["id"],
        "label": cat["label"],
        "description": cat["description"],
        "covered": covered,
        "evidence": evidence,        # 'keyword' | 'semantic' | 'none'
        "keyword_hits": keyword_hits,
        "semantic_hits": semantic_hits,
        "avg_similarity": avg_similarity,
        "matched_documents": matched_doc_names,
        "example_questions": cat["example_questions"],
        "gap_message": gap_message,
    }


def _empty_category(cat: dict[str, Any]) -> dict[str, Any]:
    return _build_category(
        cat,
        covered=False,
        evidence="none",
        keyword_hits=0,
        semantic_hits=0,
        avg_similarity=None,
        matched_doc_names=[],
    )


def _payload(categories: list[dict[str, Any]], *, total_docs: int) -> dict[str, Any]:
    covered = [c for c in categories if c["covered"]]
    gaps = [c for c in categories if not c["covered"]]
    overall = len(covered) / len(categories) if categories else 0.0
    return {
        "overall_score": round(overall, 3),
        "covered": len(covered),
        "total": len(categories),
        "total_documents": total_docs,
        "categories": categories,
        "gaps": gaps,
    }
