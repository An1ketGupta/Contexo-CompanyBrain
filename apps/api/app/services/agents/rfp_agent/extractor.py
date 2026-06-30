"""Requirement extraction + semantic dedup.

Two responsibilities:

  1. extract_requirements(file_bytes, source_format) — Try the structured
     parser first. If it returns `llm_freetext`, fall back to a Gemini call
     that lifts a JSON list of requirements out of the prose.

  2. cluster_requirements(reqs) — Embed every requirement and union similar
     ones into clusters via a single-linkage threshold sweep. The first
     member of each cluster becomes the "lead"; the answerer runs once per
     lead and shares the answer with the cluster. Big wins on real RFPs
     which routinely include 10+ near-identical questions across sections
     ("Describe your SOC 2 status" appears under Security, Compliance, AND
     References).

Public surface:

    extract(file_bytes, source_format) -> ParsedRfp
    cluster_and_persist(rfp_id, org_id, parsed) -> list of requirement rows
"""
from __future__ import annotations

import asyncio
import logging
import math
import uuid
from typing import Any

from app.database import get_service_client
from app.services.agents.kb_synthesis import synthesize_json
from app.services.ingestion.embedder import get_embedder

from .parser import ParsedRequirement, ParsedRfp, parse_rfp

log = logging.getLogger(__name__)


# ── LLM fallback structurer ─────────────────────────────────────────────────


_LLM_EXTRACT_SYSTEM = """You parse an RFP (Request for Proposal) document into discrete atomic requirements.

Definitions:
- A "requirement" is ONE specific thing the customer asks the vendor to commit to or describe: a capability, certification, SLA, integration, pricing breakdown, security control, reference.

Rules:
- Split umbrella sections into atomic requirements. "Describe your security posture" is too broad — emit one item per testable sub-claim (SOC 2, data residency, encryption, access controls).
- Keep customer language. Do not paraphrase requirements into your preferred terminology.
- Drop boilerplate ("This RFP is confidential", "Vendors must submit by July 1") — those are not requirements.
- Categorize each: security, integration, pricing, support, capability, compliance, references, company, other.
- Limit: at most 200 requirements per RFP.

Output JSON only, no prose:
{
  "requirements": [
    {"id": "R1", "requirement_text": "string", "category": "string"},
    {"id": "R2", "requirement_text": "string", "category": "string"}
  ]
}
""".strip()


async def llm_extract_from_text(full_text: str) -> list[ParsedRequirement]:
    """Last-resort LLM extractor for PDFs / free-text RFPs."""
    if not full_text.strip():
        return []
    # Cap input — Flash handles ~60k chars comfortably; longer RFPs get
    # truncated and we'd rather extract from the front half than fail.
    text = full_text[:80_000]
    try:
        result = await synthesize_json(
            system_prompt=_LLM_EXTRACT_SYSTEM,
            user_prompt=f"## RFP source\n\n{text}",
            temperature=0.0,
            timeout=120.0,
        )
    except Exception as exc:
        log.warning("rfp.extractor.llm_failed: %s", exc)
        return []

    raw = (result or {}).get("requirements") if isinstance(result, dict) else None
    if not isinstance(raw, list):
        return []

    out: list[ParsedRequirement] = []
    for i, r in enumerate(raw):
        if not isinstance(r, dict):
            continue
        text_val = (r.get("requirement_text") or "").strip()
        if len(text_val) < 5:
            continue
        out.append(
            ParsedRequirement(
                text=text_val,
                category=(r.get("category") or None),
                external_ref=str(r.get("id") or f"R{i+1}"),
            )
        )
    return out


# ── Top-level extract ──────────────────────────────────────────────────────


async def extract(file_bytes: bytes, source_format: str) -> ParsedRfp:
    """Structured parse first; LLM fallback if no structure detected.

    Important: when the parser yields structured XLSX rows we DO NOT also
    invoke the LLM. The whole point of round-tripping is that we know exactly
    which buyer cell each requirement came from. Replacing the parser's exact
    row text with an LLM paraphrase would break that breadcrumb.
    """
    parsed = await asyncio.to_thread(parse_rfp, file_bytes, source_format)

    if parsed.requirements:
        return parsed

    if parsed.extraction_source == "llm_freetext" and parsed.full_text_fallback:
        reqs = await llm_extract_from_text(parsed.full_text_fallback)
        if reqs:
            return ParsedRfp(
                requirements=reqs,
                extraction_source="llm_freetext",
                full_text_fallback=parsed.full_text_fallback,
            )

    return parsed  # empty — caller surfaces "no requirements found"


# ── Semantic dedup ─────────────────────────────────────────────────────────

# Cosine sim above this counts as "same question." Tuned empirically: 0.92
# catches paraphrases ("Do you have SOC 2?" ≈ "Are you SOC 2 compliant?")
# while 0.95+ misses too many obvious duplicates and 0.88- conflates SAML
# with SSO which a buyer often wants answered separately.
_CLUSTER_THRESHOLD = 0.92


async def cluster_requirements(
    requirements: list[ParsedRequirement],
) -> list[list[int]]:
    """Return a list of clusters where each cluster is a list of indices
    into the input. Single-element clusters are valid (most reqs are unique).

    Strategy: embed all reqs once, sweep pairs with cosine ≥ threshold and
    union-find them. O(n²) but n is bounded (200 reqs max).
    """
    if len(requirements) <= 1:
        return [[i] for i in range(len(requirements))]

    embedder = get_embedder()
    texts = [r.text[:1000] for r in requirements]
    try:
        vecs = await embedder.embed_texts(texts, task_type="RETRIEVAL_QUERY")
    except Exception as exc:
        log.warning("rfp.extractor.embed_failed: %s — falling back to no-dedup", exc)
        return [[i] for i in range(len(requirements))]

    if len(vecs) != len(requirements):
        log.warning("rfp.extractor.embed_count_mismatch: %d != %d", len(vecs), len(requirements))
        return [[i] for i in range(len(requirements))]

    # Union-find for the cluster merge.
    parent = list(range(len(requirements)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(len(vecs)):
        for j in range(i + 1, len(vecs)):
            if _cos(vecs[i], vecs[j]) >= _CLUSTER_THRESHOLD:
                union(i, j)

    clusters: dict[int, list[int]] = {}
    for i in range(len(requirements)):
        root = find(i)
        clusters.setdefault(root, []).append(i)
    # Preserve original ordering: sort clusters by lowest member index.
    return sorted(clusters.values(), key=lambda c: c[0])


def _cos(a: list[float], b: list[float]) -> float:
    # Embedder L2-normalizes outputs, so cosine == dot product.
    s = 0.0
    for x, y in zip(a, b, strict=False):
        s += x * y
    return s


# ── Persist requirements (post-dedup) ──────────────────────────────────────


async def persist_requirements(
    *,
    rfp_id: str,
    org_id: str,
    parsed: ParsedRfp,
) -> list[dict[str, Any]]:
    """Cluster + insert into rfp_requirements. Returns the inserted rows.

    Cluster lead gets `is_cluster_lead=True` and shares a `cluster_id` with
    every dependent member. The answerer skips non-leads and copies the
    lead's answer at finalize time.
    """
    if not parsed.requirements:
        return []

    svc = get_service_client()
    clusters = await cluster_requirements(parsed.requirements)

    # Pre-allocate cluster_ids so leads + members share the same UUID.
    cluster_id_by_lead_idx: dict[int, str] = {}
    for cluster in clusters:
        if len(cluster) > 1:
            cluster_id_by_lead_idx[cluster[0]] = str(uuid.uuid4())

    rows_to_insert: list[dict[str, Any]] = []
    for cluster in clusters:
        lead_idx = cluster[0]
        cluster_uuid = cluster_id_by_lead_idx.get(lead_idx)
        for ordinal, req_idx in enumerate(cluster):
            r = parsed.requirements[req_idx]
            rows_to_insert.append(
                {
                    "rfp_id": rfp_id,
                    "org_id": org_id,
                    "ordinal": req_idx,  # preserve original order
                    "external_ref": r.external_ref,
                    "requirement_text": r.text[:4000],
                    "category": (r.category or "")[:120] or None,
                    "source_sheet": r.source_sheet,
                    "source_row_index": r.source_row_index,
                    "source_answer_column": r.source_answer_column,
                    "cluster_id": cluster_uuid,
                    "is_cluster_lead": (req_idx == lead_idx),
                }
            )

    def _insert_batch() -> list[dict[str, Any]]:
        res = svc.table("rfp_requirements").insert(rows_to_insert).execute()
        return res.data or []

    return await asyncio.to_thread(_insert_batch)
