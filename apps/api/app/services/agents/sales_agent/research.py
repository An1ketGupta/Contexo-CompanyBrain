"""Stage 1 — Research + ICP scoring.

Two responsibilities:

1. Research the prospect (KB first, Tavily web fallback if no KB signal).
2. Score the prospect against the org's ICP document.

The agent calls `research_company()` then `score_icp()`. Both are pure
service-layer functions — no DB writes (the agent persists results via the
storage helpers).
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.agents.kb_synthesis import (
    build_context_block,
    collect_sources,
    search_facets_concurrent,
    synthesize_json,
    synthesize_text,
)
from app.services.integrations import tavily

log = logging.getLogger(__name__)


# Threshold for "usable" KB signal — if the combined packed_context across
# the company-related facets is below this many chars, fall back to web.
_KB_USABLE_CHAR_THRESHOLD = 600


# ── Research ─────────────────────────────────────────────────────────────────


async def research_company(
    *,
    org_id: str,
    company_name: str,
    company_website: str | None,
    industry_hint: str | None,
    contact_name: str | None,
    contact_title: str | None,
) -> dict[str, Any]:
    """KB-first research. If KB has no usable signal, fan out to Tavily and
    merge results.

    Returns a dict suitable for `insert_deal_research` plus a `source` key
    indicating which path was taken ('kb' | 'web_fallback' | 'merged').
    """
    kb_payload = await _research_from_kb(
        org_id=org_id,
        company_name=company_name,
        industry_hint=industry_hint,
    )

    if kb_payload.get("has_usable_signal"):
        # KB had signal — done. Web fallback is intentionally skipped.
        log.info("sales.research kb_only company=%s", company_name)
        return {**kb_payload, "source": "kb"}

    # KB cold — try Tavily.
    try:
        web_payload = await _research_from_web(
            company_name=company_name,
            company_website=company_website,
        )
    except tavily.TavilyUnavailable:
        log.info(
            "sales.research web_fallback_skipped_no_key company=%s", company_name
        )
        return {**kb_payload, "source": "kb"}
    except tavily.TavilyError as exc:
        log.warning("sales.research web_fallback_failed err=%s", exc)
        return {**kb_payload, "source": "kb"}

    # Synthesize a structured brief from the web snippets.
    web_brief = await _synthesize_company_brief_from_web(
        company_name=company_name,
        contact_name=contact_name,
        contact_title=contact_title,
        web_payload=web_payload,
    )

    merged = _merge_research(kb_payload, web_brief, web_payload.get("raw_sources") or [])
    merged["source"] = "merged" if kb_payload.get("raw_sources") else "web_fallback"
    return merged


async def _research_from_kb(
    *,
    org_id: str,
    company_name: str,
    industry_hint: str | None,
) -> dict[str, Any]:
    """Fan out a small bundle of KB searches: prior interactions with this
    company, similar past deals, value-prop / case-study relevance hints."""
    facets = {
        "prior_interactions": f"{company_name} prospect deal history",
        "similar_deals": (
            f"customer success {industry_hint}" if industry_hint else "customer success case study"
        ),
        "industry_context": (
            f"{industry_hint} sales playbook" if industry_hint else f"{company_name} industry overview"
        ),
    }
    results = await search_facets_concurrent(
        org_id=org_id, facets=facets, k=5, char_budget_per_facet=2500
    )

    context_block = build_context_block(results)
    sources = collect_sources(results)
    has_signal = len(context_block) >= _KB_USABLE_CHAR_THRESHOLD

    payload: dict[str, Any] = {
        "company_summary": None,
        "headcount_estimate": None,
        "industry": industry_hint,
        "tech_stack_hints": [],
        "pain_point_hypothesis": None,
        "competitors_detected": [],
        "similar_past_deals": [],
        "raw_sources": [
            {
                "type": "kb",
                "document_id": s["document_id"],
                "document_name": s["document_name"],
                "similarity": s.get("similarity"),
            }
            for s in sources
        ],
        "has_usable_signal": has_signal,
    }
    if not has_signal:
        return payload

    # Synthesize structured fields from the KB context.
    try:
        synth = await synthesize_json(
            system_prompt=_KB_BRIEF_SYSTEM_PROMPT,
            user_prompt=(
                f"Company: {company_name}\nIndustry hint: {industry_hint or '(none)'}\n\n"
                f"Knowledge base context:\n{context_block}\n\n"
                "Return JSON with the schema described in the system prompt. "
                "Use null for any field the context does not support."
            ),
            temperature=0.2,
        )
        if isinstance(synth, dict):
            payload.update(
                {
                    "company_summary": synth.get("company_summary"),
                    "headcount_estimate": synth.get("headcount_estimate"),
                    "industry": synth.get("industry") or industry_hint,
                    "tech_stack_hints": synth.get("tech_stack_hints") or [],
                    "pain_point_hypothesis": synth.get("pain_point_hypothesis"),
                    "competitors_detected": synth.get("competitors_detected") or [],
                    "similar_past_deals": synth.get("similar_past_deals") or [],
                }
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("sales.research kb_synthesis_failed err=%s", exc)

    return payload


async def _research_from_web(
    *,
    company_name: str,
    company_website: str | None,
) -> dict[str, Any]:
    return await tavily.company_brief(
        company_name=company_name,
        company_website=company_website,
    )


async def _synthesize_company_brief_from_web(
    *,
    company_name: str,
    contact_name: str | None,
    contact_title: str | None,
    web_payload: dict[str, Any],
) -> dict[str, Any]:
    """Distill Tavily snippets into the canonical research shape."""
    snippets = web_payload.get("raw_sources") or []
    answers = web_payload.get("answers") or []
    if not snippets and not answers:
        return {
            "company_summary": None,
            "headcount_estimate": None,
            "industry": None,
            "tech_stack_hints": [],
            "pain_point_hypothesis": None,
            "competitors_detected": [],
            "similar_past_deals": [],
        }

    snippets_block = "\n".join(
        f"- [{s.get('label')}] {s.get('title') or ''} :: {s.get('snippet') or ''} ({s.get('url') or ''})"
        for s in snippets[:12]
    )
    answers_block = "\n".join(f"- {a}" for a in answers[:4])
    contact_block = (
        f"Contact: {contact_name} ({contact_title})"
        if contact_name or contact_title
        else "Contact: (not provided)"
    )

    try:
        synth = await synthesize_json(
            system_prompt=_WEB_BRIEF_SYSTEM_PROMPT,
            user_prompt=(
                f"Company: {company_name}\n{contact_block}\n\n"
                f"Tavily synthesized answers:\n{answers_block or '(none)'}\n\n"
                f"Top snippets:\n{snippets_block}\n\n"
                "Return JSON matching the schema in the system prompt."
            ),
            temperature=0.2,
        )
        if isinstance(synth, dict):
            return synth
    except Exception as exc:  # noqa: BLE001
        log.warning("sales.research web_synthesis_failed err=%s", exc)

    return {
        "company_summary": None,
        "headcount_estimate": None,
        "industry": None,
        "tech_stack_hints": [],
        "pain_point_hypothesis": None,
        "competitors_detected": [],
        "similar_past_deals": [],
    }


def _merge_research(
    kb: dict[str, Any],
    web_brief: dict[str, Any],
    web_sources: list[dict[str, Any]],
) -> dict[str, Any]:
    """KB takes precedence where present; web fills gaps. raw_sources concat."""
    def _pick(field: str) -> Any:
        return kb.get(field) or web_brief.get(field)

    merged_sources: list[dict[str, Any]] = list(kb.get("raw_sources") or [])
    for s in web_sources:
        merged_sources.append({"type": "web", **s})

    return {
        "company_summary": _pick("company_summary"),
        "headcount_estimate": _pick("headcount_estimate"),
        "industry": _pick("industry"),
        "tech_stack_hints": _merge_lists(
            kb.get("tech_stack_hints"), web_brief.get("tech_stack_hints")
        ),
        "pain_point_hypothesis": _pick("pain_point_hypothesis"),
        "competitors_detected": _merge_lists(
            kb.get("competitors_detected"), web_brief.get("competitors_detected")
        ),
        "similar_past_deals": kb.get("similar_past_deals") or [],
        "raw_sources": merged_sources,
        "has_usable_signal": True,  # we got web signal at minimum
    }


def _merge_lists(a: list | None, b: list | None) -> list:
    seen: set[str] = set()
    out: list = []
    for src in (a or [], b or []):
        for v in src:
            key = str(v).strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(v)
    return out


# ── ICP scoring ──────────────────────────────────────────────────────────────


async def score_icp(
    *,
    org_id: str,
    company_name: str,
    research: dict[str, Any],
    icp_doc_id: str,
) -> dict[str, Any]:
    """Score the researched company 1-10 against the org's ICP doc.

    Pulls ICP doc content via hybrid_search (filtered to that document_id),
    feeds research summary, asks LLM for {score, rationale}.
    """
    # Fetch ICP doc content via hybrid_search with document_id filter.
    facets = {
        "icp_definition": "ideal customer profile criteria",
        "icp_disqualifiers": "disqualifying customer attributes",
    }
    # Pin both searches to the ICP doc so they return its content sections.
    from app.database import get_service_client
    from app.services.retrieval.hybrid_search import hybrid_search

    svc = get_service_client()
    icp_chunks: list[str] = []
    for facet_name, query in facets.items():
        try:
            hits = await hybrid_search(
                query=query,
                org_id=org_id,
                client=svc,
                k=8,
                document_id=icp_doc_id,
            )
            for h in hits:
                content = (h.content or "").strip()
                if content:
                    icp_chunks.append(content)
        except Exception as exc:  # noqa: BLE001
            log.warning("sales.icp.fetch_chunks_failed facet=%s err=%s", facet_name, exc)

    if not icp_chunks:
        # Defensive — caller should have validated ICP doc exists upstream.
        return {"score": None, "rationale": "ICP document content unavailable."}

    # De-dup while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for c in icp_chunks:
        if c not in seen:
            seen.add(c)
            deduped.append(c)
    icp_text = "\n\n---\n\n".join(deduped[:8])

    summary = research.get("company_summary") or "(no summary)"
    industry = research.get("industry") or "(unknown)"
    headcount = research.get("headcount_estimate") or "(unknown)"
    tech_stack = ", ".join(research.get("tech_stack_hints") or []) or "(unknown)"
    pain = research.get("pain_point_hypothesis") or "(none inferred)"

    user_prompt = (
        f"Company under evaluation: {company_name}\n"
        f"Industry: {industry}\n"
        f"Headcount estimate: {headcount}\n"
        f"Tech stack hints: {tech_stack}\n"
        f"Pain point hypothesis: {pain}\n"
        f"Company summary: {summary}\n\n"
        f"Your company's ICP definition (excerpts):\n{icp_text}\n\n"
        'Return JSON: {"score": <integer 1-10>, "rationale": "<2-3 sentences explaining the score, '
        "referencing specific ICP criteria the prospect meets or misses>\"}"
    )
    try:
        synth = await synthesize_json(
            system_prompt=_ICP_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.2,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("sales.icp.synth_failed company=%s err=%s", company_name, exc)
        return {"score": None, "rationale": f"ICP scoring failed: {exc}"}

    if not isinstance(synth, dict):
        return {"score": None, "rationale": "ICP scoring returned invalid shape."}
    score_raw = synth.get("score")
    rationale = synth.get("rationale") or ""
    score: int | None
    try:
        score = int(score_raw) if score_raw is not None else None
        if score is not None:
            score = max(1, min(10, score))
    except (TypeError, ValueError):
        score = None
    return {"score": score, "rationale": rationale}


# ── Prompts ──────────────────────────────────────────────────────────────────


_KB_BRIEF_SYSTEM_PROMPT = """You are a sales research analyst summarizing a prospect company using ONLY the provided knowledge base context.

Return strict JSON with this schema:
{
  "company_summary": "<2-3 sentence summary>",
  "headcount_estimate": "<size band, e.g. 50-200 employees, or null>",
  "industry": "<industry, or null>",
  "tech_stack_hints": ["<technology>", ...],
  "pain_point_hypothesis": "<single sentence hypothesis based on the KB context, or null>",
  "competitors_detected": ["<competitor name>", ...],
  "similar_past_deals": [{"company_name": "<name>", "outcome": "<won|lost|unknown>"}]
}

Rules:
- Use null for fields the KB context does not support.
- Do not fabricate. If the KB has nothing on the company, return mostly nulls.
- Output JSON only, no markdown fences."""


_WEB_BRIEF_SYSTEM_PROMPT = """You are a sales research analyst distilling public web snippets into a structured prospect brief.

Return strict JSON with this schema:
{
  "company_summary": "<2-3 sentence summary>",
  "headcount_estimate": "<size band, e.g. 50-200 employees, or null>",
  "industry": "<industry, or null>",
  "tech_stack_hints": ["<technology>", ...],
  "pain_point_hypothesis": "<single sentence hypothesis the snippets support, or null>",
  "competitors_detected": ["<competitor name>", ...]
}

Rules:
- Cite only what the snippets support — do not invent headcount, tech, etc.
- Tech stack: only include technologies that the snippets explicitly mention.
- Output JSON only, no markdown fences."""


_ICP_SYSTEM_PROMPT = """You are scoring a prospect company against the user's Ideal Customer Profile (ICP).

Score 1-10:
  1-3 = poor fit (disqualifying criteria missing or red flags present)
  4-6 = marginal fit (some criteria met, significant gaps)
  7-8 = strong fit (most criteria met, no major red flags)
  9-10 = exceptional fit (all key criteria met, signal of urgency)

Rationale must reference specific ICP criteria — do not give a vague justification.
Output JSON only: {"score": int, "rationale": str}."""
