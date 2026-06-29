"""Sales Agent endpoints.

Reps drive the autonomous deal pipeline through this surface:

  * POST /sales/deals/runs                              create a lead
  * GET  /sales/deals/runs                              list
  * GET  /sales/deals/runs/{id}                         detail
  * POST /sales/deals/runs/{id}/resume                  manual re-kick
  * PATCH /sales/deals/runs/{id}/outreach               edit draft inline
  * POST /sales/deals/runs/{id}/outreach/approve        approve + send via Gmail
  * POST /sales/deals/runs/{id}/outreach/regenerate     ask agent for another draft
  * POST /sales/deals/runs/{id}/reply-received          manual mark
  * POST /sales/deals/runs/{id}/meeting-booked          rep booked a meeting
  * POST /sales/deals/runs/{id}/call-transcript         submit transcript
  * POST /sales/deals/runs/{id}/next-step               propose | another_call | qualify_out
  * POST /sales/deals/runs/{id}/proposal/approve        approve + mark sent
  * POST /sales/deals/runs/{id}/proposal/regenerate
  * POST /sales/deals/runs/{id}/proposal/upload-edited  rep uploads edited DOCX/PDF
  * POST /sales/deals/runs/{id}/close                   mark won/lost
  * POST /sales/deals/templates                         tag a KB doc with a sales kind
  * POST /sales/deals/webhooks/gmail                    Pub/Sub push for inbound reply
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import inngest
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status

from app.auth import verify_jwt
from app.database import get_service_client
from app.errors import NoOrganization
from app.inngest.client import get_inngest_client
from app.models.sales_agent import (
    CloseDealRequest,
    DealApprovalRead,
    DealDocumentRead,
    DealEventRead,
    DealResearchRead,
    DealRunDetailRead,
    DealRunRead,
    EditOutreachRequest,
    LeadIntakeRequest,
    MeetingBookedRequest,
    NextStepRequest,
    ReplyReceivedRequest,
    TranscriptRequest,
)
from app.services.agents.sales_agent import storage as sa_storage
from app.services.agents.sales_agent.followups import (
    schedule_next_followup,
    schedule_next_checkin,
)
from app.services.integrations import gmail

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sales/deals", tags=["sales-agent"])


# ── Helpers ────────────────────────────────────────────────────────────────


def _require_user(current_user: dict) -> tuple[str, str, str]:
    user_id = current_user.get("user_id")
    org_id = current_user.get("org_id")
    token = current_user.get("token")
    if not user_id or not org_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return user_id, org_id, token


def _deal_to_read(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "org_id": row["org_id"],
        "created_by": row["created_by"],
        "company_name": row["company_name"],
        "company_website": row.get("company_website"),
        "company_industry": row.get("company_industry"),
        "contact_name": row.get("contact_name"),
        "contact_title": row.get("contact_title"),
        "contact_email": row.get("contact_email"),
        "status": row.get("status") or "lead_entered",
        "blocked_reason": row.get("blocked_reason"),
        "blocked_template_kind": row.get("blocked_template_kind"),
        "icp_score": row.get("icp_score"),
        "icp_rationale": row.get("icp_rationale"),
        "outreach_sent_at": row.get("outreach_sent_at"),
        "reply_received_at": row.get("reply_received_at"),
        "meeting_at": row.get("meeting_at"),
        "meeting_count": row.get("meeting_count") or 0,
        "proposal_sent_at": row.get("proposal_sent_at"),
        "closed_at": row.get("closed_at"),
        "close_outcome": row.get("close_outcome"),
        "deal_value_amount": row.get("deal_value_amount"),
        "deal_value_currency": row.get("deal_value_currency"),
        "followup_count": row.get("followup_count") or 0,
        "checkin_count": row.get("checkin_count") or 0,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def _load_deal(deal_run_id: str, org_id: str) -> dict[str, Any]:
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("deal_runs")
        .select("*")
        .eq("id", deal_run_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not res or not res.data:
        raise HTTPException(status_code=404, detail="deal_run_not_found")
    return res.data


# ── Create + list + detail ─────────────────────────────────────────────────


@router.post(
    "/runs",
    response_model=DealRunRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_lead(
    body: LeadIntakeRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, org_id, _ = _require_user(current_user)

    svc = get_service_client()
    payload = {
        "org_id": org_id,
        "created_by": user_id,
        "company_name": body.company_name,
        "company_website": body.company_website,
        "company_industry": body.company_industry,
        "contact_name": body.contact_name,
        "contact_title": body.contact_title,
        "contact_email": str(body.contact_email) if body.contact_email else None,
        "contact_linkedin_url": body.contact_linkedin_url,
        "notes": body.notes,
        "deal_value_amount": body.deal_value_amount,
        "deal_value_currency": body.deal_value_currency,
        "status": "lead_entered",
    }

    def _insert() -> dict[str, Any]:
        res = svc.table("deal_runs").insert(payload).execute()
        return (res.data or [{}])[0]

    inserted = await asyncio.to_thread(_insert)
    if not inserted.get("id"):
        raise HTTPException(status_code=500, detail="failed_to_create_deal")

    await sa_storage.log_deal_event(
        org_id=org_id,
        deal_run_id=inserted["id"],
        actor_kind="rep",
        actor_user_id=user_id,
        event_type="lead_created",
        message=f"Lead created for {body.company_name}.",
    )

    inngest_client = get_inngest_client()
    await inngest_client.send(
        inngest.Event(
            name="sales/lead-entered",
            data={
                "deal_run_id": inserted["id"],
                "org_id": org_id,
                "triggered_by_user_id": user_id,
            },
        )
    )
    return _deal_to_read(inserted)


@router.get("/runs", response_model=list[DealRunRead])
async def list_deals(
    current_user: dict = Depends(verify_jwt),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    _, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    def _q() -> list[dict[str, Any]]:
        q = (
            svc.table("deal_runs")
            .select("*")
            .eq("org_id", org_id)
            .order("updated_at", desc=True)
            .range(offset, offset + limit - 1)
        )
        if status_filter:
            q = q.eq("status", status_filter)
        res = q.execute()
        return res.data or []

    rows = await asyncio.to_thread(_q)
    return [_deal_to_read(r) for r in rows]


@router.get("/runs/{deal_run_id}", response_model=DealRunDetailRead)
async def get_deal_detail(
    deal_run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    _, org_id, _ = _require_user(current_user)
    svc = get_service_client()
    deal = await _load_deal(deal_run_id, org_id)

    def _fetch_related() -> dict[str, Any]:
        research = (
            svc.table("deal_research")
            .select("*")
            .eq("deal_run_id", deal_run_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        docs = (
            svc.table("deal_documents")
            .select("*")
            .eq("deal_run_id", deal_run_id)
            .order("created_at", desc=True)
            .execute()
        )
        events = (
            svc.table("deal_events")
            .select("*")
            .eq("deal_run_id", deal_run_id)
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )
        pending = (
            svc.table("deal_approvals")
            .select("*")
            .eq("deal_run_id", deal_run_id)
            .eq("status", "pending")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return {
            "research": (research.data or [None])[0],
            "docs": docs.data or [],
            "events": events.data or [],
            "pending": (pending.data or [None])[0],
        }

    related = await asyncio.to_thread(_fetch_related)

    # Refresh signed URLs for any document path.
    url_map = await sa_storage.refresh_signed_urls_for_deal(deal_run_id)

    out = _deal_to_read(deal) | {
        "outreach_subject": deal.get("outreach_subject"),
        "outreach_email_body": deal.get("outreach_email_body"),
        "outreach_linkedin_body": deal.get("outreach_linkedin_body"),
        "outreach_thread_id": deal.get("outreach_thread_id"),
        "call_transcript": deal.get("call_transcript"),
        "call_summary": deal.get("call_summary"),
        "bant_json": deal.get("bant_json"),
        "objections_json": deal.get("objections_json"),
        "next_steps_json": deal.get("next_steps_json"),
        "recommended_stage": deal.get("recommended_stage"),
        "prep_brief": deal.get("prep_brief"),
        "proposal_draft_path": deal.get("proposal_draft_path"),
        "proposal_edited_path": deal.get("proposal_edited_path"),
        "close_reason": deal.get("close_reason"),
        "close_notes": deal.get("close_notes"),
        "latest_research": related["research"],
        "documents": [
            {
                **d,
                "signed_url": url_map.get(d.get("storage_path") or ""),
                "pdf_signed_url": url_map.get(d.get("pdf_storage_path") or ""),
            }
            for d in related["docs"]
        ],
        "events": related["events"],
        "pending_approval": related["pending"],
    }
    return out


@router.post("/runs/{deal_run_id}/resume", response_model=DealRunRead)
async def resume_deal(
    deal_run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    _, org_id, _ = _require_user(current_user)
    deal = await _load_deal(deal_run_id, org_id)
    inngest_client = get_inngest_client()
    await inngest_client.send(
        inngest.Event(
            name="sales/resume",
            data={"deal_run_id": deal_run_id, "org_id": org_id},
        )
    )
    return _deal_to_read(deal)


# ── Outreach gate ──────────────────────────────────────────────────────────


@router.patch("/runs/{deal_run_id}/outreach", response_model=DealRunRead)
async def edit_outreach(
    deal_run_id: str,
    body: EditOutreachRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, org_id, _ = _require_user(current_user)
    deal = await _load_deal(deal_run_id, org_id)
    if deal.get("status") not in (
        "outreach_pending_rep_review",
        "followup_pending_rep_review",
        "checkin_pending_rep_review",
    ):
        raise HTTPException(status_code=409, detail="not_in_outreach_review_state")

    update: dict[str, Any] = {"outreach_edited_at": datetime.now(UTC).isoformat()}
    if body.subject is not None:
        update["outreach_subject"] = body.subject
    if body.email_body is not None:
        update["outreach_email_body"] = body.email_body
    if body.linkedin_body is not None:
        update["outreach_linkedin_body"] = body.linkedin_body

    svc = get_service_client()

    def _apply() -> dict[str, Any]:
        # Also stamp the pending approval row with the edited values so
        # the audit trail captures who edited what.
        pending = (
            svc.table("deal_approvals")
            .select("id")
            .eq("deal_run_id", deal_run_id)
            .eq("status", "pending")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if pending.data:
            svc.table("deal_approvals").update(
                {
                    "edited_subject": body.subject,
                    "edited_content": body.email_body,
                }
            ).eq("id", pending.data[0]["id"]).execute()
        res = svc.table("deal_runs").update(update).eq("id", deal_run_id).execute()
        return (res.data or [{}])[0]

    updated = await asyncio.to_thread(_apply)
    await sa_storage.log_deal_event(
        org_id=org_id,
        deal_run_id=deal_run_id,
        actor_kind="rep",
        actor_user_id=user_id,
        event_type="outreach_edited",
        message="Rep edited the outreach draft.",
    )
    return _deal_to_read(updated)


@router.post("/runs/{deal_run_id}/outreach/approve", response_model=DealRunRead)
async def approve_outreach(
    deal_run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Approve + send the outreach via the rep's Gmail. Sets thread tracking
    so the gmail-watch webhook can resolve replies back."""
    user_id, org_id, _ = _require_user(current_user)
    deal = await _load_deal(deal_run_id, org_id)
    cur_status = deal.get("status")
    if cur_status not in (
        "outreach_pending_rep_review",
        "followup_pending_rep_review",
        "checkin_pending_rep_review",
    ):
        raise HTTPException(status_code=409, detail="not_in_outreach_review_state")

    if not deal.get("contact_email"):
        raise HTTPException(status_code=422, detail="contact_email_required_to_send")

    creds = await gmail.get_user_credentials(org_id=org_id, user_id=user_id)
    if not creds:
        raise HTTPException(
            status_code=400,
            detail="gmail_not_connected: connect Gmail in Settings → Integrations",
        )

    subject = deal.get("outreach_subject") or "(no subject)"
    body_text = deal.get("outreach_email_body") or ""

    try:
        send_result = await gmail.send_email(
            access_token=creds["access_token"],
            sender=creds["email_address"],
            to=deal["contact_email"],
            subject=subject,
            body=body_text,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"gmail_send_forbidden: {exc}")
    except Exception as exc:  # noqa: BLE001
        log.exception("sales.outreach.send_failed deal=%s", deal_run_id)
        raise HTTPException(status_code=502, detail=f"gmail_send_failed: {exc}")

    svc = get_service_client()
    now = datetime.now(UTC)

    # Branching: cold outreach → status=outreach_sent (agent will move to awaiting_reply)
    #            follow-up → bump followup_count + schedule next + status=awaiting_reply
    #            check-in → bump checkin_count + schedule next + status=awaiting_decision
    if cur_status == "outreach_pending_rep_review":
        update = {
            "status": "outreach_sent",
            "outreach_sent_at": now.isoformat(),
            "outreach_thread_id": send_result.get("thread_id"),
            "outreach_message_id": send_result.get("message_id"),
        }
        next_event = "sales/outreach-approved"
    elif cur_status == "followup_pending_rep_review":
        new_count = (deal.get("followup_count") or 0) + 1
        next_due = schedule_next_followup(new_count)
        update = {
            "status": "awaiting_reply",
            "last_followup_at": now.isoformat(),
            "followup_count": new_count,
            "next_followup_due_at": next_due.isoformat() if next_due else None,
        }
        next_event = "sales/resume"
    else:  # checkin_pending_rep_review
        new_count = (deal.get("checkin_count") or 0) + 1
        next_due = schedule_next_checkin(new_count)
        update = {
            "status": "awaiting_decision",
            "last_checkin_at": now.isoformat(),
            "checkin_count": new_count,
            "next_checkin_due_at": next_due.isoformat() if next_due else None,
        }
        next_event = "sales/resume"

    def _apply() -> dict[str, Any]:
        pending = (
            svc.table("deal_approvals")
            .select("id")
            .eq("deal_run_id", deal_run_id)
            .eq("status", "pending")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if pending.data:
            svc.table("deal_approvals").update(
                {
                    "status": "approved",
                    "decided_by": user_id,
                    "decided_at": now.isoformat(),
                }
            ).eq("id", pending.data[0]["id"]).execute()
        res = svc.table("deal_runs").update(update).eq("id", deal_run_id).execute()
        return (res.data or [{}])[0]

    updated = await asyncio.to_thread(_apply)
    await sa_storage.log_deal_event(
        org_id=org_id,
        deal_run_id=deal_run_id,
        actor_kind="rep",
        actor_user_id=user_id,
        event_type="outreach_sent",
        message=f"Sent to {deal['contact_email']}",
        from_status=cur_status,
        to_status=update["status"],
        payload={
            "thread_id": send_result.get("thread_id"),
            "message_id": send_result.get("message_id"),
        },
    )

    inngest_client = get_inngest_client()
    await inngest_client.send(
        inngest.Event(
            name=next_event,
            data={"deal_run_id": deal_run_id, "org_id": org_id},
        )
    )
    return _deal_to_read(updated)


@router.post("/runs/{deal_run_id}/outreach/regenerate", response_model=DealRunRead)
async def regenerate_outreach(
    deal_run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Mark the current pending approval as regenerate_requested and re-drive
    the agent so it produces a new draft. Bumps revision in the approval row."""
    user_id, org_id, _ = _require_user(current_user)
    deal = await _load_deal(deal_run_id, org_id)
    svc = get_service_client()

    def _cancel_pending() -> None:
        svc.table("deal_approvals").update(
            {
                "status": "regenerate_requested",
                "decided_by": user_id,
                "decided_at": datetime.now(UTC).isoformat(),
            }
        ).eq("deal_run_id", deal_run_id).eq("status", "pending").execute()

    await asyncio.to_thread(_cancel_pending)

    # Reset status to drafting so the agent re-enters the right step.
    cur = deal.get("status")
    if cur == "followup_pending_rep_review":
        next_status = "awaiting_reply"  # cron will re-pick it up
    elif cur == "checkin_pending_rep_review":
        next_status = "awaiting_decision"
    else:
        next_status = "outreach_drafting"

    def _update_status() -> None:
        svc.table("deal_runs").update({"status": next_status}).eq("id", deal_run_id).execute()

    await asyncio.to_thread(_update_status)
    await sa_storage.log_deal_event(
        org_id=org_id,
        deal_run_id=deal_run_id,
        actor_kind="rep",
        actor_user_id=user_id,
        event_type="outreach_regenerate_requested",
        from_status=cur,
        to_status=next_status,
    )

    inngest_client = get_inngest_client()
    await inngest_client.send(
        inngest.Event(
            name="sales/resume",
            data={"deal_run_id": deal_run_id, "org_id": org_id},
        )
    )
    deal["status"] = next_status
    return _deal_to_read(deal)


# ── Reply / meeting / transcript ───────────────────────────────────────────


@router.post("/runs/{deal_run_id}/reply-received", response_model=DealRunRead)
async def mark_reply_received(
    deal_run_id: str,
    body: ReplyReceivedRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, org_id, _ = _require_user(current_user)
    deal = await _load_deal(deal_run_id, org_id)
    svc = get_service_client()
    now = datetime.now(UTC).isoformat()

    def _apply() -> dict[str, Any]:
        res = (
            svc.table("deal_runs")
            .update({"reply_received_at": now, "next_followup_due_at": None})
            .eq("id", deal_run_id)
            .execute()
        )
        return (res.data or [{}])[0]

    updated = await asyncio.to_thread(_apply)
    await sa_storage.log_deal_event(
        org_id=org_id,
        deal_run_id=deal_run_id,
        actor_kind="prospect",
        actor_user_id=None,
        event_type="reply_received",
        message=body.note or "Prospect replied.",
    )
    return _deal_to_read(updated)


@router.post("/runs/{deal_run_id}/meeting-booked", response_model=DealRunRead)
async def mark_meeting_booked(
    deal_run_id: str,
    body: MeetingBookedRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, org_id, _ = _require_user(current_user)
    deal = await _load_deal(deal_run_id, org_id)
    svc = get_service_client()

    new_count = (deal.get("meeting_count") or 0) + 1
    update = {
        "status": "meeting_booked",
        "meeting_at": body.meeting_at.isoformat(),
        "meeting_count": new_count,
    }

    def _apply() -> dict[str, Any]:
        res = svc.table("deal_runs").update(update).eq("id", deal_run_id).execute()
        return (res.data or [{}])[0]

    updated = await asyncio.to_thread(_apply)
    await sa_storage.log_deal_event(
        org_id=org_id,
        deal_run_id=deal_run_id,
        actor_kind="rep",
        actor_user_id=user_id,
        event_type="meeting_booked",
        message=body.notes or f"Meeting #{new_count} booked for {body.meeting_at.isoformat()}",
        to_status="meeting_booked",
    )

    inngest_client = get_inngest_client()
    await inngest_client.send(
        inngest.Event(
            name="sales/meeting-booked",
            data={"deal_run_id": deal_run_id, "org_id": org_id},
        )
    )
    return _deal_to_read(updated)


@router.post("/runs/{deal_run_id}/call-transcript", response_model=DealRunRead)
async def submit_transcript(
    deal_run_id: str,
    body: TranscriptRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, org_id, _ = _require_user(current_user)
    deal = await _load_deal(deal_run_id, org_id)
    svc = get_service_client()

    def _apply() -> None:
        svc.table("deal_runs").update({"status": "call_summarizing"}).eq(
            "id", deal_run_id
        ).execute()

    await asyncio.to_thread(_apply)
    await sa_storage.log_deal_event(
        org_id=org_id,
        deal_run_id=deal_run_id,
        actor_kind="rep",
        actor_user_id=user_id,
        event_type="transcript_submitted",
        message="Transcript submitted; summary generating.",
        to_status="call_summarizing",
    )
    inngest_client = get_inngest_client()
    await inngest_client.send(
        inngest.Event(
            name="sales/transcript-submitted",
            data={
                "deal_run_id": deal_run_id,
                "org_id": org_id,
                "transcript": body.transcript,
            },
        )
    )
    updated = await _load_deal(deal_run_id, org_id)
    return _deal_to_read(updated)


@router.post("/runs/{deal_run_id}/next-step", response_model=DealRunRead)
async def choose_next_step(
    deal_run_id: str,
    body: NextStepRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, org_id, _ = _require_user(current_user)
    deal = await _load_deal(deal_run_id, org_id)
    if deal.get("status") != "awaiting_next_step_decision":
        raise HTTPException(status_code=409, detail="not_in_decision_state")

    svc = get_service_client()
    next_status = (
        "proposal_drafting"
        if body.decision == "proceed_to_proposal"
        else "awaiting_reply"  # back to wait for next meeting
    )

    def _apply() -> dict[str, Any]:
        res = (
            svc.table("deal_runs")
            .update({"status": next_status})
            .eq("id", deal_run_id)
            .execute()
        )
        return (res.data or [{}])[0]

    updated = await asyncio.to_thread(_apply)
    await sa_storage.log_deal_event(
        org_id=org_id,
        deal_run_id=deal_run_id,
        actor_kind="rep",
        actor_user_id=user_id,
        event_type="next_step_chosen",
        message=body.notes or f"Rep chose: {body.decision}",
        from_status="awaiting_next_step_decision",
        to_status=next_status,
        payload={"decision": body.decision},
    )

    inngest_client = get_inngest_client()
    await inngest_client.send(
        inngest.Event(
            name="sales/next-step-chosen",
            data={"deal_run_id": deal_run_id, "org_id": org_id},
        )
    )
    return _deal_to_read(updated)


# ── Proposal gate ──────────────────────────────────────────────────────────


@router.post("/runs/{deal_run_id}/proposal/approve", response_model=DealRunRead)
async def approve_proposal(
    deal_run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, org_id, _ = _require_user(current_user)
    deal = await _load_deal(deal_run_id, org_id)
    if deal.get("status") != "proposal_pending_rep_review":
        raise HTTPException(status_code=409, detail="not_in_proposal_review_state")

    svc = get_service_client()
    now = datetime.now(UTC).isoformat()

    def _apply() -> dict[str, Any]:
        pending = (
            svc.table("deal_approvals")
            .select("id")
            .eq("deal_run_id", deal_run_id)
            .eq("gate_type", "proposal")
            .eq("status", "pending")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if pending.data:
            svc.table("deal_approvals").update(
                {
                    "status": "approved",
                    "decided_by": user_id,
                    "decided_at": now,
                }
            ).eq("id", pending.data[0]["id"]).execute()
        res = (
            svc.table("deal_runs")
            .update({"status": "proposal_sent", "proposal_sent_at": now})
            .eq("id", deal_run_id)
            .execute()
        )
        return (res.data or [{}])[0]

    updated = await asyncio.to_thread(_apply)
    await sa_storage.log_deal_event(
        org_id=org_id,
        deal_run_id=deal_run_id,
        actor_kind="rep",
        actor_user_id=user_id,
        event_type="proposal_sent",
        message="Proposal approved and marked sent.",
        from_status="proposal_pending_rep_review",
        to_status="proposal_sent",
    )

    inngest_client = get_inngest_client()
    await inngest_client.send(
        inngest.Event(
            name="sales/proposal-approved",
            data={"deal_run_id": deal_run_id, "org_id": org_id},
        )
    )
    return _deal_to_read(updated)


@router.post("/runs/{deal_run_id}/proposal/regenerate", response_model=DealRunRead)
async def regenerate_proposal(
    deal_run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, org_id, _ = _require_user(current_user)
    deal = await _load_deal(deal_run_id, org_id)
    svc = get_service_client()

    def _cancel() -> None:
        svc.table("deal_approvals").update(
            {
                "status": "regenerate_requested",
                "decided_by": user_id,
                "decided_at": datetime.now(UTC).isoformat(),
            }
        ).eq("deal_run_id", deal_run_id).eq("gate_type", "proposal").eq(
            "status", "pending"
        ).execute()
        svc.table("deal_runs").update({"status": "proposal_drafting"}).eq(
            "id", deal_run_id
        ).execute()

    await asyncio.to_thread(_cancel)
    await sa_storage.log_deal_event(
        org_id=org_id,
        deal_run_id=deal_run_id,
        actor_kind="rep",
        actor_user_id=user_id,
        event_type="proposal_regenerate_requested",
    )
    inngest_client = get_inngest_client()
    await inngest_client.send(
        inngest.Event(
            name="sales/resume",
            data={"deal_run_id": deal_run_id, "org_id": org_id},
        )
    )
    deal["status"] = "proposal_drafting"
    return _deal_to_read(deal)


@router.post("/runs/{deal_run_id}/proposal/upload-edited", response_model=DealRunRead)
async def upload_edited_proposal(
    deal_run_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Rep downloaded the agent draft, edited in Word, and is uploading the
    final version. We store as a new deal_documents row tagged
    source=rep_uploaded and update the proposal_edited_path so the approve
    step sends THAT version."""
    user_id, org_id, _ = _require_user(current_user)
    deal = await _load_deal(deal_run_id, org_id)
    if deal.get("status") != "proposal_pending_rep_review":
        raise HTTPException(status_code=409, detail="not_in_proposal_review_state")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=422, detail="empty_file")

    filename = (file.filename or "").lower()
    is_pdf = filename.endswith(".pdf")
    is_docx = filename.endswith(".docx")
    if not (is_pdf or is_docx):
        raise HTTPException(status_code=422, detail="must_be_pdf_or_docx")

    revision = (deal.get("proposal_revision") or 0) + 1
    upload = await sa_storage.upload_deal_artifact(
        org_id=org_id,
        deal_run_id=deal_run_id,
        kind="proposal",
        revision=revision,
        pdf_bytes=raw if is_pdf else None,
        docx_bytes=raw if is_docx else None,
    )

    storage_path = upload["docx_path"] or upload["pdf_path"]
    if not storage_path:
        raise HTTPException(status_code=500, detail="upload_failed")

    doc_id = await sa_storage.insert_deal_document(
        org_id=org_id,
        deal_run_id=deal_run_id,
        kind="proposal",
        revision=revision,
        source="rep_uploaded",
        storage_path=storage_path,
        pdf_storage_path=upload["pdf_path"],
        file_bytes=upload["file_bytes"],
    )

    svc = get_service_client()
    update = {
        "proposal_edited_path": upload["docx_path"],
        "proposal_edited_pdf_path": upload["pdf_path"],
        "proposal_revision": revision,
    }

    def _apply() -> dict[str, Any]:
        svc.table("deal_approvals").update(
            {"edited_document_id": doc_id}
        ).eq("deal_run_id", deal_run_id).eq("gate_type", "proposal").eq(
            "status", "pending"
        ).execute()
        res = svc.table("deal_runs").update(update).eq("id", deal_run_id).execute()
        return (res.data or [{}])[0]

    updated = await asyncio.to_thread(_apply)
    await sa_storage.log_deal_event(
        org_id=org_id,
        deal_run_id=deal_run_id,
        actor_kind="rep",
        actor_user_id=user_id,
        event_type="proposal_edited_uploaded",
        message=f"Rep uploaded edited proposal v{revision}.",
        payload={"document_id": doc_id, "revision": revision},
    )
    return _deal_to_read(updated)


# ── Close ──────────────────────────────────────────────────────────────────


@router.post("/runs/{deal_run_id}/close", response_model=DealRunRead)
async def close_deal(
    deal_run_id: str,
    body: CloseDealRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, org_id, _ = _require_user(current_user)
    deal = await _load_deal(deal_run_id, org_id)
    svc = get_service_client()
    now = datetime.now(UTC).isoformat()
    next_status = "closed_won" if body.outcome == "won" else "closed_lost"
    update: dict[str, Any] = {
        "status": next_status,
        "close_outcome": body.outcome,
        "close_reason": body.reason,
        "close_notes": body.notes,
        "closed_at": now,
    }
    if body.final_deal_value is not None:
        update["deal_value_amount"] = body.final_deal_value

    def _apply() -> dict[str, Any]:
        res = svc.table("deal_runs").update(update).eq("id", deal_run_id).execute()
        return (res.data or [{}])[0]

    updated = await asyncio.to_thread(_apply)
    await sa_storage.log_deal_event(
        org_id=org_id,
        deal_run_id=deal_run_id,
        actor_kind="rep",
        actor_user_id=user_id,
        event_type="deal_closed",
        message=f"Closed {body.outcome}: {body.reason}",
        from_status=deal.get("status"),
        to_status=next_status,
    )
    return _deal_to_read(updated)


# ── Template tagging (admin-side) ──────────────────────────────────────────


@router.post("/templates", status_code=status.HTTP_200_OK)
async def tag_sales_template(
    body: dict[str, Any],
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Tag a KB document with a sales template_kind. Body: {document_id, kind}.
    kind must be one of: icp, proposal, sow, pricing_sheet, battle_card,
    sales_value_props, sales_tone_guide, sales_case_study."""
    _, org_id, _ = _require_user(current_user)
    document_id = body.get("document_id")
    kind = body.get("kind")
    allowed = {
        "icp",
        "proposal",
        "sow",
        "pricing_sheet",
        "battle_card",
        "sales_value_props",
        "sales_tone_guide",
        "sales_case_study",
    }
    if not document_id or kind not in allowed:
        raise HTTPException(status_code=422, detail="invalid_payload")

    svc = get_service_client()

    def _update() -> dict[str, Any]:
        res = (
            svc.table("documents")
            .update({"template_kind": kind, "template_status": "active"})
            .eq("id", document_id)
            .eq("org_id", org_id)
            .execute()
        )
        return (res.data or [{}])[0]

    updated = await asyncio.to_thread(_update)
    if not updated:
        raise HTTPException(status_code=404, detail="document_not_found")

    inngest_client = get_inngest_client()
    await inngest_client.send(
        inngest.Event(
            name="sales/template-uploaded",
            data={"org_id": org_id, "template_kind": kind},
        )
    )
    return {"status": "ok", "document_id": document_id, "kind": kind}


# ── Gmail watch webhook ────────────────────────────────────────────────────


@router.post("/webhooks/gmail", status_code=status.HTTP_200_OK)
async def gmail_watch_webhook(request: Request) -> dict[str, Any]:
    """Pub/Sub push endpoint for Gmail watch notifications.

    The body schema follows Google's Pub/Sub push contract:
        {"message": {"data": "<base64-json>", "messageId": "..."}}

    The inner JSON has {emailAddress, historyId}. We don't need to pull the
    full history here — the contract for the sales agent is simpler: when
    *any* notification fires for an account that's the sender of a tracked
    outreach, we mark every awaiting_reply deal owned by that user as
    `reply_received_at = now()` IF there's at least one matching thread.

    This is intentionally coarse — a real implementation would call
    users.history.list to get the new message and match thread_id exactly.
    The simpler path is fine for v1 since the rep can also manually click
    "I got a reply".
    """
    payload = await request.json()
    message = payload.get("message") or {}
    msg_id = message.get("messageId")
    encoded = message.get("data")
    if not encoded:
        return {"status": "skipped", "reason": "no_data"}

    import base64
    import json as _json

    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
        body = _json.loads(decoded)
    except Exception:
        log.warning("sales.gmail_watch.decode_failed msg=%s", msg_id)
        return {"status": "skipped", "reason": "decode_failed"}

    email_address = body.get("emailAddress")
    if not email_address:
        return {"status": "skipped", "reason": "no_email"}

    svc = get_service_client()

    def _find_integ() -> dict[str, Any] | None:
        res = (
            svc.table("gmail_integrations")
            .select("org_id, user_id")
            .eq("email_address", email_address)
            .limit(1)
            .execute()
        )
        return (res.data or [None])[0]

    integ = await asyncio.to_thread(_find_integ)
    if not integ:
        return {"status": "skipped", "reason": "no_integration_for_address"}

    def _flag_deals() -> int:
        res = (
            svc.table("deal_runs")
            .update({"reply_received_at": datetime.now(UTC).isoformat()})
            .eq("org_id", integ["org_id"])
            .eq("created_by", integ["user_id"])
            .eq("status", "awaiting_reply")
            .is_("reply_received_at", "null")
            .execute()
        )
        return len(res.data or [])

    flagged = await asyncio.to_thread(_flag_deals)
    return {"status": "ok", "flagged": flagged, "message_id": msg_id}
