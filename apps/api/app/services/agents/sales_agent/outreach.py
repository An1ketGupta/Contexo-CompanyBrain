"""Stage 2 — Outreach drafting.

Generates a cold email (subject + body) and an optional LinkedIn connection
message, both grounded in the org's KB (value props, case studies, tone
guide) and the research from Stage 1.

The agent calls `draft_outreach()` then persists the result via storage
helpers + the deal_approvals gate.

Also generates follow-up and check-in drafts (same shape, different prompt)
since they share the rep-review gate.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.agents.kb_synthesis import (
    build_context_block,
    search_facets_concurrent,
    synthesize_json,
)

log = logging.getLogger(__name__)


# ── Cold outreach draft ──────────────────────────────────────────────────────


async def draft_cold_outreach(
    *,
    org_id: str,
    company_name: str,
    contact_name: str | None,
    contact_title: str | None,
    industry: str | None,
    icp_score: int | None,
    icp_rationale: str | None,
    research: dict[str, Any],
) -> dict[str, Any]:
    """Generate the initial cold outreach.

    KB facets pulled:
      * value_props — what we sell, positioning
      * case_studies — wins relevant to this prospect's industry/segment
      * tone_guide — voice / formality
      * objection_preempts — anticipated objections to address upfront
    """
    industry_hint = industry or "(unspecified industry)"
    facets = {
        "value_props": "value propositions key benefits",
        "case_studies": f"customer success case study {industry_hint}",
        "tone_guide": "sales email tone of voice guidelines",
        "objection_preempts": "common sales objections and responses",
    }
    facet_results = await search_facets_concurrent(
        org_id=org_id, facets=facets, k=4, char_budget_per_facet=2000
    )
    context_block = build_context_block(facet_results)

    research_block = _format_research_for_prompt(research)

    user_prompt = (
        f"Prospect company: {company_name}\n"
        f"Contact: {contact_name or '(unknown contact)'} — {contact_title or '(unknown title)'}\n"
        f"ICP score: {icp_score if icp_score is not None else 'n/a'} "
        f"(rationale: {icp_rationale or 'n/a'})\n\n"
        f"Research on the prospect:\n{research_block}\n\n"
        f"Knowledge-base context (your company's value props, case studies, tone, "
        f"objection handling):\n{context_block or '(empty)'}\n\n"
        "Draft the cold outreach. Output JSON with this schema:\n"
        '{"subject": "<email subject, 5-9 words>", '
        '"email_body": "<plain-text email body, 100-200 words, '
        'opening that names a specific signal from the research, one concrete value statement '
        'grounded in a case study if available, and a single low-friction CTA>", '
        '"linkedin_body": "<LinkedIn connection note, 1-2 sentences, max 280 chars, '
        'less formal, no CTA>"}\n'
    )

    synth = await synthesize_json(
        system_prompt=_COLD_OUTREACH_PROMPT,
        user_prompt=user_prompt,
        temperature=0.5,
    )
    return _coerce_draft(synth)


# ── Follow-up draft ──────────────────────────────────────────────────────────


async def draft_followup(
    *,
    org_id: str,
    company_name: str,
    contact_name: str | None,
    contact_title: str | None,
    prior_subject: str | None,
    prior_email_body: str | None,
    followup_index: int,
    days_since_outreach: int,
) -> dict[str, Any]:
    """Generate follow-up email N (1-indexed). Each follow-up is shorter and
    lower-pressure than the previous. We feed the prior outreach so the model
    doesn't recycle the same opening.
    """
    facets = {
        "tone_guide": "sales email tone of voice guidelines",
        "followup_examples": "follow up email examples",
    }
    facet_results = await search_facets_concurrent(
        org_id=org_id, facets=facets, k=3, char_budget_per_facet=1500
    )
    context_block = build_context_block(facet_results)

    user_prompt = (
        f"Prospect: {company_name} (contact: {contact_name or 'unknown'}, "
        f"{contact_title or 'unknown title'})\n"
        f"Follow-up #{followup_index} of 3. Days since original outreach: "
        f"{days_since_outreach}.\n\n"
        f"Original outreach subject: {prior_subject or '(unknown)'}\n"
        f"Original outreach body:\n{prior_email_body or '(unknown)'}\n\n"
        f"Knowledge-base tone / examples:\n{context_block or '(empty)'}\n\n"
        "Draft the follow-up. Return JSON: "
        '{"subject": "<reply-style subject prefixed with Re:>", '
        '"email_body": "<plain-text body, 50-100 words, lower-pressure, '
        'do NOT repeat the original opening, single short CTA>"}\n'
        "Do not include a linkedin_body."
    )
    synth = await synthesize_json(
        system_prompt=_FOLLOWUP_PROMPT.format(index=followup_index),
        user_prompt=user_prompt,
        temperature=0.5,
    )
    return _coerce_draft(synth)


# ── Check-in draft (post-proposal) ───────────────────────────────────────────


async def draft_checkin_nudge(
    *,
    org_id: str,
    company_name: str,
    contact_name: str | None,
    checkin_index: int,
    days_since_proposal: int,
) -> dict[str, Any]:
    facets = {
        "tone_guide": "sales email tone of voice guidelines",
    }
    facet_results = await search_facets_concurrent(
        org_id=org_id, facets=facets, k=2, char_budget_per_facet=1500
    )
    context_block = build_context_block(facet_results)

    user_prompt = (
        f"Prospect: {company_name} (contact: {contact_name or 'unknown'})\n"
        f"Check-in #{checkin_index} of 2 after proposal sent. Days since: {days_since_proposal}.\n\n"
        f"Knowledge-base tone:\n{context_block or '(empty)'}\n\n"
        "Draft the check-in. Return JSON: "
        '{"subject": "<brief subject>", '
        '"email_body": "<plain-text body, 60-100 words, '
        'references the proposal, asks about status/timeline, offers to answer questions, '
        'no aggressive close>"}\n'
    )
    synth = await synthesize_json(
        system_prompt=_CHECKIN_PROMPT.format(index=checkin_index),
        user_prompt=user_prompt,
        temperature=0.4,
    )
    return _coerce_draft(synth)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _format_research_for_prompt(research: dict[str, Any] | None) -> str:
    if not research:
        return "(no research available)"
    lines = []
    if v := research.get("company_summary"):
        lines.append(f"Summary: {v}")
    if v := research.get("headcount_estimate"):
        lines.append(f"Size: {v}")
    if v := research.get("industry"):
        lines.append(f"Industry: {v}")
    if v := research.get("tech_stack_hints"):
        lines.append(f"Tech stack hints: {', '.join(str(x) for x in v)}")
    if v := research.get("pain_point_hypothesis"):
        lines.append(f"Pain point hypothesis: {v}")
    if v := research.get("competitors_detected"):
        lines.append(f"Competitors detected: {', '.join(str(x) for x in v)}")
    return "\n".join(lines) if lines else "(empty)"


def _coerce_draft(synth: Any) -> dict[str, Any]:
    """Normalize the LLM response to a stable shape with safe defaults."""
    if not isinstance(synth, dict):
        return {"subject": "", "email_body": "", "linkedin_body": None}
    return {
        "subject": (synth.get("subject") or "").strip()[:500],
        "email_body": (synth.get("email_body") or "").strip()[:10000],
        "linkedin_body": (
            (synth.get("linkedin_body") or "").strip()[:2000] if synth.get("linkedin_body") else None
        ),
    }


# ── System prompts ───────────────────────────────────────────────────────────


_COLD_OUTREACH_PROMPT = """You are a senior B2B sales rep writing personalized cold outreach.

Rules:
- Personalize using a specific signal from the research — do not write generic openings.
- Lead with the prospect's context, not your company's. They do not care who you are yet.
- One value statement, grounded in a customer win when available. No marketing fluff.
- One low-friction CTA — a 15-minute call, a single question, or a relevant resource.
- Tone: peer-to-peer, confident, no exclamation marks, no superlatives.
- Use only information present in the research or knowledge base. Do not invent facts.
- Output strict JSON, no markdown."""


_FOLLOWUP_PROMPT = """You are writing follow-up email #{index} (of 3) on a cold sales thread that has not received a reply.

Rules:
- Do NOT recycle the opening from the original outreach. Acknowledge the prior email briefly or skip the salutation entirely.
- Each subsequent follow-up should be shorter and lower-pressure than the previous.
- Vary the angle: if the original led with the prospect's pain, this one can lead with a quick proof point or a question.
- Single short CTA — a yes/no question or "is this a priority right now?".
- Output strict JSON, no markdown."""


_CHECKIN_PROMPT = """You are writing check-in nudge #{index} (of 2) after a proposal was sent and there has been no response.

Rules:
- Professional, not pushy. Assume the prospect is busy, not ghosting.
- Reference the proposal explicitly.
- Offer to answer questions or jump on a quick call to walk through it.
- Tone: collaborative, no urgency language ("ASAP", "before EOD").
- Output strict JSON, no markdown."""
