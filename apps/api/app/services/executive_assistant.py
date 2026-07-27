"""Executive Assistant Agent (#25, Agent2 Day 6).

Pipeline:
  generate_briefing(request, recipients, schedule_followup)
    1. Concurrent KB search across 5 facets (market, competitive,
       customers, financials, risks).
    2. Synthesize a structured briefing via Gemini.
    3. Create a Google Doc with the structured content.
    4. Send the doc link to recipients via Gmail.
    5. Optionally schedule a follow-up meeting on Google Calendar.

Each step writes to the executive_briefings row as it progresses so the
frontend's poll/SSE can show "doc created → email sent". A failure mid-way
(e.g. recipient bounces) does not undo the doc — that's the load-bearing
deliverable, and the user can resend manually.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from app.database import get_service_client
from app.models.executive_assistant import BriefingSections
from app.services.agents.kb_synthesis import (
    build_context_block,
    collect_sources,
    search_facets_concurrent,
    synthesize_json,
)
from app.services.integrations import google_calendar, google_docs, google_workspace
from app.services.integrations import gmail as gmail_svc

log = logging.getLogger(__name__)


_BRIEF_SYSTEM = """You are a chief of staff. Your job is to compress the company's full context into a 1-page executive briefing for a request like "Prepare a briefing on competitive positioning for our board meeting".

Constraints:
- Use ONLY the company-internal context provided. If a section has no signal, say so honestly ("Limited internal documentation on X — recommend follow-up with Y team.").
- Be specific: name customers, products, numbers, dates from the context.
- Each section is 80–200 words, prose (not bullets), exec-readable at 6am.

Output JSON only:
{
  "executive_summary": "string — top-line strategic read in 3-4 sentences",
  "market_context": "string — landscape, trends, sizing",
  "competitive_advantages": "string — differentiation, moats, validation",
  "risks": "string — what could break, near-term threats",
  "recommendations": "string — specific actions, decisions to seek, owners"
}
""".strip()


async def generate_briefing(
    *,
    org_id: str,
    user_id: str,
    request_text: str,
    recipients: list[str],
    schedule_followup: bool,
    followup_proposed_start: datetime | None,
    followup_proposed_end: datetime | None,
    followup_title: str | None,
) -> dict[str, Any]:
    """End-to-end: search → synthesize → doc → email → optional calendar."""
    svc = get_service_client()

    # Create the row up-front so the frontend can poll status.
    def _insert_row() -> dict[str, Any]:
        res = (
            svc.table("executive_briefings")
            .insert(
                {
                    "org_id": org_id,
                    "created_by": user_id,
                    "request_text": request_text,
                    "recipients": recipients,
                    "status": "draft",
                }
            )
            .execute()
        )
        return (res.data or [{}])[0]

    row = await asyncio.to_thread(_insert_row)
    briefing_id = row["id"]

    try:
        sections = await _synthesize_sections(org_id=org_id, request_text=request_text)
        await _update(briefing_id, {"sections": sections.model_dump()})

        # Title: first sentence or first 80 chars of the request.
        title = _derive_title(request_text)

        doc_result = await google_docs.create_briefing_doc(
            org_id=org_id,
            user_id=user_id,
            title=title,
            sections={
                "Executive Summary": sections.executive_summary,
                "Market Context": sections.market_context,
                "Competitive Advantages": sections.competitive_advantages,
                "Risks": sections.risks,
                "Recommendations": sections.recommendations,
            },
        )
        await _update(
            briefing_id,
            {
                "google_doc_id": doc_result["doc_id"],
                "google_doc_url": doc_result["url"],
                "status": "doc_created",
            },
        )

        if recipients:
            await _send_briefing_email(
                org_id=org_id,
                user_id=user_id,
                recipients=recipients,
                title=title,
                doc_url=doc_result["url"],
                request_text=request_text,
            )
            await _update(
                briefing_id,
                {"status": "sent", "sent_at": datetime.now(UTC).isoformat()},
            )

        followup_info: dict[str, Any] = {}
        if (
            schedule_followup
            and followup_proposed_start
            and followup_proposed_end
            and recipients
        ):
            try:
                ev = await google_calendar.create_event(
                    org_id=org_id,
                    user_id=user_id,
                    title=followup_title or f"Follow-up: {title}",
                    description=f"Briefing doc: {doc_result['url']}",
                    start=followup_proposed_start,
                    end=followup_proposed_end,
                    attendee_emails=recipients,
                )
                followup_info = {
                    "followup_event_id": ev.get("event_id"),
                    "followup_event_url": ev.get("url"),
                }
                await _update(briefing_id, followup_info)
            except PermissionError as exc:
                # Soft failure — log + flag in metadata; the briefing is
                # already delivered.
                log.info("exec.followup.scope_missing user=%s err=%s", user_id, exc)
            except Exception as exc:
                log.warning("exec.followup.failed user=%s err=%s", user_id, exc)

        # Final fetch so the response includes all the writes we just did.
        def _fetch() -> dict[str, Any]:
            res = (
                svc.table("executive_briefings")
                .select("*")
                .eq("id", briefing_id)
                .single()
                .execute()
            )
            return res.data or {}

        return await asyncio.to_thread(_fetch)

    except PermissionError as exc:
        await _update(
            briefing_id,
            {"status": "failed", "error_message": f"permission: {exc}"},
        )
        raise
    except Exception as exc:
        log.exception("exec.briefing.failed id=%s", briefing_id)
        await _update(briefing_id, {"status": "failed", "error_message": str(exc)})
        raise


# ── Helpers ─────────────────────────────────────────────────────────────────


async def _synthesize_sections(
    *,
    org_id: str,
    request_text: str,
) -> BriefingSections:
    facets = {
        "market": f"market context industry size trends related to: {request_text}",
        "competitive": f"competitive landscape differentiation moats related to: {request_text}",
        "customers": f"customer case studies wins references related to: {request_text}",
        "financials": f"financial metrics projections unit economics related to: {request_text}",
        "risks": f"risks threats blockers concerns related to: {request_text}",
    }
    facet_results = await search_facets_concurrent(
        org_id=org_id, facets=facets, k=6, char_budget_per_facet=2500
    )
    context = build_context_block(facet_results)
    prompt = (
        f"## Request\n{request_text}\n\n"
        + (f"## Company-internal context\n{context}\n" if context else "")
        + "\n## Output\nJSON only per the schema in your system prompt."
    )
    result = await synthesize_json(
        system_prompt=_BRIEF_SYSTEM,
        user_prompt=prompt,
        temperature=0.4,
        timeout=120.0,
    )
    if not isinstance(result, dict):
        raise RuntimeError("exec_synthesis_returned_non_object")
    return BriefingSections(**result)


def _derive_title(request_text: str) -> str:
    """Heuristic title from the request. First sentence, capped at 80 chars."""
    text = request_text.strip()
    for sep in [". ", "? ", "! ", "\n"]:
        if sep in text:
            text = text.split(sep, 1)[0]
            break
    return text[:80] or "Executive Briefing"


async def _send_briefing_email(
    *,
    org_id: str,
    user_id: str,
    recipients: list[str],
    title: str,
    doc_url: str,
    request_text: str,
) -> None:
    """Send via Gmail. Prefer the per-user Gmail creds row (lighter scope)
    if it exists; fall back to the google_workspace row's send capability."""
    creds = await gmail_svc.get_user_credentials(org_id=org_id, user_id=user_id)
    if creds:
        access_token = creds["access_token"]
        sender = creds["email_address"]
    else:
        # google_workspace integration includes gmail.send; reuse its token.
        access_token = await google_workspace.get_user_token(org_id=org_id, user_id=user_id)
        sender = await google_workspace.get_user_email(org_id=org_id, user_id=user_id)
        if not (access_token and sender):
            raise PermissionError("no_gmail_send_credentials")

    body = (
        f"Hi,\n\n"
        f"Per your request — '{request_text}' — here's the briefing:\n\n"
        f"{doc_url}\n\n"
        f"Generated by Contexo. Reply with edits and I'll regenerate.\n"
    )
    # Gmail's API only lets you send one recipient per call. Loop, but cap
    # at the @model recipient limit (20).
    for r in recipients[:20]:
        await gmail_svc.send_email(
            access_token=access_token,
            sender=sender,
            to=r,
            subject=f"Briefing: {title}",
            body=body,
        )


async def _update(briefing_id: str, fields: dict[str, Any]) -> None:
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("executive_briefings")
        .update(fields)
        .eq("id", briefing_id)
        .execute()
    )
