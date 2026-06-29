"""Pydantic models for the Sales Agent routers.

Surfaces the request/response shapes for the deal-pipeline endpoints. Aligned
1:1 with the deal_runs / deal_documents / deal_events / deal_approvals tables
from migration 076.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator


# ── Intake / lifecycle requests ──────────────────────────────────────────────


class LeadIntakeRequest(BaseModel):
    """POST /sales/deals/runs — create a new opportunity."""

    company_name: str = Field(..., min_length=1, max_length=200)
    company_website: str | None = Field(default=None, max_length=500)
    company_industry: str | None = Field(default=None, max_length=200)
    contact_name: str | None = Field(default=None, max_length=200)
    contact_title: str | None = Field(default=None, max_length=200)
    contact_email: EmailStr | None = None
    contact_linkedin_url: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=4000)
    deal_value_amount: float | None = Field(default=None, ge=0)
    deal_value_currency: str = Field(default="USD", max_length=8)

    @field_validator("company_website")
    @classmethod
    def _normalize_website(cls, v: str | None) -> str | None:
        if not v:
            return None
        v = v.strip()
        if v and not v.startswith(("http://", "https://")):
            v = f"https://{v}"
        return v


class EditOutreachRequest(BaseModel):
    """PATCH /sales/deals/runs/{id}/outreach — rep inline edit."""

    subject: str | None = Field(default=None, max_length=500)
    email_body: str | None = Field(default=None, max_length=10000)
    linkedin_body: str | None = Field(default=None, max_length=2000)


class MeetingBookedRequest(BaseModel):
    """POST /sales/deals/runs/{id}/meeting-booked."""

    meeting_at: datetime
    notes: str | None = Field(default=None, max_length=2000)


class TranscriptRequest(BaseModel):
    """POST /sales/deals/runs/{id}/call-transcript — rep submits notes/transcript."""

    transcript: str = Field(..., min_length=1, max_length=120_000)


class NextStepRequest(BaseModel):
    """POST /sales/deals/runs/{id}/next-step — rep's branching decision after call summary."""

    decision: Literal["schedule_another_call", "proceed_to_proposal"]
    notes: str | None = Field(default=None, max_length=2000)


class CloseDealRequest(BaseModel):
    """POST /sales/deals/runs/{id}/close — rep marks deal won/lost."""

    outcome: Literal["won", "lost"]
    reason: str = Field(..., min_length=1, max_length=2000)
    notes: str | None = Field(default=None, max_length=4000)
    final_deal_value: float | None = Field(default=None, ge=0)


class ReplyReceivedRequest(BaseModel):
    """POST /sales/deals/runs/{id}/reply-received — manual mark."""

    note: str | None = Field(default=None, max_length=2000)


# ── Read models ──────────────────────────────────────────────────────────────


class DealRunRead(BaseModel):
    """Slim read model for list views."""

    id: str
    org_id: str
    created_by: str
    company_name: str
    company_website: str | None = None
    company_industry: str | None = None
    contact_name: str | None = None
    contact_title: str | None = None
    contact_email: str | None = None
    status: str
    blocked_reason: str | None = None
    blocked_template_kind: str | None = None
    icp_score: int | None = None
    icp_rationale: str | None = None
    outreach_sent_at: datetime | None = None
    reply_received_at: datetime | None = None
    meeting_at: datetime | None = None
    meeting_count: int = 0
    proposal_sent_at: datetime | None = None
    closed_at: datetime | None = None
    close_outcome: str | None = None
    deal_value_amount: float | None = None
    deal_value_currency: str | None = None
    followup_count: int = 0
    checkin_count: int = 0
    created_at: datetime
    updated_at: datetime


class DealEventRead(BaseModel):
    id: str
    actor_kind: str
    actor_user_id: str | None = None
    event_type: str
    from_status: str | None = None
    to_status: str | None = None
    message: str | None = None
    payload: dict[str, Any] | None = None
    created_at: datetime


class DealResearchRead(BaseModel):
    id: str
    source: str
    company_summary: str | None = None
    headcount_estimate: str | None = None
    industry: str | None = None
    tech_stack_hints: list[str] | None = None
    pain_point_hypothesis: str | None = None
    competitors_detected: list[str] | None = None
    similar_past_deals: list[dict[str, Any]] | None = None
    raw_sources: list[dict[str, Any]] | None = None
    has_usable_signal: bool = False
    created_at: datetime


class DealDocumentRead(BaseModel):
    id: str
    kind: str
    storage_path: str
    pdf_storage_path: str | None = None
    signed_url: str | None = None
    pdf_signed_url: str | None = None
    source: str
    revision: int
    sign_status: str
    created_at: datetime


class DealApprovalRead(BaseModel):
    id: str
    gate_type: str
    gate_variant: str | None = None
    draft_subject: str | None = None
    draft_content: str | None = None
    draft_document_id: str | None = None
    edited_subject: str | None = None
    edited_content: str | None = None
    edited_document_id: str | None = None
    revision: int
    status: str
    decided_by: str | None = None
    decided_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DealRunDetailRead(DealRunRead):
    """Detail view: deal + research + documents + events + pending approval."""

    outreach_subject: str | None = None
    outreach_email_body: str | None = None
    outreach_linkedin_body: str | None = None
    outreach_thread_id: str | None = None
    call_transcript: str | None = None
    call_summary: str | None = None
    bant_json: dict[str, Any] | None = None
    objections_json: list[Any] | None = None
    next_steps_json: list[Any] | None = None
    recommended_stage: str | None = None
    prep_brief: dict[str, Any] | None = None
    proposal_draft_path: str | None = None
    proposal_edited_path: str | None = None
    close_reason: str | None = None
    close_notes: str | None = None
    latest_research: DealResearchRead | None = None
    documents: list[DealDocumentRead] = Field(default_factory=list)
    events: list[DealEventRead] = Field(default_factory=list)
    pending_approval: DealApprovalRead | None = None
