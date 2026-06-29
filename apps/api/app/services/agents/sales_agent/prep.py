"""Stage 4 — Meeting prep brief.

Produces a structured brief the rep can scan 5 minutes before a meeting:
talking points, likely objections + responses, relevant case studies,
pricing scenarios, and the prior history with this deal (research + last
call summary if this is a follow-up meeting).

Reuses `precall_brief.generate_precall_brief` style — concurrent facet
searches + one Gemini synthesis — but layered onto the deal's stored
research and call summary so the brief reflects what we already know
about *this* prospect, not a generic precall on a name.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.services.agents.kb_synthesis import (
    build_context_block,
    collect_sources,
    search_facets_concurrent,
    synthesize_json,
)

log = logging.getLogger(__name__)


_PREP_SYSTEM = """You are a sales engineer preparing a rep for a meeting with a specific prospect.

Constraints:
- Quote concrete numbers, customer names, and feature names from the company-internal context when available.
- Reference the prospect's research / prior-call notes — do not produce a generic brief.
- Each bullet is one sentence. No marketing fluff.
- Empty array if a section has no supporting context. Do not invent.

Output strict JSON only, no markdown fences:
{
  "agenda": ["string", ...],
  "talking_points": ["string", ...],
  "objections": [{"objection": "string", "response": "string"}, ...],
  "case_studies": [{"title": "string", "takeaway": "string"}, ...],
  "pricing_scenarios": ["string", ...],
  "questions_to_ask": ["string", ...],
  "risks": ["string", ...]
}
""".strip()


async def build_meeting_prep_brief(
    *,
    org_id: str,
    company_name: str,
    contact_name: str | None,
    contact_title: str | None,
    industry: str | None,
    meeting_count: int,
    research: dict[str, Any] | None,
    prior_call_summary: str | None,
    prior_bant: dict[str, Any] | None,
    prior_objections: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    industry_hint = industry or company_name
    facets = {
        "talking_points": f"talking points positioning competitive advantage {industry_hint}",
        "objections": f"objection handling pricing security concerns {industry_hint}",
        "case_studies": f"customer success story {industry_hint}",
        "pricing_scenarios": f"pricing tiers enterprise scenario {industry_hint}",
        "discovery_questions": "discovery questions BANT qualification",
    }
    facet_results = await search_facets_concurrent(
        org_id=org_id, facets=facets, k=5, char_budget_per_facet=2500
    )
    context_block = build_context_block(facet_results)
    sources = collect_sources(facet_results)

    research_block = _format_research(research) if research else "(no prior research)"
    prior_block = _format_prior_call(prior_call_summary, prior_bant, prior_objections)

    user_prompt = (
        f"## Prospect\n{company_name}\n"
        f"Contact: {contact_name or 'unknown'} ({contact_title or 'unknown title'})\n"
        f"Industry: {industry or 'unknown'}\n"
        f"Meeting #{meeting_count + 1} on this deal.\n\n"
        f"## Research on the prospect\n{research_block}\n\n"
        f"## Prior call context (if any)\n{prior_block}\n\n"
        f"## Company-internal knowledge base context\n{context_block or '(empty)'}\n\n"
        "Output strict JSON matching the schema in your system prompt."
    )

    try:
        result = await synthesize_json(
            system_prompt=_PREP_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.3,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("sales.prep.synth_failed company=%s", company_name)
        raise RuntimeError(f"prep_synthesis_failed: {exc}") from exc

    if not isinstance(result, dict):
        raise RuntimeError("prep_synthesis_returned_non_object")

    return {
        "agenda": [x for x in result.get("agenda") or [] if isinstance(x, str)],
        "talking_points": [x for x in result.get("talking_points") or [] if isinstance(x, str)],
        "objections": [x for x in result.get("objections") or [] if isinstance(x, dict)],
        "case_studies": [x for x in result.get("case_studies") or [] if isinstance(x, dict)],
        "pricing_scenarios": [
            x for x in result.get("pricing_scenarios") or [] if isinstance(x, str)
        ],
        "questions_to_ask": [
            x for x in result.get("questions_to_ask") or [] if isinstance(x, str)
        ],
        "risks": [x for x in result.get("risks") or [] if isinstance(x, str)],
        "sources": sources,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def _format_research(research: dict[str, Any]) -> str:
    lines = []
    if v := research.get("company_summary"):
        lines.append(f"Summary: {v}")
    if v := research.get("industry"):
        lines.append(f"Industry: {v}")
    if v := research.get("headcount_estimate"):
        lines.append(f"Size: {v}")
    if v := research.get("tech_stack_hints"):
        lines.append(f"Tech stack: {', '.join(str(x) for x in v)}")
    if v := research.get("pain_point_hypothesis"):
        lines.append(f"Pain point: {v}")
    if v := research.get("competitors_detected"):
        lines.append(f"Competitors: {', '.join(str(x) for x in v)}")
    return "\n".join(lines) or "(empty)"


def _format_prior_call(
    summary: str | None,
    bant: dict[str, Any] | None,
    objections: list[dict[str, Any]] | None,
) -> str:
    parts: list[str] = []
    if summary:
        parts.append(f"Summary: {summary}")
    if bant:
        for k in ("budget", "authority", "need", "timeline"):
            v = bant.get(k)
            if v:
                parts.append(f"{k.capitalize()}: {v}")
    if objections:
        joined = "; ".join(
            f"{o.get('objection', '')}" for o in objections if isinstance(o, dict)
        )
        if joined:
            parts.append(f"Objections raised: {joined}")
    return "\n".join(parts) or "(no prior call)"
