"""Stage 5 — Call summary + BANT extraction.

Takes a rep-supplied transcript (or notes blob) and produces:

  * A 2-3 sentence summary.
  * BANT extraction (Budget / Authority / Need / Timeline).
  * Objections raised and how the rep handled them.
  * Next steps with owner + due date hints.
  * A recommended next stage — `propose | another_call | qualify_out`.

We feed the LLM the prior research / prep brief so it can be specific (e.g.
"prospect mentioned switching from Hubspot, matching the competitor we
detected"). Output is a structured JSON object the agent persists into
`deal_runs.{call_summary, bant_json, objections_json, next_steps_json,
recommended_stage}`.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.agents.kb_synthesis import synthesize_json

log = logging.getLogger(__name__)


_CALL_SUMMARY_SYSTEM = """You are a senior sales engineer. The rep just finished a call with a prospect and pasted the transcript or notes. Extract structured BANT + next-step guidance.

Rules:
- Use ONLY information present in the transcript / notes. Do not infer beyond what was said.
- For BANT fields, if the call doesn't cover one, return null — do not guess.
- `recommended_stage` MUST be one of: 'propose' (qualified, ready for proposal), 'another_call' (need more discovery / stakeholder), 'qualify_out' (clear disqualifier surfaced).
- Output strict JSON only, no markdown fences.

Schema:
{
  "summary": "<2-3 sentence executive summary>",
  "bant": {
    "budget": "<string or null>",
    "authority": "<string or null>",
    "need": "<string or null>",
    "timeline": "<string or null>"
  },
  "objections": [{"objection": "string", "rep_response": "string or null"}],
  "next_steps": [{"action": "string", "owner": "rep|prospect", "due_hint": "string or null"}],
  "recommended_stage": "propose|another_call|qualify_out",
  "recommended_stage_rationale": "<one sentence>"
}
""".strip()


async def extract_call_summary(
    *,
    company_name: str,
    contact_name: str | None,
    transcript: str,
    research: dict[str, Any] | None,
    prep_brief: dict[str, Any] | None,
) -> dict[str, Any]:
    transcript = (transcript or "").strip()
    if not transcript:
        raise ValueError("transcript_required")

    # Bound the transcript so a 50k-word raw paste doesn't blow up tokens.
    if len(transcript) > 40_000:
        transcript = transcript[:40_000] + "\n\n[... transcript truncated ...]"

    research_block = _format_research(research) if research else "(no prior research)"
    prep_block = _format_prep(prep_brief) if prep_brief else "(no prep brief)"

    user_prompt = (
        f"## Deal\nCompany: {company_name}\nContact: {contact_name or 'unknown'}\n\n"
        f"## Prior research\n{research_block}\n\n"
        f"## Prep brief\n{prep_block}\n\n"
        f"## Call transcript / notes\n{transcript}\n\n"
        "Return JSON matching the schema in your system prompt."
    )

    try:
        result = await synthesize_json(
            system_prompt=_CALL_SUMMARY_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.2,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("sales.call_summary.synth_failed company=%s", company_name)
        raise RuntimeError(f"call_summary_synthesis_failed: {exc}") from exc

    if not isinstance(result, dict):
        raise RuntimeError("call_summary_returned_non_object")

    bant = result.get("bant") or {}
    recommended = (result.get("recommended_stage") or "").strip().lower()
    if recommended not in ("propose", "another_call", "qualify_out"):
        recommended = "another_call"

    return {
        "summary": (result.get("summary") or "").strip()[:4000],
        "bant": {
            "budget": bant.get("budget"),
            "authority": bant.get("authority"),
            "need": bant.get("need"),
            "timeline": bant.get("timeline"),
        },
        "objections": [
            x for x in result.get("objections") or [] if isinstance(x, dict)
        ],
        "next_steps": [
            x for x in result.get("next_steps") or [] if isinstance(x, dict)
        ],
        "recommended_stage": recommended,
        "recommended_stage_rationale": (
            result.get("recommended_stage_rationale") or ""
        ).strip()[:1000],
    }


def _format_research(research: dict[str, Any]) -> str:
    lines = []
    for k in ("company_summary", "industry", "pain_point_hypothesis"):
        v = research.get(k)
        if v:
            lines.append(f"{k}: {v}")
    return "\n".join(lines) or "(empty)"


def _format_prep(prep: dict[str, Any]) -> str:
    parts: list[str] = []
    for k in ("talking_points", "objections", "questions_to_ask"):
        v = prep.get(k)
        if v:
            joined = "; ".join(str(x) for x in v[:5])
            parts.append(f"{k}: {joined}")
    return "\n".join(parts) or "(empty)"
