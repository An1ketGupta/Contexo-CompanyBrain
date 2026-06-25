"""Pre-call brief service (#22 — Sales Enablement, first half).

The rep clicks "Pre-call brief" with a prospect name + company and we run
four KB searches *concurrently* to produce a one-page brief:

    talking_points       — pricing + positioning highlights, persuasive framing
    objections           — likely objections + our standard responses
    case_studies         — similar customers, headline outcomes
    pricing_scenario     — concrete numbers the rep can quote

Latency budget matters here — a rep clicks this 5 minutes before a call.
We aim for <8s end-to-end: 4 facet searches in parallel (~1s p95 each
including hybrid SQL) + one Gemini synthesis (~3-5s on Flash). The brief is
NOT persisted — repeated runs are cheap, and writes here would pollute the
conversation/messages telemetry without giving the rep anything.
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


_PRECALL_SYSTEM = """You are a sales engineer. The rep has 5 minutes before a call with a prospect. You produce a one-page brief grounded ONLY in the company-internal context provided.

Constraints:
- Be specific. Quote numbers, customer names, and feature names from the context if present.
- If a section has no relevant context, return an empty array — do NOT invent.
- Bullets are short (one sentence each). No prose paragraphs.

Output JSON only, no prose around it:
{
  "talking_points": ["string", ...],
  "objections": [{"objection": "string", "response": "string"}, ...],
  "case_studies": [{"title": "string", "takeaway": "string"}, ...],
  "pricing_scenario": ["string", ...]
}
""".strip()


async def generate_precall_brief(
    *,
    org_id: str,
    prospect_name: str,
    company: str,
    notes: str | None = None,
) -> dict[str, Any]:
    """Concurrent facet search + one Gemini synthesis. Returns the sectioned
    brief plus a deduplicated sources list."""
    facets = {
        "talking_points": f"talking points positioning competitive advantage {company}",
        "objections": f"objection handling pricing security concerns {company}",
        "case_studies": f"customer case studies success stories similar to {company}",
        "pricing_scenario": f"pricing tiers SKU comparison enterprise scenario {company}",
    }
    facet_results = await search_facets_concurrent(
        org_id=org_id, facets=facets, k=6, char_budget_per_facet=3000
    )
    context_block = build_context_block(facet_results)
    sources = collect_sources(facet_results)

    user_prompt = (
        f"## Prospect\n{prospect_name} at {company}\n"
        + (f"\n## Rep notes\n{notes}\n" if notes else "")
        + (f"\n## Company-internal context\n{context_block}\n" if context_block else "")
        + "\n## Output\nJSON only, matching the schema in your system prompt."
    )

    try:
        result = await synthesize_json(
            system_prompt=_PRECALL_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.3,
            timeout=45.0,
        )
    except Exception as exc:
        log.exception("precall.synthesis_failed org=%s prospect=%s", org_id, prospect_name)
        raise RuntimeError(f"precall_synthesis_failed: {exc}") from exc

    if not isinstance(result, dict):
        raise RuntimeError("precall_synthesis_returned_non_object")

    return {
        "prospect_name": prospect_name,
        "company": company,
        "talking_points": [x for x in result.get("talking_points") or [] if isinstance(x, str)],
        "objections": [x for x in result.get("objections") or [] if isinstance(x, dict)],
        "case_studies": [x for x in result.get("case_studies") or [] if isinstance(x, dict)],
        "pricing_scenario": [x for x in result.get("pricing_scenario") or [] if isinstance(x, str)],
        "sources": sources,
        "generated_at": datetime.now(UTC).isoformat(),
    }
