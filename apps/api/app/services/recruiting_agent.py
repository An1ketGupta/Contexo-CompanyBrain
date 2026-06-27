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
    SourcingTemplate,
)
from app.services.agents.kb_synthesis import (
    build_context_block,
    collect_sources,
    search_facets_concurrent,
    synthesize_json,
    synthesize_text,
)
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


_GENERATE_SYSTEM = """You are a principal technical recruiter who has written thousands of job descriptions at high-bar companies. Your JDs are known for three things: brutal specificity, zero filler, and attracting exactly the right person while naturally filtering out the wrong one.

━━━ GROUND RULES ━━━

NEVER invent facts. Every claim must come from one of: "Role to hire", "Tech stack", "Org facts", "Hire-specific context", or "Company-grounded context". The hierarchy of authority is:
  1. Org facts (authoritative DB columns — use verbatim, never paraphrase)
  2. Tech stack (list provided by the recruiter — include all tools listed, add none)
  3. Hire-specific context (per-hire specifics — overrides conflicting KB chunks)
  4. Company-grounded context (KB retrieval — quotes are preferred over paraphrase)

Specific prohibitions:
- If headcount is not in Org facts → never write a company size range
- If comp is not disclosed → write "Competitive compensation" exactly; never invent or hint at a range
- If a tool is not in "Tech stack" → do not add it
- No buzzwords: passionate, rockstar, ninja, guru, world-class, dynamic team, fast-paced, self-starter

━━━ SENIORITY SIGNALS ━━━

Match every JD to the seniority level provided. Use these signals:

- intern / entry:    "you will learn", guided work, mentorship mentioned, explicit ramp-up period
- mid:               "you will own", some ambiguity expected, cross-team collaboration, 2-4 yr experience signal
- senior:            "you will lead the design/decision", broad scope, mentors others implicitly, 5+ yr signal
- staff / principal: "you will set direction", org-wide impact, works across multiple teams, define standards
- lead / manager:    "you will grow the team", hiring/performance ownership, strategy + execution balance, P&L or headcount ownership if relevant

━━━ INTERVIEW PROCESS ━━━

When an interview process is provided, include it under a "## Interview process" section in every variant.

Always format the process with framing prose — never paste the raw input as-is:
- Always open with a short lead-in: "The process consists of N round(s):"
- Always number each round: "Round 1: …", "Round 2: …"
- Light connective sentences are fine but must not add new facts

You MUST NOT invent any of the following — only include them if they appear in the input:
- Durations, time slots, or scheduling
- Panel members, interviewer names, roles, or titles
- Formats (in-person / virtual / async / live)
- Follow-up steps or post-interview communication
- Take-home assignments, prep materials, or any artefacts not mentioned

If the input is a single phrase like "HR Screening", render it as a one-round process ("The process consists of 1 round:" → "Round 1: HR Screening"). If the input lists multiple stages (e.g. "HR → Tech Interview → Final HR call"), split on "→" or "," or numbered markers, then number each stage in order.

━━━ THREE VARIANTS — STRUCTURAL SPECS ━━━

Each variant must differ in STRUCTURE and OPENING, not just vocabulary. Produce exactly three, in this order:

1. startup-casual
   - Open with 1-2 sentence "who we are / what we're building" hook (use Org facts if present, otherwise derive from role)
   - Use first person plural ("we're looking for", "you'll own")
   - Bullet-heavy responsibilities (6-8 tight bullets)
   - Requirements split: Must-have (3-5) + Nice-to-have (2-3)
   - Close with a human note on culture or why this role exists now

2. enterprise-formal
   - Open with "Position Summary" paragraph in third person ("The [Title] will…")
   - Numbered or headed sections: Summary → Key Responsibilities → Qualifications → Preferred → Compensation → Process
   - Passive/formal register throughout
   - Emphasise scope, stakeholders, and measurable impact

3. concise-pragmatic
   - 250-350 words maximum — no section you can cut without losing signal
   - Format: role in one line → 4-5 must-have requirements → comp/location → how to apply
   - Zero filler sentences
   - Reads like a well-written internal headcount request, not marketing copy

━━━ OUTPUT ━━━

JSON ONLY — no prose, no markdown fences around the JSON block, no trailing explanation.
{
  "variants": [
    {"tone": "<tone name>", "text": "<full JD in markdown, 300-600 words except concise-pragmatic which is 250-350>"}
  ]
}
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


_ATS_ADAPTERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}


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
            "job_id": ats_result["job_id"],
            "url": ats_result["url"],
        }
    except PermissionError as exc:
        msg = f"ats_not_connected_or_unauthorized: {exc}"
        log.warning("recruiting.publish.permission ats=%s err=%s", ats_platform, exc)
        return {"platform": ats_platform, "error": msg}
    except Exception as exc:
        msg = f"ats_publish_failed: {exc}"
        log.warning("recruiting.publish.failed ats=%s err=%s", ats_platform, exc)
        return {"platform": ats_platform, "error": msg}


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
    if row.get("status") == "published":
        raise RuntimeError("requisition_already_published")

    # If the publish request didn't supply a Notion parent, fall back to the
    # org-level default the user configured during recruiting onboarding.
    # This keeps the publish form one-click for the steady-state case while
    # leaving an escape hatch for the per-requisition override.
    if not notion_parent_page_id:
        notion_parent_page_id = await _org_default_notion_parent(org_id)

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

    overrides = mapping_overrides or {}

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
        _draft_sourcing(org_id=org_id, role_request=row["role_request"], jd=content)
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
    notion_url = await notion_task
    await slack_task
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
        res = (
            svc.table("job_requisitions")
            .update(
                {
                    "selected_variant_index": selected_variant_index,
                    "ats_platform": primary["platform"],
                    "ats_job_id": primary["job_id"],
                    "ats_url": primary_url,
                    "ats_postings": postings,
                    "notion_tracker_url": notion_url,
                    "sourcing_templates": [t.model_dump() for t in sourcing_drafts],
                    "linkedin_search_urls": linkedin_urls,
                    "hiring_manager_email": hiring_manager_email,
                    "slack_channel": slack_channel,
                    "status": "published",
                    "error_message": error_message,
                    "published_at": datetime.now(UTC).isoformat(),
                }
            )
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
    """First H1/H2 line of the JD if present, else fall back to role_request.

    Empirically Gemini opens every JD with `# Senior Product Designer` or
    similar. If the model deviated we use the user's original input — better
    a generic title than the model's first paragraph as the job name.
    """
    for line in (jd_text or "").splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()[:200]
        if line.startswith("## "):
            return line[3:].strip()[:200]
    return role_request[:200]


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
) -> tuple[list[SourcingTemplate], list[str]]:
    """Returns (templates, linkedin_search_urls). Best-effort: failures
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

    # LinkedIn Recruiter search URL templates — no API call, just a deep link
    # the recruiter can click. We synthesize 2-3 query strings from role
    # keywords.
    linkedin_urls = _linkedin_search_urls_for(role_request)

    return templates, linkedin_urls


def _linkedin_search_urls_for(role_request: str) -> list[str]:
    """Build clickable LinkedIn search URLs. No LinkedIn API call.

    LinkedIn Sales Navigator / Recruiter search uses /search/results/people/
    with `keywords` query. We construct 3 variants:
      1. exact role
      2. role with seniority disambiguated ("senior", "staff")
      3. role with adjacent title family
    """
    base = "https://www.linkedin.com/search/results/people/"
    keywords = role_request.strip()
    urls = [f"{base}?keywords={quote_plus(keywords)}"]
    if "senior" not in keywords.lower():
        urls.append(f"{base}?keywords={quote_plus('senior ' + keywords)}")
    urls.append(
        f"{base}?keywords={quote_plus(keywords)}&network=%5B%22S%22%5D"  # 2nd-degree
    )
    return urls


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
) -> str | None:
    """Create a hiring tracker page in Notion. Returns the URL on success.

    Raises whatever the Notion client raises (PermissionError when the bot
    isn't shared on the parent page or the org has no Notion token,
    arbitrary RuntimeError/HTTP errors otherwise). The caller is expected
    to handle these — typically `_create_notion_tracker_audited` so the
    failure shows up in the audit log instead of getting swallowed here.

    `ats_postings` is the list of successful postings; each platform gets
    its own bullet at the top of the tracker page so the hiring team can
    jump to the right ATS without guessing which one was used.
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
        f"## Candidates\n_(add candidates with status here)_\n\n"
        f"## Notes\n\n## Job description\n\n{jd_content}\n"
    )
    result = await notion_svc.create_page(
        org_id=org_id,
        parent_page_id=parent_page_id,
        title=f"Hiring — {title}",
        content=tracker_content,
        is_markdown=True,
    )
    return result.get("url")


async def _notify_slack(
    *,
    org_id: str,
    channel: str | None,
    title: str,
    ats_postings: list[dict[str, Any]],
    hiring_manager_email: str | None,
) -> None:
    """Post one Slack message. No-ops when channel is empty."""
    if not channel:
        return
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
    except Exception as exc:
        log.warning("recruiting.slack.failed org=%s ch=%s err=%s", org_id, channel, exc)


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
) -> str | None:
    """Wraps _create_notion_tracker with an audit row. Skipped status when no
    parent_page_id is provided (recruiter chose not to create a tracker)."""
    if not parent_page_id:
        await audit_log.write(
            org_id=org_id,
            requisition_id=requisition_id,
            actor_user_id=user_id,
            action="notion_create",
            status="skipped",
            request_summary={"reason": "no_parent_page_id"},
        )
        return None
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
            url = await _create_notion_tracker(
                org_id=org_id,
                title=title,
                jd_content=jd_content,
                ats_postings=ats_postings,
                parent_page_id=parent_page_id,
            )
            if not url:
                # Notion returned 200 but no URL in the response — treat as a
                # failure so the audit row reflects that no tracker exists.
                # `audit_log.timed` records status="failure" + error_message.
                raise RuntimeError("notion_returned_no_url")
            ctx.response_summary = {"url": url}
            return url
    except PermissionError as exc:
        # Bot not shared on the parent page, or no Notion token for the org.
        # Audit row already recorded as failure by timed() — log + swallow so
        # the publish flow isn't aborted.
        log.warning("recruiting.notion.permission org=%s err=%s", org_id, exc)
        return None
    except Exception as exc:
        log.warning("recruiting.notion.failed org=%s err=%s", org_id, exc)
        return None


async def _notify_slack_audited(
    *,
    org_id: str,
    requisition_id: str,
    user_id: str,
    channel: str | None,
    title: str,
    ats_postings: list[dict[str, Any]],
    hiring_manager_email: str | None,
) -> None:
    if not channel:
        await audit_log.write(
            org_id=org_id,
            requisition_id=requisition_id,
            actor_user_id=user_id,
            action="slack_notify",
            status="skipped",
            request_summary={"reason": "no_channel"},
        )
        return
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
            await _notify_slack(
                org_id=org_id,
                channel=channel,
                title=title,
                ats_postings=ats_postings,
                hiring_manager_email=hiring_manager_email,
            )
            ctx.response_summary = {"posted": True}
    except Exception:
        return


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
                event_type="recruiting_published",  # type: ignore[arg-type]
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
