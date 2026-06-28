"""Pydantic models for Onboarding v2 routers."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class BgvReferenceInput(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=40)
    relationship: str | None = Field(default=None, max_length=200)


class StartOnboardingRequest(BaseModel):
    candidate_id: UUID | None = None
    requisition_id: UUID | None = None
    candidate_name: str = Field(..., min_length=1, max_length=200)
    candidate_email: EmailStr
    candidate_phone: str | None = Field(default=None, max_length=40)
    role_title: str = Field(..., min_length=1, max_length=200)
    designation: str | None = Field(default=None, max_length=200)
    ctc_amount: float | None = Field(default=None, ge=0)
    ctc_currency: str = Field(default="INR", max_length=8)
    ctc_breakdown: dict[str, Any] | None = None
    start_date: date
    work_location: str | None = Field(default=None, max_length=200)
    probation_period_months: int | None = Field(default=None, ge=0, le=24)
    reporting_manager_name: str | None = Field(default=None, max_length=200)
    reporting_manager_email: EmailStr | None = None
    reporting_manager_user_id: UUID | None = None
    references: list[BgvReferenceInput] = Field(..., min_length=2, max_length=4)


class BgvReferenceRead(BaseModel):
    id: str
    reference_name: str
    reference_email: str
    reference_phone: str | None = None
    relationship: str | None = None
    status: str
    email_sent_at: datetime | None = None
    opened_at: datetime | None = None
    submitted_at: datetime | None = None
    reminder_count: int = 0
    response_worked_together_months: int | None = None
    response_would_recommend: bool | None = None
    response_strengths: str | None = None
    response_concerns: str | None = None
    response_role_description: str | None = None


class OnboardingDocumentRead(BaseModel):
    id: str
    kind: str
    storage_path: str
    signed_url: str | None = None
    sign_status: str
    signed_pdf_path: str | None = None
    signed_uploaded_at: datetime | None = None
    file_bytes: int | None = None
    docusign_envelope_id: str | None = None
    docusign_status: str | None = None
    docusign_signing_url: str | None = None
    docusign_completed_at: datetime | None = None
    used_default_template: bool = False
    created_at: datetime
    updated_at: datetime


class OnboardingEventRead(BaseModel):
    id: str
    actor_kind: str
    event_type: str
    message: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime


class OnboardingRunRead(BaseModel):
    id: str
    org_id: str
    candidate_id: str | None = None
    requisition_id: str | None = None
    candidate_name: str
    candidate_email: str
    candidate_phone: str | None = None
    role_title: str
    designation: str | None = None
    ctc_amount: float | None = None
    ctc_currency: str | None = None
    ctc_breakdown: dict[str, Any] | None = None
    start_date: date
    work_location: str | None = None
    probation_period_months: int | None = None
    reporting_manager_name: str | None = None
    reporting_manager_email: str | None = None
    status: str
    blocked_reason: str | None = None
    blocked_template_kind: str | None = None
    current_step: str | None = None
    agent_run_id: str | None = None
    triggered_by_user_id: str | None = None
    loi_sent_to_hr_at: datetime | None = None
    loi_signed_at: datetime | None = None
    loi_sent_to_candidate_at: datetime | None = None
    bgv_sent_at: datetime | None = None
    bgv_completed_at: datetime | None = None
    appointment_sent_at: datetime | None = None
    policies_assigned_at: datetime | None = None
    policies_acknowledged_at: datetime | None = None
    induction_sent_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class OnboardingRunDetailRead(OnboardingRunRead):
    references: list[BgvReferenceRead] = Field(default_factory=list)
    documents: list[OnboardingDocumentRead] = Field(default_factory=list)
    events: list[OnboardingEventRead] = Field(default_factory=list)


class BgvFormPrefill(BaseModel):
    """Returned by the public BGV GET so the reference can see context before
    filling the form. We deliberately don't leak the candidate's PII beyond
    name + role."""
    reference_name: str
    candidate_name: str
    candidate_role: str
    company_name: str
    expires_at: datetime
    already_submitted: bool


class BgvFormSubmit(BaseModel):
    worked_together_months: int = Field(..., ge=0, le=600)
    would_recommend: bool
    strengths: str = Field(default="", max_length=4000)
    concerns: str = Field(default="", max_length=4000)
    role_description: str = Field(default="", max_length=4000)


class TagTemplateRequest(BaseModel):
    document_id: UUID
    template_kind: str = Field(..., pattern=r"^(loi|appointment_letter|nda|induction)$")


# ── Sources: published jobs + their pipeline candidates for the onboarding
# entry point. Lets HR pick a candidate from the recruiting pipeline instead
# of retyping identity fields.
class SourceCandidate(BaseModel):
    id: str
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    current_company: str | None = None
    current_title: str | None = None
    stage: str | None = None
    resume_url: str | None = None
    candidate_url: str | None = None
    applied_at: datetime | None = None
    ats_platform: str | None = None
    # null when no onboarding run has been started for this candidate yet,
    # or all prior runs are terminal (cancelled / failed) — i.e. the row is
    # eligible to be onboarded again.
    onboarding_run_id: str | None = None
    onboarding_status: str | None = None
    # Heuristic flag: stage text suggests the candidate is hired (matches
    # 'hired', 'offer accepted', 'joined'). HR can still onboard anyone.
    looks_hired: bool = False


class SourceJob(BaseModel):
    id: str
    role_request: str
    location: str | None = None
    department: str | None = None
    seniority_level: str | None = None
    published_at: datetime | None = None
    candidates_last_synced_at: datetime | None = None
    notion_tracker_url: str | None = None
    candidates: list[SourceCandidate] = Field(default_factory=list)


class SourcesResponse(BaseModel):
    jobs: list[SourceJob] = Field(default_factory=list)
