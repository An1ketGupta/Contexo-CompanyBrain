"""Recruiting Agent (#20) — service layer.

Pipeline:
  generate_job_requisition()
      KB search across 4 facets (templates, compensation, requirements,
      team-structure) → Gemini produces 5 JD variants with distinct tones.
      Persists to job_requisitions with status='draft'.

  publish_requisition()
      Posts the selected variant to one or more ATS platforms (fans out in
      parallel; one platform failing does NOT abort the others), then runs
      four best-effort side-effects in parallel:
        * Notion hiring tracker page (lists every ATS link)
        * LinkedIn sourcing drafts + Recruiter search URLs
        * Slack notification to the hiring manager / channel
        * React Email to the hiring manager
      The requisition is marked `published` if at least one ATS posting
      succeeded — failures are surfaced inline in `ats_postings` so the
      recruiter can see which platforms still need attention. Status only
      flips to `failed` when every selected platform failed.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote_plus

from app.database import get_service_client
from app.models.recruiting import (
    AtsPlatform,
    JdVariant,
    LinkedinSearch,
    SourcingTemplate,
)
from app.services.agents.kb_synthesis import (
    build_context_block,
    collect_sources,
    search_facets_concurrent,
    synthesize_json,
    synthesize_text,
)
from app.services.integrations import posting_registry
from app.services.integrations.ats import ashby, greenhouse, lever
from app.services.recruiting import audit_log, mapping_resolver

log = logging.getLogger(__name__)


# ── Org facts ────────────────────────────────────────────────────────────────


async def _fetch_org_facts(org_id: str) -> dict[str, str | None]:
    """Pull structured org metadata that the LLM must treat as authoritative.

    These fields were captured during onboarding (migration 025) and are the
    ground truth for company name, size, and industry. The recruiting agent
    must never fabricate values that already exist here.
    """
    svc = get_service_client()
    try:
        result = await asyncio.to_thread(
            lambda: svc.table("organizations")
            .select("name, company_size, industry")
            .eq("id", org_id)
            .maybe_single()
            .execute()
        )
        row = result.data or {} if result else {}
    except Exception as exc:
        log.warning("recruiting.org_facts_fetch_failed org=%s err=%s", org_id, exc)
        row = {}
    return {
        "name": row.get("name") or None,
        "company_size": row.get("company_size") or None,
        "industry": row.get("industry") or None,
    }


def _build_org_facts_block(facts: dict[str, str | None]) -> str:
    """Render org facts as a bullet list. Omit keys with no value so the LLM
    doesn't receive blank lines that imply the field exists but is empty."""
    lines: list[str] = []
    if facts.get("name"):
        lines.append(f"- Company name: {facts['name']}")
    if facts.get("industry"):
        lines.append(f"- Industry: {facts['industry']}")
    if facts.get("company_size"):
        lines.append(f"- Company size: {facts['company_size']}")
    return "\n".join(lines)


# ── 1. Generate ──────────────────────────────────────────────────────────────


_THREE_TONES = [
    "startup-casual",
    "enterprise-formal",
    "concise-pragmatic",
]


_GENERATE_SYSTEM = """
You are a senior technical recruiter with 15+ years of experience writing high-performing job descriptions for companies across early-stage startups, growth-stage scaleups, and global enterprises.
Your job descriptions attract qualified candidates, filter unqualified applicants, and reflect the real character of the hiring company—not a generic template.
You write like a seasoned recruiter, not a content generator.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OBJECTIVE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Generate three structurally and tonally distinct job descriptions, each ready to publish without editing.
A sentence earns its place only if it does at least one of:
• Clarifies what the role requires or involves
• Helps the company attract better-fit applicants
• Helps a candidate disqualify themselves early
• Removes a common point of confusion

Remove anything that fails this test.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INPUT HIERARCHY (Source of Truth)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Information must come from these sources only, in priority order:

1. Org Facts          — highest authority
2. Tech Stack
3. Hire-specific Context
4. Company-grounded KB — lowest authority

Higher-priority inputs always override lower-priority ones.
If a piece of information is missing from all sources:
→ DO NOT GUESS. Omit it entirely.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HALLUCINATION PREVENTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Never invent or infer any of the following unless explicitly present in the input:

• Company size or headcount
• Salary ranges, equity, or bonuses
• Benefits or perks
• Remote / hybrid / on-site policy
• Visa sponsorship
• Interview format, duration, or number of rounds
• Technologies, tools, or platforms
• Responsibilities or deliverables
• Reporting or team structure
• Career progression or growth paths
• Company culture claims
• KPIs or performance metrics
• Hiring urgency or start date

COMPENSATION FALLBACK
If no compensation data is provided, write exactly:
"Competitive compensation"
Nothing more.

LOCATION FALLBACK
If no location is provided, omit the field entirely.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TECH STACK — STRICT INCLUSION RULE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Include only technologies explicitly listed in the Tech Stack input.
Do NOT add technologies because they are commonly paired, implied by the stack, or assumed by industry convention.
Example:
Input: Python, FastAPI

You may NOT add: Docker, AWS, PostgreSQL, Redis, Git, Linux, Celery, Nginx
Every technology you mention must appear word-for-word in the input.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LEGAL COMPLIANCE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

All three variants must comply with equal employment opportunity standards.

NEVER include language that discriminates—explicitly or implicitly—based on:

• Age (avoid: "recent graduate," "young and hungry," "digital native")
• Gender (use gender-neutral language throughout)
• Race, nationality, or ethnicity
• Disability or health status
• Family or marital status
• Religion

Avoid degree gatekeeping where possible. Prefer "equivalent experience" phrasing alongside formal qualifications.

Examples:
✗ "Bachelor's degree required"
✓ "Bachelor's degree or equivalent practical experience"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WRITING STYLE — NON-NEGOTIABLE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Write in plain, direct language. Every line should sound like a real person wrote it.
BANNED PHRASES — never use:

• passionate / passionate about
• rockstar / ninja / guru / wizard
• world-class
• fast-paced environment
• dynamic team
• self-starter
• wear many hats
• hit the ground running
• exciting opportunity
• join our amazing team
• thrive under pressure
• collaborative culture
• we're like a family
• make an impact
• move fast and break things
• results-oriented

STYLE RULES:

• Active voice by default
• Short paragraphs (2-4 lines max)
• Specific over general
• Concrete over abstract
• Believable over aspirational

BAD: "You'll work closely with cross-functional stakeholders to drive impactful outcomes."
GOOD: "You'll work with the product manager and data team to ship features on a bi-weekly cycle."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SENIORITY CALIBRATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Match every responsibility and requirement to the stated seniority level.

Intern / Entry-Level
→ Learning, mentorship, guided ownership, structured onboarding

Mid-Level
→ Owns discrete features, collaborates across teams, comfortable with moderate ambiguity, accountable for delivery

Senior
→ Leads technical decisions, designs systems, mentors junior engineers, drives architecture discussions

Staff / Principal
→ Sets long-range technical direction, influences multiple teams, establishes standards, owns multi-quarter roadmap items

Engineering Manager / Lead
→ Responsible for hiring, coaching, planning, delivery, cross-functional alignment, and team health

If a responsibility does not fit the seniority level, remove it.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INTERVIEW PROCESS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

If interview stages are provided, include a clearly labeled section in each applicable variant:

## Interview Process

Open with:
"The hiring process consists of [N] round(s):"

Then number each stage.

Example:
Round 1: HR Screening
Round 2: Technical Assessment
Round 3: Hiring Manager Discussion

DO NOT invent or assume:
• Duration of any round
• Whether rounds are virtual or in-person
• Panel composition
• Take-home assignment details
• Feedback timelines

If no interview data is provided, omit the section entirely.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PRE-WRITE VALIDATION CHECKLIST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Before writing any variant, run this check internally:

✓ Every responsibility is traceable to Hire-specific Context or Org Facts.
✓ Every requirement is traceable to a provided input.
✓ Every technology appears verbatim in Tech Stack.
✓ Every company claim appears in Org Facts or KB.
✓ No language violates legal compliance rules.
✓ Seniority level is reflected in every line.
✓ No banned phrases appear.
✓ No unsupported assumptions remain.

Remove anything that fails. Do not add a caveat—just remove it.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VARIANT REQUIREMENTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Generate exactly three variants. They must differ in:

• Structure (sections, order, formatting)
• Pacing (dense vs. spaced, short vs. long)
• Opening approach (narrative, summary, direct)
• Tone (conversational, formal, terse)

Variation must be substantive—not surface-level rewording.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VARIANT 1 — startup-casual
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Structure:
1. Opening narrative (1-2 short paragraphs): what the company does and why this role exists now.
2. What You'll Do — 6-8 specific bullets
3. What We're Looking For — split into:
   • Must Have
   • Nice to Have
4. Closing paragraph: human, specific to this role, explains what makes this opportunity worth considering.

Voice: first-person ("we," "our," "you'll")
Tone: direct, grounded, no fluff
Length: medium

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VARIANT 2 — enterprise-formal
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Sections (in order):
1. Position Summary
2. Key Responsibilities
3. Required Qualifications
4. Preferred Qualifications
5. Compensation
6. Interview Process (if data available)

Voice: third-person ("The candidate will…", "This role requires…")
Tone: formal, scope-focused, business-impact oriented
Emphasis: stakeholders, ownership, measurable outcomes
Length: longer; comprehensive

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VARIANT 3 — concise-pragmatic
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Hard limit: 350 words

Sections:
1. Role Summary (3-5 sentences max)
2. Must-Have Requirements (bullets only; no elaboration)
3. Compensation
4. Location
5. How to Apply

Voice: neutral, impersonal
Tone: internal memo style; no preamble
No opening narrative. No culture language. No closing pitch.
Reads like a brief written for a hiring manager, not a candidate.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FINAL QUALITY STANDARD
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Each variant must:

• Read as if written by a senior recruiter, not generated
• Pass ATS keyword parsing without keyword stuffing
• Contain zero duplicated information across sections
• Contain zero generic filler or marketing language
• Be legally compliant
• Be maximally specific to the provided inputs
• Be immediately publishable without editing

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Return ONLY valid JSON. No markdown wrapping. No preamble. No trailing explanation.

Schema:

{
  "variants": [
    {
      "tone": "startup-casual",
      "text": "<full JD in markdown>"
    },
    {
      "tone": "enterprise-formal",
      "text": "<full JD in markdown>"
    },
    {
      "tone": "concise-pragmatic",
      "text": "<full JD in markdown>"
    }
  ]
}

If any input is insufficient to produce a complete, compliant JD, return:

{
  "error": "<specific description of what is missing>"
}
Return nothing outside the JSON object.
""".strip()


def _facet_qualifier(
    department: str | None,
    stack: str | None,
    context_notes: str | None,
) -> str:
    """Build the per-hire suffix appended to every facet query.

    Without this, two "Senior Engineer" hires from different teams pull
    identical KB chunks. We append department + stack + a trimmed slice of the
    hire-specific notes to the search string so the retrieval differentiates.
    Slice is bounded because hybrid_search runs FTS on the query and very
    long queries dilute lexical signal.
    """
    parts: list[str] = []
    if department:
        parts.append(department)
    if stack:
        parts.append(stack[:200])
    if context_notes:
        parts.append(context_notes[:300])
    return (" " + " ".join(parts)) if parts else ""




async def generate_job_requisition(
    *,
    org_id: str,
    user_id: str,
    role_request: str,
    location: str,
    department: str,
    seniority_level: str,
    disclosed_compensation: str | None = None,
    interview_details: str | None = None,
    stack: str | None = None,
    working_hours: str | None = None,
    context_notes: str | None = None,
) -> dict[str, Any]:
    """Search → synthesize → persist. Returns the inserted row.

    `context_notes` carries per-hire specifics (team, HM, stack, problems)
    that disambiguate same-role hires. It is appended to every facet query
    AND surfaced to the LLM, so retrieval AND synthesis both differentiate.
    """
    qualifier = _facet_qualifier(department, stack, context_notes)
    org_facts = await _fetch_org_facts(org_id)
    facets = {
        "templates": f"JD template structure for {role_request}{qualifier}",
        "compensation": f"compensation band salary range {role_request}{qualifier}",
        "requirements": f"role requirements skills experience {role_request}{qualifier}",
        "team_structure": (
            f"team structure org chart reporting line for {role_request}{qualifier}"
        ),
    }
    facet_results = await search_facets_concurrent(
        org_id=org_id, facets=facets, k=6, char_budget_per_facet=3500
    )
    context_block = build_context_block(facet_results)
    sources = collect_sources(facet_results)
    # Empty context_block means every facet's hybrid_search returned no
    # usable chunks. We surface this as `grounded` so the UI can warn that
    # comp ranges / stack in the JD are model-generated, not KB-grounded.
    grounded = bool(context_block)

    org_facts_block = _build_org_facts_block(org_facts)

    # Compensation: disclose verbatim when provided, otherwise never invent a figure.
    if disclosed_compensation:
        comp_section = (
            f"\n## Compensation (include verbatim in every variant)\n"
            f"{disclosed_compensation}\n"
        )
    else:
        comp_section = (
            "\n## Compensation\n"
            "(not disclosed — write 'Competitive compensation' only; "
            "do NOT invent or imply a specific range)\n"
        )

    _tone_guide = (
        "1. startup-casual      — hook + first-person plural + bullet-heavy + culture close\n"
        "2. enterprise-formal   — Position Summary paragraph + numbered sections + third-person\n"
        "3. concise-pragmatic   — 250-350 words max, zero filler, role→requirements→comp→apply"
    )

    user_prompt = (
        f"## Role to hire\n{role_request}\n"
        + f"\n## Seniority level\n{seniority_level}\n"
        + f"\n## Location\n{location}\n"
        + f"\n## Department\n{department}\n"
        + comp_section
        + (
            f"\n## Tech stack (include ALL of these tools verbatim; add none)\n{stack}\n"
            if stack
            else ""
        )
        + (
            f"\n## Interview process (include verbatim in every variant under '## Interview process')\n{interview_details}\n"
            if interview_details
            else ""
        )
        + (
            f"\n## Working hours (include verbatim in every variant under '## Working hours')\n{working_hours}\n"
            if working_hours
            else ""
        )
        + (
            f"\n## Hire-specific context (highest authority — overrides KB chunks)\n{context_notes}\n"
            if context_notes
            else ""
        )
        # Org facts come from structured DB columns — authoritative ground truth.
        # Must appear before KB context so the LLM anchors on them first.
        + (
            f"\n## Org facts (use verbatim; never paraphrase or guess missing values)\n{org_facts_block}\n"
            if org_facts_block
            else ""
        )
        + f"\n## Tones to produce — three variants in this exact order\n{_tone_guide}\n"
        + (
            f"\n## Company-grounded context (KB retrieval — quote, don't invent)\n{context_block}\n"
            if grounded
            else (
                "\n## Company-grounded context\n"
                "No matching documents found in KB. Do NOT invent comp ranges, headcount, "
                "internal tools, or team details. Omit any fact you cannot source from the "
                "inputs above.\n"
            )
        )
        + "\n## Output\nReturn JSON only — exactly the schema in your system prompt. Three variants."
    )

    try:
        result = await synthesize_json(
            system_prompt=_GENERATE_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.7,
            timeout=120.0,
        )
    except Exception as exc:
        log.exception("recruiting.generate.synthesis_failed org=%s", org_id)
        raise RuntimeError(f"jd_generation_failed: {exc}") from exc

    variants_raw = (result or {}).get("variants") if isinstance(result, dict) else None
    if not variants_raw or not isinstance(variants_raw, list):
        raise RuntimeError("jd_generation_returned_no_variants")
    variants = [JdVariant(**v) for v in variants_raw if v.get("text")]

    if len(variants) < 2:
        # Hard floor; if the model couldn't produce at least 2 of the 3
        # variants we'd rather raise than persist a degraded result.
        raise RuntimeError(f"jd_generation_too_few_variants: got {len(variants)}")

    svc = get_service_client()

    def _insert() -> dict[str, Any]:
        res = (
            svc.table("job_requisitions")
            .insert(
                {
                    "org_id": org_id,
                    "created_by": user_id,
                    "role_request": role_request,
                    "seniority_level": seniority_level,
                    "disclosed_compensation": disclosed_compensation,
                    "interview_details": interview_details,
                    "stack": stack,
                    "working_hours": working_hours,
                    "context_notes": context_notes,
                    "location": location,
                    "department": department,
                    "jd_variants": [v.model_dump() for v in variants],
                    "grounded": grounded,
                    "status": "draft",
                }
            )
            .execute()
        )
        rows = res.data or []
        if not rows:
            raise RuntimeError("requisition_insert_returned_no_rows")
        return rows[0]

    row = await asyncio.to_thread(_insert)
    log.info(
        "recruiting.generate.ok org=%s req=%s variants=%d grounded=%s",
        org_id,
        row["id"],
        len(variants),
        grounded,
    )
    return {**row, "sources": sources, "grounded": grounded}


# ── 2. Publish ───────────────────────────────────────────────────────────────


# Every key here MUST appear in posting_registry as well — the registry's
# kind metadata (ats | job_board) decorates each posting result so the UI can
# render them differently.
_ATS_ADAPTERS = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
}


async def _publish_one_ats(
    *,
    org_id: str,
    requisition_id: str,
    user_id: str,
    ats_platform: AtsPlatform,
    title: str,
    content: str,
    location: str | None,
    department: str | None,
    mapping_override: dict[str, Any] | None,
    selected_variant_index: int,
) -> dict[str, Any]:
    """Publish to a single ATS. Returns one ATS posting dict.

    On success: {"platform", "job_id", "url"}.
    On failure: {"platform", "error"}. Never raises — callers aggregate.
    """
    try:
        resolved = await mapping_resolver.resolve_mapping(
            org_id=org_id,
            ats_platform=ats_platform,
            location_text=location,
            department_text=department,
        )
        resolved_dict = resolved.to_dict()
        ats_metadata: dict[str, Any] = resolved.to_ats_metadata()
    except Exception as exc:
        log.warning(
            "recruiting.mapping.resolve_failed ats=%s err=%s", ats_platform, exc
        )
        resolved_dict = {}
        ats_metadata = {}

    if mapping_override:
        ats_metadata = {**ats_metadata, **mapping_override}

    await audit_log.write(
        org_id=org_id,
        requisition_id=requisition_id,
        actor_user_id=user_id,
        action="publish_attempt",
        status="success",
        ats_platform=ats_platform,
        request_summary={
            "variant_index": selected_variant_index,
            "location": location,
            "department": department,
            "mapping_confidence_location": resolved_dict.get("location", {}).get("confidence"),
            "mapping_confidence_department": resolved_dict.get("department", {}).get("confidence"),
        },
    )

    adapter = _ATS_ADAPTERS[ats_platform]
    # Tag every posting with the registry's destination_type so the UI can
    # render "ATS" vs "Job board" failures differently. Looked up once here
    # rather than re-derived on every read of ats_postings.
    destination_type = posting_registry.kind_of(ats_platform)
    try:
        async with audit_log.timed(
            org_id=org_id,
            requisition_id=requisition_id,
            actor_user_id=user_id,
            action="ats_publish",
            ats_platform=ats_platform,
            request_summary={"title": title, "metadata_keys": list(ats_metadata.keys())},
        ) as ctx:
            ats_result = await adapter.publish_job(
                org_id=org_id,
                title=title,
                content=content,
                location=location,
                department=department,
                metadata=ats_metadata or None,
            )
            ctx.status_code = 200
            ctx.response_summary = {
                "job_id": ats_result.get("job_id"),
                "url": ats_result.get("url"),
            }
        return {
            "platform": ats_platform,
            "destination_type": destination_type,
            "job_id": ats_result["job_id"],
            "url": ats_result["url"],
        }
    except PermissionError as exc:
        msg = f"ats_not_connected_or_unauthorized: {exc}"
        log.warning("recruiting.publish.permission ats=%s err=%s", ats_platform, exc)
        return {
            "platform": ats_platform,
            "destination_type": destination_type,
            "error": msg,
        }
    except Exception as exc:
        msg = f"ats_publish_failed: {exc}"
        log.warning("recruiting.publish.failed ats=%s err=%s", ats_platform, exc)
        return {
            "platform": ats_platform,
            "destination_type": destination_type,
            "error": msg,
        }


async def publish_requisition(
    *,
    org_id: str,
    requisition_id: str,
    user_id: str,
    selected_variant_index: int,
    ats_platforms: list[AtsPlatform],
    hiring_manager_email: str | None,
    slack_channel: str | None,
    notion_parent_page_id: str | None,
    location_override: str | None = None,
    department_override: str | None = None,
    mapping_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Publish the requisition to every selected ATS, then run best-effort
    side-effects in parallel.

    Per-platform results are aggregated into `ats_postings`. The requisition
    is marked `published` if *any* platform succeeded; `failed` only when
    every platform failed. Notion / sourcing / Slack / email are best-effort:
    a Slack 404 doesn't undo successful ATS postings.

    Mapping overrides are scoped per platform — the UI shows a mapping
    preview per ATS, and each preview produces its own override dict.
    """
    svc = get_service_client()

    def _fetch() -> dict[str, Any] | None:
        res = (
            svc.table("job_requisitions")
            .select("*")
            .eq("id", requisition_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    row = await asyncio.to_thread(_fetch)
    if not row:
        raise LookupError("requisition_not_found")
    if (row.get("status") or "").lower() == "published":
        raise RuntimeError("requisition_already_published")

    # If the publish request didn't supply a Notion parent, fall back to the
    # org-level default the user configured during recruiting onboarding.
    # This keeps the publish form one-click for the steady-state case while
    # leaving an escape hatch for the per-requisition override.
    if not notion_parent_page_id:
        notion_parent_page_id = await _org_default_notion_parent(org_id)
    if not slack_channel:
        slack_channel = await _org_default_slack_channel(org_id)

    variants = row.get("jd_variants") or []
    if selected_variant_index >= len(variants):
        raise ValueError("selected_variant_index_out_of_range")
    variant = variants[selected_variant_index]
    title = _extract_title(row.get("role_request") or "", variant.get("text") or "")
    content = variant.get("text") or ""

    location = location_override or row.get("location")
    department = department_override or row.get("department")

    # Dedupe while preserving order — UI may pass duplicates if a checkbox
    # was clicked twice in the same render.
    seen: set[str] = set()
    platforms: list[AtsPlatform] = []
    for p in ats_platforms:
        if p not in seen:
            seen.add(p)
            platforms.append(p)
    if not platforms:
        raise ValueError("no_ats_platforms")

    overrides = dict(mapping_overrides or {})

    # Fan out across platforms in parallel — each ATS is an independent
    # network call. One failing doesn't abort the others.
    ats_tasks = [
        asyncio.create_task(
            _publish_one_ats(
                org_id=org_id,
                requisition_id=requisition_id,
                user_id=user_id,
                ats_platform=p,
                title=title,
                content=content,
                location=location,
                department=department,
                mapping_override=overrides.get(p),
                selected_variant_index=selected_variant_index,
            )
        )
        for p in platforms
    ]
    postings = await asyncio.gather(*ats_tasks)

    successes = [p for p in postings if p.get("url")]
    failures = [p for p in postings if p.get("error")]

    if not successes:
        # Every ATS failed — surface the first error in the row's
        # error_message so the UI shows something meaningful.
        joined = "; ".join(
            f"{p['platform']}: {p.get('error')}" for p in failures
        )
        await _mark_failed_with_postings(
            requisition_id, org_id, joined or "all_ats_failed", postings
        )
        # Re-raise as RuntimeError so the router returns 502 like before.
        raise RuntimeError(joined or "all_ats_failed")

    # Primary URL/job_id (back-compat with single-ATS columns and downstream
    # notifications that take a single link) = first successful posting in
    # the order the user selected.
    primary = successes[0]
    primary_url: str = primary["url"]
    extra_success_urls = [s["url"] for s in successes[1:]]

    sourcing_task = asyncio.create_task(
        _draft_sourcing(
            org_id=org_id,
            role_request=row["role_request"],
            jd=content,
            seniority_level=row.get("seniority_level"),
            location=row.get("location"),
        )
    )
    notion_task = asyncio.create_task(
        _create_notion_tracker_audited(
            org_id=org_id,
            requisition_id=requisition_id,
            user_id=user_id,
            title=title,
            jd_content=content,
            ats_postings=successes,
            parent_page_id=notion_parent_page_id,
        )
    )
    slack_task = asyncio.create_task(
        _notify_slack_audited(
            org_id=org_id,
            requisition_id=requisition_id,
            user_id=user_id,
            channel=slack_channel,
            title=title,
            ats_postings=successes,
            hiring_manager_email=hiring_manager_email,
        )
    )
    email_task = asyncio.create_task(
        _notify_hiring_manager_audited(
            org_id=org_id,
            requisition_id=requisition_id,
            user_id=user_id,
            recipient=hiring_manager_email,
            title=title,
            jd_excerpt=content[:1200],
            ats_url=primary_url,
            extra_urls=extra_success_urls,
        )
    )

    sourcing_drafts, linkedin_urls = await sourcing_task
    notion_result = await notion_task
    notion_url = notion_result.get("tracker_url")
    notion_candidates_db_id = notion_result.get("candidates_db_id")
    slack_post_error = await slack_task
    await email_task

    # If some platforms failed but at least one succeeded, surface that as
    # a non-fatal error message — the UI shows it alongside the ats_postings
    # list so the recruiter can re-publish the failed ones from the ATS.
    error_message = (
        "; ".join(f"{p['platform']}: {p.get('error')}" for p in failures)
        if failures
        else None
    )

    def _update() -> dict[str, Any]:
        update_payload: dict[str, Any] = {
            "selected_variant_index": selected_variant_index,
            "ats_platform": primary["platform"],
            "ats_job_id": primary["job_id"],
            "ats_url": primary_url,
            "ats_postings": postings,
            "notion_tracker_url": notion_url,
            "notion_candidates_db_id": notion_candidates_db_id,
            "sourcing_templates": [t.model_dump() for t in sourcing_drafts],
            "linkedin_search_urls": [s.model_dump() for s in linkedin_urls],
            "hiring_manager_email": hiring_manager_email,
            "slack_channel": slack_channel,
            "slack_post_error": slack_post_error,
            "status": "published",
            "error_message": error_message,
            "published_at": datetime.now(UTC).isoformat(),
        }
        res = (
            svc.table("job_requisitions")
            .update(update_payload)
            .eq("id", requisition_id)
            .eq("org_id", org_id)
            .execute()
        )
        return (res.data or [{}])[0]

    updated = await asyncio.to_thread(_update)
    log.info(
        "recruiting.publish.ok req=%s platforms=%s ok=%d fail=%d notion=%s",
        requisition_id,
        ",".join(platforms),
        len(successes),
        len(failures),
        bool(notion_url),
    )
    return updated


def _extract_title(role_request: str, jd_text: str) -> str:
    """Recruiter-supplied role_request (first line) is the source of truth.

    The JD body's heading structure isn't stable — the generator may emit
    `## Interview process` or `## Working hours` as the first heading with
    no H1 at all, which previously hijacked the title. role_request is what
    the recruiter actually typed as the role (e.g. "Senior Product
    Designer"), so use it directly. Fall back to the JD's first H1 / H2
    only when role_request is empty.
    """
    first_line = (role_request or "").strip().splitlines()[0:1]
    if first_line and first_line[0].strip():
        return first_line[0].strip()[:120]
    first_h2: str | None = None
    for line in (jd_text or "").splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()[:120]
        if first_h2 is None and line.startswith("## "):
            first_h2 = line[3:].strip()[:120]
    if first_h2:
        return first_h2
    return (role_request or "")[:120]


async def _mark_failed(requisition_id: str, org_id: str, msg: str) -> None:
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("job_requisitions")
        .update({"status": "failed", "error_message": msg})
        .eq("id", requisition_id)
        .eq("org_id", org_id)
        .execute()
    )


async def _mark_failed_with_postings(
    requisition_id: str,
    org_id: str,
    msg: str,
    postings: list[dict[str, Any]],
) -> None:
    """Like _mark_failed but also persists the per-platform error details so
    the UI can render which ATS broke and why."""
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("job_requisitions")
        .update(
            {
                "status": "failed",
                "error_message": msg,
                "ats_postings": postings,
            }
        )
        .eq("id", requisition_id)
        .eq("org_id", org_id)
        .execute()
    )


# ── Side-effects ─────────────────────────────────────────────────────────────


_SOURCING_SYSTEM = """You are a sourcing recruiter. Draft 3 distinct LinkedIn outreach messages tailored to the JD below. Each message must be ≤900 characters (LinkedIn InMail cap), open with a specific hook tied to the role, and avoid generic phrasings.

Output JSON only:
{
  "templates": [
    {"channel": "linkedin", "subject": "<short>", "body": "<message>", "notes": "<who this is best for>"}
  ]
}
""".strip()


async def _draft_sourcing(
    *,
    org_id: str,
    role_request: str,
    jd: str,
    seniority_level: str | None = None,
    location: str | None = None,
) -> tuple[list[SourcingTemplate], list[LinkedinSearch]]:
    """Returns (templates, linkedin_searches). Best-effort: failures
    produce empty lists rather than blowing up the publish."""
    templates: list[SourcingTemplate] = []
    try:
        prompt = f"## Role\n{role_request}\n\n## Selected JD\n{jd[:4000]}"
        result = await synthesize_json(
            system_prompt=_SOURCING_SYSTEM,
            user_prompt=prompt,
            temperature=0.6,
            timeout=60.0,
        )
        raw_templates = (result or {}).get("templates") if isinstance(result, dict) else None
        if isinstance(raw_templates, list):
            templates = [SourcingTemplate(**t) for t in raw_templates if t.get("body")]
    except Exception as exc:
        log.warning("recruiting.sourcing.failed org=%s err=%s", org_id, exc)

    # LinkedIn deep links — no LinkedIn API call. We hand the recruiter a
    # small menu of pre-filtered searches keyed off the requisition's role,
    # seniority, and location so they don't have to rebuild the same query
    # in LinkedIn's UI five times.
    linkedin_urls = _linkedin_search_urls_for(
        role_request, seniority_level=seniority_level, location=location
    )

    return templates, linkedin_urls


# Seniority labels that drift across companies (Lead/Staff/Principal are
# title-level, so we drop "Senior" rather than stacking it).
_TITLE_LEVEL_SENIORITIES = {"staff", "lead", "principal"}


def _linkedin_search_urls_for(
    role_request: str,
    *,
    seniority_level: str | None = None,
    location: str | None = None,
) -> list[LinkedinSearch]:
    """Build a curated list of LinkedIn People-search deep links.

    LinkedIn's /search/results/people/ endpoint accepts:
      * `keywords`     — full-text match across profile
      * `titleFreeText` — match on current title only (fewer false positives)
      * `network`      — JSON-encoded array of degree codes: F (1st), S (2nd)
      * `openToWork`   — surfaces #OpenToWork badge holders

    We return them in the order a recruiter would normally try them:
    closest signal first (warm intros), broadest reach last.
    """
    base = "https://www.linkedin.com/search/results/people/"
    role = role_request.strip()
    role_lower = role.lower()

    # Pick the seniority prefix that makes sense. If the title already
    # contains the level (e.g. "Senior Frontend Engineer") or the level is
    # title-level (Staff/Lead/Principal), we just use the role as-is.
    level = (seniority_level or "").strip().lower()
    if level and level not in role_lower and level not in _TITLE_LEVEL_SENIORITIES:
        senior_role = f"{level.capitalize()} {role}"
    elif "senior" not in role_lower and level in {"", "senior"}:
        senior_role = f"Senior {role}"
    else:
        senior_role = role

    def _qs(params: dict[str, str]) -> str:
        return "&".join(f"{k}={quote_plus(v)}" for k, v in params.items() if v)

    searches: list[LinkedinSearch] = []

    # 1. Title-only, 1st-degree — warm intros, highest reply rate.
    searches.append(
        LinkedinSearch(
            label=f"{role} — warm intros",
            url=base + "?" + _qs({"titleFreeText": role, "network": '["F"]'}),
            description="Title-only search across your 1st-degree connections. "
            "Best for asking for intros before cold outreach.",
        )
    )

    # 2. Title-only, 2nd-degree — best balance of reach + relevance.
    searches.append(
        LinkedinSearch(
            label=f"{role} — 2nd-degree network",
            url=base + "?" + _qs({"titleFreeText": role, "network": '["S"]'}),
            description="Title-only search across 2nd-degree connections. "
            "Largest pool of candidates you can still reference-check.",
        )
    )

    # 3. Seniority-disambiguated keyword search (broader recall).
    if senior_role != role:
        searches.append(
            LinkedinSearch(
                label=senior_role,
                url=f"{base}?{_qs({'keywords': senior_role})}",
                description=(
                    "Full-profile keyword search disambiguated by seniority. "
                    "Catches candidates whose current title is adjacent "
                    f"(e.g. Tech Lead, Principal) but who match {senior_role!r} elsewhere on their profile."
                ),
            )
        )

    # 4. Open-to-work pool — active job seekers.
    searches.append(
        LinkedinSearch(
            label=f"{role} — open to work",
            url=f"{base}?{_qs({'titleFreeText': role, 'openToWork': 'true'})}",
            description="Filtered to profiles flagged #OpenToWork. Smaller pool, "
            "but highest response rate on cold outreach.",
        )
    )

    # 5. Location-scoped search (only when we have one and it isn't 'Remote').
    loc = (location or "").strip()
    if loc and loc.lower() not in {"remote", "anywhere"}:
        searches.append(
            LinkedinSearch(
                label=f"{role} in {loc}",
                url=f"{base}?{_qs({'keywords': role, 'origin': 'FACETED_SEARCH'})}#location={quote_plus(loc)}",
                description=(
                    f"Keyword search you can immediately narrow to {loc!r} via "
                    "LinkedIn's left-rail Location filter. (LinkedIn requires a "
                    "geoUrn for true pre-filtering — they don't expose those publicly.)"
                ),
            )
        )

    return searches


async def _org_default_slack_channel(org_id: str) -> str | None:
    """Look up the org's configured default Slack channel for recruiting.

    Returns None when Slack isn't connected or no default has been set —
    the publish flow then skips the Slack notification entirely.
    """
    svc = get_service_client()
    try:
        res = await asyncio.to_thread(
            lambda: svc.table("slack_integrations")
            .select("default_recruiting_slack_channel_id")
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        log.warning("recruiting.slack_default_lookup_failed org=%s err=%s", org_id, exc)
        return None
    if not res or not res.data:
        return None
    return res.data.get("default_recruiting_slack_channel_id")


async def _org_default_notion_parent(org_id: str) -> str | None:
    """Look up the org's configured default tracker parent page.

    Returns None when (a) Notion isn't connected or (b) the user hasn't
    configured a default yet. The publish flow then treats Notion as
    skipped instead of failing.
    """
    svc = get_service_client()
    try:
        res = await asyncio.to_thread(
            lambda: svc.table("notion_integrations")
            .select("default_recruiting_tracker_parent_id")
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        log.warning("recruiting.notion_default_lookup_failed org=%s err=%s", org_id, exc)
        return None
    if not res or not res.data:
        return None
    return res.data.get("default_recruiting_tracker_parent_id")


async def _create_notion_tracker(
    *,
    org_id: str,
    title: str,
    jd_content: str,
    ats_postings: list[dict[str, Any]],
    parent_page_id: str | None,
) -> dict[str, str | None] | None:
    """Create a hiring tracker page + candidate database in Notion.

    Returns a dict {tracker_url, candidates_db_id} on success or None when no
    parent_page_id was supplied. The candidate database is created as a
    child of the tracker page so it lives alongside the JD instead of
    polluting the recruiter's workspace root. The DB write is best-effort:
    if it fails we still return the tracker_url so the publish flow
    succeeds — the recruiter can manually create a DB later.

    Raises PermissionError when the bot isn't shared on the parent or the
    org has no Notion token; RuntimeError on other API failures. Caller
    (`_create_notion_tracker_audited`) wraps both so they end up in the
    audit log rather than killing the publish.
    """
    if not parent_page_id:
        return None
    from app.services.integrations import notion as notion_svc

    if ats_postings:
        ats_links_block = "## ATS links\n" + "\n".join(
            f"- **{p['platform'].title()}:** {p['url']}" for p in ats_postings
        )
    else:
        ats_links_block = "## ATS links\n_(none — publish failed on every platform)_"

    tracker_content = (
        f"# Hiring Tracker — {title}\n\n"
        f"{ats_links_block}\n\n"
        f"## Pipeline\n- [ ] Sourcing\n- [ ] Outreach sent\n- [ ] Phone screens\n"
        f"- [ ] Onsites\n- [ ] Offer\n- [ ] Closed\n\n"
        f"## Job description\n\n{jd_content}\n"
    )
    page_title = f"Hiring-{title}-{datetime.now(UTC).strftime('%Y-%m-%d')}"
    result = await notion_svc.create_page(
        org_id=org_id,
        parent_page_id=parent_page_id,
        title=page_title,
        content=tracker_content,
        is_markdown=True,
    )
    tracker_url = result.get("url")
    tracker_page_id = result.get("page_id")

    candidates_db_id: str | None = None
    if tracker_page_id:
        # Best-effort: a DB creation failure shouldn't undo the tracker page.
        try:
            db_result = await notion_svc.create_candidate_database(
                org_id=org_id,
                parent_page_id=tracker_page_id,
                title=title,
            )
            candidates_db_id = db_result.get("database_id")
        except Exception as exc:
            log.warning(
                "recruiting.notion.candidate_db_create_failed org=%s err=%s",
                org_id,
                exc,
            )

    return {
        "tracker_url": tracker_url,
        "candidates_db_id": candidates_db_id,
    }


async def _notify_slack(
    *,
    org_id: str,
    channel: str | None,
    title: str,
    ats_postings: list[dict[str, Any]],
    hiring_manager_email: str | None,
) -> str | None:
    """Post one Slack message. No-ops when channel is empty.

    Returns:
        None when the post succeeded or there was no channel to post to.
        The error string (e.g. "slack_not_in_channel") when the post failed —
        callers persist this on the requisition so the UI can surface it.
    """
    if not channel:
        return None
    from app.services.integrations import slack as slack_svc

    if len(ats_postings) == 1:
        msg = f"📢 New job opening: *{title}* — {ats_postings[0]['url']}"
    else:
        links = "\n".join(
            f"• {p['platform'].title()}: {p['url']}" for p in ats_postings
        )
        msg = f"📢 New job opening: *{title}*\n{links}"
    if hiring_manager_email:
        msg += f"\nHiring manager: {hiring_manager_email}"
    try:
        await slack_svc.post_message(org_id=org_id, channel_id=channel, text=msg)
        return None
    except Exception as exc:
        log.warning("recruiting.slack.failed org=%s ch=%s err=%s", org_id, channel, exc)
        return str(exc) or type(exc).__name__


# ── Audited variants of the side-effects ────────────────────────────────────


async def _create_notion_tracker_audited(
    *,
    org_id: str,
    requisition_id: str,
    user_id: str,
    title: str,
    jd_content: str,
    ats_postings: list[dict[str, Any]],
    parent_page_id: str | None,
) -> dict[str, str | None]:
    """Wraps _create_notion_tracker with an audit row. Returns
    {tracker_url, candidates_db_id} — either may be None on partial failure.
    """
    empty: dict[str, str | None] = {"tracker_url": None, "candidates_db_id": None}
    if not parent_page_id:
        await audit_log.write(
            org_id=org_id,
            requisition_id=requisition_id,
            actor_user_id=user_id,
            action="notion_create",
            status="skipped",
            request_summary={"reason": "no_parent_page_id"},
        )
        return empty
    try:
        async with audit_log.timed(
            org_id=org_id,
            requisition_id=requisition_id,
            actor_user_id=user_id,
            action="notion_create",
            request_summary={
                "parent_page_id": parent_page_id[:32] + "…",
                "ats_platforms": [p["platform"] for p in ats_postings],
            },
        ) as ctx:
            result = await _create_notion_tracker(
                org_id=org_id,
                title=title,
                jd_content=jd_content,
                ats_postings=ats_postings,
                parent_page_id=parent_page_id,
            )
            if not result or not result.get("tracker_url"):
                # Notion returned 200 but no URL in the response — treat as a
                # failure so the audit row reflects that no tracker exists.
                raise RuntimeError("notion_returned_no_url")
            ctx.response_summary = {
                "url": result.get("tracker_url"),
                "candidates_db_id": result.get("candidates_db_id"),
            }
            return result
    except PermissionError as exc:
        log.warning("recruiting.notion.permission org=%s err=%s", org_id, exc)
        return empty
    except Exception as exc:
        log.warning("recruiting.notion.failed org=%s err=%s", org_id, exc)
        return empty


async def _notify_slack_audited(
    *,
    org_id: str,
    requisition_id: str,
    user_id: str,
    channel: str | None,
    title: str,
    ats_postings: list[dict[str, Any]],
    hiring_manager_email: str | None,
) -> str | None:
    """Returns the Slack error string when the post failed, None otherwise.

    Mirrors _notify_slack's contract so publish_requisition can persist the
    error on the row without inspecting the audit log.
    """
    if not channel:
        await audit_log.write(
            org_id=org_id,
            requisition_id=requisition_id,
            actor_user_id=user_id,
            action="slack_notify",
            status="skipped",
            request_summary={"reason": "no_channel"},
        )
        return None
    error_msg: str | None = None
    try:
        async with audit_log.timed(
            org_id=org_id,
            requisition_id=requisition_id,
            actor_user_id=user_id,
            action="slack_notify",
            request_summary={
                "channel": channel,
                "ats_platforms": [p["platform"] for p in ats_postings],
            },
        ) as ctx:
            error_msg = await _notify_slack(
                org_id=org_id,
                channel=channel,
                title=title,
                ats_postings=ats_postings,
                hiring_manager_email=hiring_manager_email,
            )
            if error_msg:
                # Surface the Slack-side error to the audit row as a failure
                # without raising — publish flow must not abort here.
                ctx.response_summary = {"posted": False, "error": error_msg}
                raise _SlackPostFailed(error_msg)
            ctx.response_summary = {"posted": True}
    except _SlackPostFailed:
        # Already captured in error_msg; audit row marked failure by timed().
        pass
    except Exception as exc:
        # Unexpected non-Slack failure (e.g. audit log write blew up). Treat
        # the post as undelivered so the UI can show *something* meaningful.
        error_msg = error_msg or f"{type(exc).__name__}: {exc}"
    return error_msg


class _SlackPostFailed(RuntimeError):
    """Internal sentinel so audit_log.timed records status='failure' for a
    Slack-side error returned by _notify_slack, without bubbling the failure
    out of the publish flow."""


async def _notify_hiring_manager_audited(
    *,
    org_id: str,
    requisition_id: str,
    user_id: str,
    recipient: str | None,
    title: str,
    jd_excerpt: str,
    ats_url: str,
    extra_urls: list[str] | None = None,
) -> None:
    """Fire-and-forget React Email to the hiring manager. Idempotency uses
    the requisition_id as dedupe_key so a republish doesn't double-send.

    When more than one ATS posting exists, the primary `ats_url` is used for
    the "Open in ATS" CTA and the rest are appended to the JD excerpt so the
    hiring manager still sees every link without us having to change the
    email template's typed props.
    """
    if not recipient:
        await audit_log.write(
            org_id=org_id,
            requisition_id=requisition_id,
            actor_user_id=user_id,
            action="hiring_manager_email",
            status="skipped",
            request_summary={"reason": "no_recipient"},
        )
        return
    try:
        from app.config import get_settings
        from app.services.email import send_email_event

        settings = get_settings()
        excerpt = jd_excerpt
        if extra_urls:
            extras = "\n".join(f"• {u}" for u in extra_urls)
            excerpt = f"Also live at:\n{extras}\n\n{jd_excerpt}"

        async with audit_log.timed(
            org_id=org_id,
            requisition_id=requisition_id,
            actor_user_id=user_id,
            action="hiring_manager_email",
            request_summary={"recipient": recipient, "extra_links": len(extra_urls or [])},
        ) as ctx:
            await send_email_event(
                event_type="recruiting_published",
                to=recipient,
                user_id=None,
                org_id=org_id,
                dedupe_key=requisition_id,
                data={
                    "role_title": title,
                    "jd_excerpt": excerpt,
                    "ats_url": ats_url,
                    "app_url": settings.app_url,
                    "requisition_id": requisition_id,
                },
            )
            ctx.response_summary = {"enqueued": True}
    except Exception:
        return
