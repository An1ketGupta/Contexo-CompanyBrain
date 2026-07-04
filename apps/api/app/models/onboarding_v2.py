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
    # References used to be required at run-start (HR entered 2+ in the dialog).
    # Now the candidate submits them via a public form linked from their LOI
    # email, so the field is optional and may stay empty. HR can still seed
    # them up-front if they happen to know them (passed through to BGV after
    # the candidate is sent the form).
    references: list[BgvReferenceInput] = Field(default_factory=list, max_length=4)


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
    # HR's edited copy of the agent-rendered .docx (only set when HR
    # downloaded, tweaked, and re-uploaded during the loi_pending_hr_review
    # step). The PDF derived from that edit is at hr_edited_pdf_path.
    hr_edited_storage_path: str | None = None
    hr_edited_pdf_path: str | None = None
    hr_edited_at: datetime | None = None
    hr_edit_revision: int = 0
    esign_envelope_id: str | None = None
    esign_status: str | None = None
    esign_signing_url: str | None = None
    esign_completed_at: datetime | None = None
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
    # New: LOI review & candidate-references-form tracking.
    loi_approved_for_signing_at: datetime | None = None
    loi_draft_edited_at: datetime | None = None
    loi_draft_revision: int = 0
    references_form_expires_at: datetime | None = None
    references_submitted_at: datetime | None = None
    references_reminder_count: int = 0
    references_last_reminder_at: datetime | None = None
    # Note: references_form_token is intentionally NOT exposed in this read
    # model — it's the candidate's auth credential. HR doesn't need it; we
    # surface the form URL on the detail page through a separate field below.
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


class LoiApproveDraftResponse(BaseModel):
    """Returned by POST /runs/{id}/loi/approve-draft."""
    status: str
    document_id: str


class LoiReplaceDraftResponse(BaseModel):
    """Returned by POST /runs/{id}/loi/replace-draft. The preview_url points
    to a freshly-rendered PDF of HR's edited .docx so the UI can refresh the
    inline preview immediately after upload."""
    status: str
    revision: int
    preview_url: str | None = None


class CandidateReferencesPrefill(BaseModel):
    """Returned by GET /onboarding/public/references/{token}. Tells the
    candidate which company is asking, their own name, and how many
    references they must submit."""
    candidate_name: str
    company_name: str
    role_title: str
    required_count: int
    expires_at: datetime
    already_submitted: bool


class CandidateReferenceItem(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=40)
    relationship: str | None = Field(default=None, max_length=200)


class CandidateReferencesSubmit(BaseModel):
    references: list[CandidateReferenceItem] = Field(..., min_length=1, max_length=4)


class HrReferencesOverrideRequest(BaseModel):
    """HR-driven override: if the candidate hasn't submitted their references
    form, HR can post refs directly. Same shape as the candidate submission."""
    references: list[CandidateReferenceItem] = Field(..., min_length=1, max_length=4)


class TagTemplateRequest(BaseModel):
    document_id: UUID
    template_kind: str = Field(..., pattern=r"^(loi|appointment_letter|nda|induction)$")


class ImportTemplateFromDriveRequest(BaseModel):
    """Body for POST /onboarding/templates/import-from-drive.

    `file_id` + `mime_type` are returned by the Google Picker to the browser.
    `file_name` is used as the document's display name; we sanitize before
    writing it to storage so it can't escape the per-org prefix.
    """

    file_id: str = Field(..., min_length=1, max_length=120)
    file_name: str = Field(..., min_length=1, max_length=255)
    mime_type: str = Field(..., min_length=1, max_length=160)
    template_kind: str = Field(..., pattern=r"^(loi|appointment_letter|nda|induction)$")


# ── Template analyzer (AI-assisted placeholder conversion) ──────────────────


class TemplateMappingItem(BaseModel):
    """One blank → variable mapping. The analyzer proposes these and HR
    edits/confirms each one before they are applied to the DOCX.

    `paragraph_index`/`start_offset`/`end_offset` locate the exact blank span
    in the canonical paragraph enumeration (see `_canonical_paragraphs`); the
    apply step substitutes by that position, not by re-finding `blank_text`.
    `blank_text` is retained for HR-readability and as a drift safety-check at
    apply time — if the text at that offset no longer equals `blank_text`
    (e.g. the block was edited in between) the mapping is skipped rather than
    corrupting the document. The UI must round-trip the offset fields
    unchanged, exactly as it already does with `TemplateTextBlock.index`."""

    blank_text: str = Field(..., min_length=1, max_length=500)
    variable: str = Field(..., min_length=1, max_length=80)
    context_before: str = Field(default="", max_length=200)
    context_after: str = Field(default="", max_length=200)
    confidence: str = Field(default="medium", pattern=r"^(high|medium|low)$")
    paragraph_index: int = Field(..., ge=0)
    start_offset: int = Field(..., ge=0)
    end_offset: int = Field(..., ge=0)


class TemplateAnalyzeResponse(BaseModel):
    """Returned by POST /templates/{id}/analyze.

    `has_placeholders` short-circuits the UI — if the DOCX already has
    `{{ var }}` placeholders, the mapper modal doesn't open.

    `mappings` is the AI's best guess at blank → variable assignments. HR
    reviews, edits, and confirms before they are applied.

    `text_preview` is the first ~1500 chars of extracted text. The UI uses
    it as a "what we read from the document" sanity check.

    `available_variables` is the vocabulary the UI populates dropdowns with.
    """

    document_id: str
    template_kind: str
    has_placeholders: bool
    mappings: list[TemplateMappingItem] = Field(default_factory=list)
    text_preview: str = ""
    available_variables: list[dict[str, str]] = Field(default_factory=list)
    warning: str | None = None


class TemplateApplyMappingsRequest(BaseModel):
    """HR's confirmed mappings — written back to the DOCX as `{{ var }}`."""

    mappings: list[TemplateMappingItem] = Field(..., min_length=1, max_length=200)


class TemplateApplyMappingsResponse(BaseModel):
    document_id: str
    template_kind: str
    applied_count: int
    # Mappings dropped at apply time (stale offset / drifted text). 0 in the
    # normal analyze→apply flow; >0 only if the document changed in between.
    skipped_count: int = 0
    preview_url: str | None = None


class TemplateTextBlock(BaseModel):
    """One editable line of a template — a paragraph, table cell, header, or
    footer. `index` is the stable position key the write-back step uses to
    locate the paragraph again; the UI must round-trip it unchanged."""

    index: int = Field(..., ge=0)
    text: str = Field(default="", max_length=20000)
    kind: str = Field(default="body")


class TemplateBlocksResponse(BaseModel):
    """Returned by GET /templates/{id}/blocks — the current DOCX rendered as
    an ordered list of editable text blocks (placeholders visible)."""

    document_id: str
    template_kind: str
    blocks: list[TemplateTextBlock] = Field(default_factory=list)


class TemplateEditTextRequest(BaseModel):
    """HR's edited text blocks — written back into the DOCX paragraph runs in
    place, preserving formatting."""

    edits: list[TemplateTextBlock] = Field(..., max_length=5000)


class TemplateEditTextResponse(BaseModel):
    document_id: str
    template_kind: str
    changed_count: int
    preview_url: str | None = None
    preview_error: str | None = None


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


