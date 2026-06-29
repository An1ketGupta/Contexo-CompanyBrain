"""Stage 6 — Proposal generation.

Takes the org's `proposal` (and optionally `sow`, `pricing_sheet`) DOCX
templates and the deal context, derives a structured render context via
LLM (executive summary, scope bullets, pricing scenario), then renders
the DOCX via docxtpl + Gotenberg.

Output shape mirrors onboarding_v2:
    (filled_docx_bytes, pdf_bytes, render_context)

The caller (the agent) uploads to Supabase Storage at the standard sales
prefix and writes the deal_documents row.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.services.agents.kb_synthesis import (
    build_context_block,
    search_facets_concurrent,
    synthesize_json,
)
from app.services.pdf import render_docx_template_to_pdf

log = logging.getLogger(__name__)


_PROPOSAL_CONTEXT_SYSTEM = """You are a senior solutions engineer drafting the variable content for a sales proposal.

You will receive:
  * The deal's research + call-summary BANT + objections
  * Knowledge-base context (value props, case studies, pricing scenarios)
  * A list of placeholder variable names the customer's DOCX template expects

Produce a JSON object whose KEYS are exactly the placeholder names and whose VALUES are the rendered strings or simple lists. Reuse text from the call summary verbatim where appropriate. If a placeholder cannot be supported by the provided context, return an empty string for it (NOT a guess).

Rules:
- Do not invent customer names, numbers, or features.
- Plain text only — no markdown, no HTML, no fences.
- Output strict JSON only.
""".strip()


# Default template variables we hand the LLM if the org hasn't documented
# their own. Mirrors the names we recommend in ONBOARDING_TEMPLATE_VARS.md.
DEFAULT_PROPOSAL_VARIABLES = [
    "executive_summary",
    "problem_statement",
    "proposed_solution",
    "scope_bullets",
    "deliverables_bullets",
    "timeline_summary",
    "pricing_summary",
    "investment_amount",
    "investment_currency",
    "success_metrics",
    "next_steps",
    "company_name",
    "contact_name",
    "contact_title",
    "today_date",
    "valid_until_date",
]


async def synthesize_proposal_context(
    *,
    org_id: str,
    company_name: str,
    contact_name: str | None,
    contact_title: str | None,
    industry: str | None,
    research: dict[str, Any] | None,
    call_summary: str | None,
    bant: dict[str, Any] | None,
    next_steps: list[dict[str, Any]] | None,
    objections: list[dict[str, Any]] | None,
    deal_value_amount: float | None,
    deal_value_currency: str | None,
    template_variables: list[str] | None = None,
) -> dict[str, Any]:
    """Ask the LLM to fill in the variable bag the DOCX template will use."""
    variables = template_variables or list(DEFAULT_PROPOSAL_VARIABLES)
    industry_hint = industry or company_name

    facets = {
        "value_props": "value propositions key benefits",
        "case_studies": f"customer case study {industry_hint}",
        "pricing_scenarios": "pricing tiers enterprise scenario",
        "scope_examples": "project scope deliverables timeline",
    }
    facet_results = await search_facets_concurrent(
        org_id=org_id, facets=facets, k=4, char_budget_per_facet=2500
    )
    context_block = build_context_block(facet_results)

    research_block = _format_research(research) if research else "(empty)"
    bant_block = _format_bant(bant) if bant else "(empty)"
    objections_block = _format_objections(objections) if objections else "(empty)"
    next_steps_block = _format_next_steps(next_steps) if next_steps else "(empty)"
    investment_hint = (
        f"{deal_value_currency or 'USD'} {deal_value_amount:,.2f}"
        if deal_value_amount
        else "(rep did not enter a value yet)"
    )

    user_prompt = (
        f"## Deal\nCompany: {company_name}\n"
        f"Contact: {contact_name or 'unknown'} ({contact_title or 'unknown title'})\n"
        f"Industry: {industry or 'unknown'}\n"
        f"Investment to use: {investment_hint}\n\n"
        f"## Research\n{research_block}\n\n"
        f"## Last call summary\n{call_summary or '(none)'}\n\n"
        f"## BANT\n{bant_block}\n\n"
        f"## Objections raised\n{objections_block}\n\n"
        f"## Agreed next steps\n{next_steps_block}\n\n"
        f"## Knowledge base context\n{context_block or '(empty)'}\n\n"
        f"## Template variables to fill\n{', '.join(variables)}\n\n"
        "Return strict JSON with ONLY those keys. Use empty string for any "
        "key the context does not support."
    )

    try:
        filled = await synthesize_json(
            system_prompt=_PROPOSAL_CONTEXT_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.25,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("sales.proposal.context_synth_failed company=%s", company_name)
        raise RuntimeError(f"proposal_context_failed: {exc}") from exc

    if not isinstance(filled, dict):
        filled = {}

    today = datetime.now(UTC).date().isoformat()
    rendered_context: dict[str, Any] = {k: (filled.get(k) or "") for k in variables}
    # Always overwrite the structural keys with deterministic values rather
    # than trusting the LLM for them.
    rendered_context["company_name"] = company_name
    rendered_context["contact_name"] = contact_name or ""
    rendered_context["contact_title"] = contact_title or ""
    rendered_context["today_date"] = today
    if deal_value_amount is not None:
        rendered_context["investment_amount"] = (
            f"{deal_value_currency or 'USD'} {deal_value_amount:,.2f}"
        )
        rendered_context["investment_currency"] = deal_value_currency or "USD"
    return rendered_context


async def render_proposal_docx(
    *,
    template_bytes: bytes,
    context: dict[str, Any],
    template_kind: str = "proposal",
) -> tuple[bytes, bytes]:
    """Thin wrapper over the shared PDF service. Kept so callers in the sales
    agent reach for a sales-specific function rather than threading the
    template_kind directly."""
    filled_docx, pdf_bytes = await render_docx_template_to_pdf(
        template_bytes=template_bytes,
        context=context,
        strict=False,  # proposals may have placeholders that vary by template
        template_kind=template_kind,
    )
    return filled_docx, pdf_bytes


# ── Format helpers ──────────────────────────────────────────────────────────


def _format_research(research: dict[str, Any]) -> str:
    lines = []
    for k in ("company_summary", "industry", "headcount_estimate", "pain_point_hypothesis"):
        v = research.get(k)
        if v:
            lines.append(f"{k}: {v}")
    return "\n".join(lines) or "(empty)"


def _format_bant(bant: dict[str, Any]) -> str:
    return "\n".join(
        f"{k}: {bant.get(k)}" for k in ("budget", "authority", "need", "timeline") if bant.get(k)
    ) or "(empty)"


def _format_objections(objections: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"- {o.get('objection')} → {o.get('rep_response') or '(no response logged)'}"
        for o in objections
        if isinstance(o, dict) and o.get("objection")
    ) or "(empty)"


def _format_next_steps(next_steps: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"- [{s.get('owner') or 'unspecified'}] {s.get('action') or ''} "
        f"(due: {s.get('due_hint') or 'tbd'})"
        for s in next_steps
        if isinstance(s, dict) and s.get("action")
    ) or "(empty)"
