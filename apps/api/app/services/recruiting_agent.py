"""Recruiting Agent (#20) — service layer.

Pipeline:
  generate_job_requisition()
      KB search across 4 facets (templates, compensation, requirements,
      team-structure) → Gemini produces 5 JD variants with distinct tones.
      Persists to job_requisitions with status='draft'.

  publish_requisition()
      Posts the selected variant via the chosen ATS adapter, then runs four
      best-effort side-effects in parallel:
        * Notion hiring tracker page
        * LinkedIn sourcing drafts + Recruiter search URLs
        * Slack notification to the hiring manager / channel
      A failure in any side-effect is logged but does NOT mark the
      requisition failed — the JD made it into the ATS, which is the
      load-bearing step.
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

log = logging.getLogger(__name__)


# ── 1. Generate ──────────────────────────────────────────────────────────────


_FIVE_TONES = [
    "startup-casual",
    "enterprise-formal",
    "mission-driven",
    "concise-pragmatic",
    "ic-builder",  # IC-focused; signals depth over scope
]


_GENERATE_SYSTEM = """You are a senior in-house recruiter. You write job descriptions that hit the company's specific tone, comp band, and seniority signals — never generic templates.

You will be given four facets of company-grounded context (templates, compensation bands, role requirements, team structure). Use it. Quote concrete details: comp ranges if present, specific tools/stack, location/remote policy, reporting line.

You will write FIVE genuinely distinct JD variants for the same role, each in a different tone. The variants must differ in *voice and structure*, not just word choice — e.g. one is bullet-heavy, one prose-led, one mission-first, one comp-and-stack-first.

Output JSON ONLY. No prose around it. Schema:
{
  "variants": [
    {"tone": "<one of the five tones>", "text": "<full JD markdown ~300–600 words>"}
  ]
}
""".strip()


async def generate_job_requisition(
    *,
    org_id: str,
    user_id: str,
    role_request: str,
    location: str | None,
    department: str | None,
) -> dict[str, Any]:
    """Search → synthesize → persist. Returns the inserted row."""
    facets = {
        "templates": f"JD template structure for {role_request}",
        "compensation": f"compensation band salary range {role_request}",
        "requirements": f"role requirements skills experience {role_request}",
        "team_structure": f"team structure org chart reporting line for {role_request}",
    }
    facet_results = await search_facets_concurrent(
        org_id=org_id, facets=facets, k=6, char_budget_per_facet=3500
    )
    context_block = build_context_block(facet_results)
    sources = collect_sources(facet_results)

    user_prompt = (
        f"## Role to hire\n{role_request}\n"
        + (f"\n## Location\n{location}\n" if location else "")
        + (f"\n## Department\n{department}\n" if department else "")
        + f"\n## Tones to produce (in this order)\n{', '.join(_FIVE_TONES)}\n"
        + (f"\n## Company-grounded context\n{context_block}\n" if context_block else "")
        + "\n## Output\nReturn JSON only matching the schema in your system prompt."
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

    if len(variants) < 3:
        # Hard floor; if the model couldn't produce at least 3 distinct
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
                    "jd_variants": [v.model_dump() for v in variants],
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
        "recruiting.generate.ok org=%s req=%s variants=%d",
        org_id,
        row["id"],
        len(variants),
    )
    return {**row, "sources": sources}


# ── 2. Publish ───────────────────────────────────────────────────────────────


async def publish_requisition(
    *,
    org_id: str,
    requisition_id: str,
    user_id: str,
    selected_variant_index: int,
    ats_platform: AtsPlatform,
    hiring_manager_email: str | None,
    slack_channel: str | None,
    notion_parent_page_id: str | None,
) -> dict[str, Any]:
    """Publish to ATS, then best-effort side-effects in parallel.

    The ATS publish is the only step that can mark the requisition failed.
    Notion / sourcing / Slack are best-effort: a Slack 404 doesn't undo
    a successful Greenhouse posting.
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

    variants = row.get("jd_variants") or []
    if selected_variant_index >= len(variants):
        raise ValueError("selected_variant_index_out_of_range")
    variant = variants[selected_variant_index]
    title = _extract_title(row.get("role_request") or "", variant.get("text") or "")
    content = variant.get("text") or ""

    adapter = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}[ats_platform]

    try:
        ats_result = await adapter.publish_job(
            org_id=org_id,
            title=title,
            content=content,
            location=None,
            department=None,
        )
    except PermissionError as exc:
        msg = f"ats_not_connected_or_unauthorized: {exc}"
        await _mark_failed(requisition_id, org_id, msg)
        raise PermissionError(msg) from exc
    except Exception as exc:
        msg = f"ats_publish_failed: {exc}"
        await _mark_failed(requisition_id, org_id, msg)
        raise RuntimeError(msg) from exc

    # Side-effects: every one is wrapped in its own try so a single failure
    # doesn't cascade. Outputs are aggregated into the persisted row.
    sourcing_task = asyncio.create_task(
        _draft_sourcing(org_id=org_id, role_request=row["role_request"], jd=content)
    )
    notion_task = asyncio.create_task(
        _create_notion_tracker(
            org_id=org_id,
            title=title,
            jd_content=content,
            ats_url=ats_result["url"],
            parent_page_id=notion_parent_page_id,
        )
    )
    slack_task = asyncio.create_task(
        _notify_slack(
            org_id=org_id,
            channel=slack_channel,
            title=title,
            ats_url=ats_result["url"],
            hiring_manager_email=hiring_manager_email,
        )
    )

    sourcing_drafts, linkedin_urls = await sourcing_task
    notion_url = await notion_task
    # Slack is fire-and-await; we don't propagate its return.
    await slack_task

    def _update() -> dict[str, Any]:
        res = (
            svc.table("job_requisitions")
            .update(
                {
                    "selected_variant_index": selected_variant_index,
                    "ats_platform": ats_platform,
                    "ats_job_id": ats_result["job_id"],
                    "ats_url": ats_result["url"],
                    "notion_tracker_url": notion_url,
                    "sourcing_templates": [t.model_dump() for t in sourcing_drafts],
                    "linkedin_search_urls": linkedin_urls,
                    "hiring_manager_email": hiring_manager_email,
                    "slack_channel": slack_channel,
                    "status": "published",
                    "error_message": None,
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
        "recruiting.publish.ok req=%s ats=%s job_id=%s notion=%s",
        requisition_id,
        ats_platform,
        ats_result["job_id"],
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


async def _create_notion_tracker(
    *,
    org_id: str,
    title: str,
    jd_content: str,
    ats_url: str,
    parent_page_id: str | None,
) -> str | None:
    """Create a hiring tracker page in Notion. Returns the URL or None."""
    if not parent_page_id:
        return None
    from app.services.integrations import notion as notion_svc

    tracker_content = (
        f"# Hiring Tracker — {title}\n\n"
        f"**ATS link:** {ats_url}\n\n"
        f"## Pipeline\n- [ ] Sourcing\n- [ ] Outreach sent\n- [ ] Phone screens\n"
        f"- [ ] Onsites\n- [ ] Offer\n- [ ] Closed\n\n"
        f"## Candidates\n_(add candidates with status here)_\n\n"
        f"## Notes\n\n## Job description\n\n{jd_content}\n"
    )
    try:
        result = await notion_svc.create_page(
            org_id=org_id,
            parent_page_id=parent_page_id,
            title=f"Hiring — {title}",
            content=tracker_content,
        )
        return result.get("url")
    except PermissionError as exc:
        log.warning("recruiting.notion.permission org=%s err=%s", org_id, exc)
    except Exception as exc:
        log.warning("recruiting.notion.failed org=%s err=%s", org_id, exc)
    return None


async def _notify_slack(
    *,
    org_id: str,
    channel: str | None,
    title: str,
    ats_url: str,
    hiring_manager_email: str | None,
) -> None:
    """Post one Slack message. No-ops when channel is empty."""
    if not channel:
        return
    from app.services.integrations import slack as slack_svc

    msg = f"📢 New job opening: *{title}* — {ats_url}"
    if hiring_manager_email:
        msg += f"\nHiring manager: {hiring_manager_email}"
    try:
        await slack_svc.post_message(org_id=org_id, channel_id=channel, text=msg)
    except Exception as exc:
        log.warning("recruiting.slack.failed org=%s ch=%s err=%s", org_id, channel, exc)
