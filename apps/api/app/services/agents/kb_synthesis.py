"""KB-backed synthesis helpers for bespoke agents (Recruiting, Sales, Calendar).

The "big" agents in Agent2 — Recruiting, Sales Enablement (Pre-Call, RFP),
Calendar Intelligence, Action Tracker — all share two needs:

  1. Run several focused KB searches concurrently across different facets of
     the task (e.g. "competitor", "pricing", "case studies" for a pre-call
     brief). Sequential per-facet retrieval is the dominant latency cost.

  2. Hand the retrieved context + a task-shaped prompt to Gemini and get back
     either free text or structured JSON.

Per the explicit "no shared agent framework" decision in Agent_Roadmap_2.md,
this module deliberately stops at *helpers* — there's no `BaseAgent` to extend.
Each agent service composes these calls in its own pipeline.

Why service-role for the Supabase client:
    Agent calls happen from HTTP handlers (Recruiting "Generate variants"
    button) AND from background jobs (Calendar prep brief cron). The HTTP
    paths *could* use the user JWT, but the cron paths cannot — so for
    consistency every agent call goes through service-role, and org_id is
    passed explicitly to every search.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from app.database import get_service_client
from app.services.llm.client import get_llm_client
from app.services.llm.types import Message
from app.services.retrieval.hybrid_search import hybrid_search

log = logging.getLogger(__name__)


@dataclass
class FacetResult:
    """One facet's hits + a compact text packing for LLM consumption."""

    facet: str
    query: str
    hits: list[dict[str, Any]]
    packed_context: str


async def search_facet(
    *,
    facet: str,
    query: str,
    org_id: str,
    k: int = 6,
    char_budget: int = 4000,
    min_similarity: float | None = None,
) -> FacetResult:
    """Run one hybrid search and pack the top hits into a budget-bounded blob.

    We pack chunks newest-to-best until char_budget is exhausted. Each hit is
    prefixed with [docname] so the synthesis prompt can cite by name without
    a second SQL round-trip — the model decides which to surface.

    `min_similarity` sets a cosine relevance floor on the vector branch so a
    vague/empty facet query yields no context instead of the corpus's most
    central (but irrelevant) chunks. Left None, retrieval is unchanged.
    """
    svc = get_service_client()
    try:
        hits = await hybrid_search(
            query=query, org_id=org_id, client=svc, k=k, min_similarity=min_similarity
        )
    except Exception as exc:
        log.warning("kb_synthesis.search_failed facet=%s err=%s", facet, exc)
        return FacetResult(facet=facet, query=query, hits=[], packed_context="")

    hit_dicts: list[dict[str, Any]] = []
    parts: list[str] = []
    total = 0
    for h in hits:
        content = (h.content or "").strip()
        if not content:
            continue
        block = f"[{h.document_name}] {content}"
        if total + len(block) > char_budget and parts:
            break
        parts.append(block)
        total += len(block)
        hit_dicts.append(
            {
                "chunk_id": h.chunk_id,
                "document_id": h.document_id,
                "document_name": h.document_name,
                "section_heading": h.section_heading,
                "page_number": h.page_number,
                "similarity": h.similarity,
                "vector_similarity": h.vector_similarity,
            }
        )

    return FacetResult(
        facet=facet,
        query=query,
        hits=hit_dicts,
        packed_context="\n\n---\n\n".join(parts),
    )


async def search_facets_concurrent(
    *,
    org_id: str,
    facets: dict[str, str],
    k: int = 6,
    char_budget_per_facet: int = 4000,
    min_similarity: float | None = None,
) -> dict[str, FacetResult]:
    """Run N facet searches in parallel. Returns a {facet_name: FacetResult} map.

    facets shape: {"competitor": "competitive analysis of acme corp", …}
    The dict key is the facet label the caller uses to wire results into the
    synthesis prompt; the value is the actual search query.

    `min_similarity`, when set, is applied uniformly to every facet's vector
    branch (see `search_facet`).
    """
    if not facets:
        return {}

    facet_names = list(facets.keys())
    queries = [facets[name] for name in facet_names]

    results = await asyncio.gather(
        *[
            search_facet(
                facet=name,
                query=q,
                org_id=org_id,
                k=k,
                char_budget=char_budget_per_facet,
                min_similarity=min_similarity,
            )
            for name, q in zip(facet_names, queries, strict=False)
        ],
        return_exceptions=False,
    )

    return dict(zip(facet_names, results, strict=False))


def build_context_block(facets: dict[str, FacetResult]) -> str:
    """Stitch every facet's packed_context into one prompt-ready blob."""
    sections: list[str] = []
    for name, fr in facets.items():
        if not fr.packed_context:
            continue
        sections.append(f"=== Facet: {name} (query: {fr.query}) ===\n{fr.packed_context}")
    return "\n\n".join(sections) if sections else ""


def collect_sources(facets: dict[str, FacetResult]) -> list[dict[str, Any]]:
    """Deduplicate the union of facet hits by document_id, preserving the
    best similarity seen across facets. Used to surface a "Based on:" panel."""
    by_doc: dict[str, dict[str, Any]] = {}
    for fr in facets.values():
        for h in fr.hits:
            doc_id = h["document_id"]
            sim = h.get("vector_similarity") or h.get("similarity") or 0.0
            existing = by_doc.get(doc_id)
            if not existing or (existing.get("similarity") or 0) < sim:
                by_doc[doc_id] = {
                    "document_id": doc_id,
                    "document_name": h["document_name"],
                    "similarity": sim,
                    "section_heading": h.get("section_heading"),
                }
    return sorted(by_doc.values(), key=lambda r: r.get("similarity") or 0, reverse=True)


async def fetch_document_text(
    *,
    document_id: str,
    org_id: str,
    char_budget: int = 12000,
) -> str:
    """Return one document's chunks concatenated in order, packed to a budget.

    Deliberately bypasses `hybrid_search`/the search RPCs: those enforce the
    migration-084 visibility gate (`d.visibility='org' OR d.created_by =
    auth.uid()`), and under the service-role client `auth.uid()` is NULL — so a
    private doc (every Google Meet transcript is `visibility='private'`) would
    return nothing. Reading the `chunks` table directly with the service client
    sidesteps that gate. Callers must therefore establish authorization
    themselves (the prep-brief path scopes to the meeting owner's own docs).
    """
    svc = get_service_client()

    def _fetch() -> list[dict[str, Any]]:
        res = (
            svc.table("chunks")
            .select("content, chunk_index")
            .eq("org_id", org_id)
            .eq("document_id", document_id)
            .order("chunk_index")
            .execute()
        )
        return res.data or []

    rows = await asyncio.to_thread(_fetch)
    parts: list[str] = []
    total = 0
    for r in rows:
        content = (r.get("content") or "").strip()
        if not content:
            continue
        if total + len(content) > char_budget and parts:
            break
        parts.append(content)
        total += len(content)
    return "\n\n".join(parts)


# ── Synthesis ──────────────────────────────────────────────────────────────


async def synthesize_text(
    *,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.4,
    timeout: float = 90.0,
) -> str:
    """One-shot Gemini call returning the message text. No tools.

    Why expose a single helper instead of having every agent talk to
    `get_llm_client()` directly: standardizes timeout/temperature defaults and
    centralizes Langfuse instrumentation (the @observe wrapping happens inside
    GeminiClient.complete already, so we don't need to add it here).
    """
    client = get_llm_client()
    response = await client.complete(
        messages=[Message(role="user", content=user_prompt)],
        system_extra=system_prompt,
        temperature=temperature,
        timeout=timeout,
    )
    return (response.text or "").strip()


async def synthesize_json(
    *,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.2,
    timeout: float = 90.0,
) -> dict[str, Any] | list[Any]:
    """Same as `synthesize_text` but parses a JSON object/array from the reply.

    We avoid Gemini's "response_mime_type=application/json" because it's
    structured-output mode interacts with thinking models in surprising ways.
    Instead we ask the model in the prompt to return JSON only, then parse
    whatever it returns. Falls back to attempting to slice the first {...}
    or [...] from the text if the model wrapped it in prose.
    """
    import json
    import re

    raw = await synthesize_text(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=temperature,
        timeout=timeout,
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Salvage: model often wraps JSON in ```json ... ``` fences or chats first.
    fence = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", raw, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    obj_match = re.search(r"(\{.*\}|\[.*\])", raw, re.DOTALL)
    if obj_match:
        try:
            return json.loads(obj_match.group(1))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"LLM did not return valid JSON. First 200 chars: {raw[:200]}")
