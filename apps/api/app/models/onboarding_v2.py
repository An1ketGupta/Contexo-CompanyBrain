"""Pydantic models for Onboarding v2 routers."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator


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


class EsignSignerRead(BaseModel):
    """Per-signer status within a signing envelope."""
    role: str
    name: str
    status: str  # "pending" | "completed"
    completed_at: datetime | None = None


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
    # Per-signer statuses from the signing envelope — lets the UI show
    # individual progress (e.g. "HR signed ✓ / Candidate pending").
    esign_signers: list[EsignSignerRead] = Field(default_factory=list)
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


class BlockingFieldRead(BaseModel):
    """One template field HR has to answer before the run can continue.

    Derived from the validation report of the last failed generation, enriched
    from `doc_template_variables` so the form can label and type the input
    rather than showing a raw `internal_name`.
    """

    internal_name: str
    label: str
    data_type: str = "text"
    description: str | None = None
    example_value: str | None = None
    code: str
    message: str
    # Prefilled with what HR typed before, or with the value that failed a
    # format check — correcting a wrong date beats retyping it from scratch.
    value: str = ""


class BlockingFieldsResponse(BaseModel):
    """Empty `fields` means the block is not something a value can fix (no
    template uploaded, no fields confirmed) and the UI should keep pointing at
    the template library instead."""

    document_kind: str | None = None
    template_name: str | None = None
    generated_document_id: str | None = None
    fields: list[BlockingFieldRead] = Field(default_factory=list)


class RunFieldValuesRequest(BaseModel):
    """HR's answers, keyed by `doc_template_variables.internal_name`.

    A blank value clears the stored one rather than writing "", so the field
    goes back to resolving from the candidate profile.
    """

    values: dict[str, str] = Field(default_factory=dict)

    @field_validator("values")
    @classmethod
    def _bounded(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 60:
            raise ValueError("Too many fields in one save.")
        for name, value in v.items():
            if not name or len(name) > 80:
                raise ValueError(f"'{name}' is not a valid field name.")
            if len(value) > 2000:
                raise ValueError(f"The value for '{name}' is too long.")
        return v


class RunFieldValuesResponse(BaseModel):
    status: str
    saved: int
    resumed: bool


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


class TemplateFieldSlot(BaseModel):
    """One persisted fill-point in a `fill_strategy='slots'` template.

    `paragraph_index`/`start_offset`/`end_offset` locate the fill-point in the
    canonical paragraph enumeration (see `docx_positions`); the renderer
    substitutes by that position, never by re-finding `blank_text` as a
    substring. The UI must round-trip them unchanged, exactly as it already does
    with `TemplateTextBlock.index`. Two more fields drive the review UI:

      * `action` — `replace_span` overwrites a marked blank; `insert_after_label`
        appends after a label like `Signature:` that has nothing after it;
        `insert_empty_cell` fills an empty table cell beside a labelled one. The
        last two have `start_offset == end_offset` (an insertion point) and an
        empty `blank_text`, so the UI must render them by their surrounding
        context rather than by the (nonexistent) blank text.
      * `status` — `proposed` slots are NOT rendered. HR confirms or rejects each
        one, which is the human-in-the-loop step the old auto-apply flow skipped.
    """

    id: str
    action: str = Field(default="replace_span")
    status: str = Field(default="proposed")
    source: str = Field(default="ai")
    variable: str | None = None
    confidence: str = Field(default="medium")
    blank_text: str = Field(default="", max_length=500)
    context_before: str = Field(default="", max_length=200)
    context_after: str = Field(default="", max_length=200)
    paragraph_index: int = Field(..., ge=0)
    paragraph_kind: str = Field(default="body")
    start_offset: int = Field(..., ge=0)
    end_offset: int = Field(..., ge=0)


class TemplateAnalyzeResponse(BaseModel):
    """Returned by POST /templates/{id}/analyze.

    `fill_strategy` tells the UI which flow to run:

      * `slots` — fill-points are persisted as `slots` below; HR confirms each
        one and nothing is written into the customer's DOCX. This is what an
        ordinary HR document analyzes to.
      * `jinja` — the document is a genuine hand-authored template (every
        `{{ }}` tag parses); it renders via docxtpl and needs no mapping, so
        `slots` comes back empty.

    `has_placeholders` means "this document has `{{ }}` tags", not "these tags
    are valid" — that distinction is what `jinja_errors` carries. A malformed tag
    (e.g. `{{ Signing Date }}`) yields `fill_strategy='slots'` PLUS a populated
    `jinja_errors`, because a tag that can't parse must never be trusted as a
    placeholder.
    """

    document_id: str
    template_kind: str
    has_placeholders: bool
    fill_strategy: str = "slots"
    slots: list[TemplateFieldSlot] = Field(default_factory=list)
    text_preview: str = ""
    available_variables: list[dict[str, str]] = Field(default_factory=list)
    warning: str | None = None
    # Plain-English problems found in hand-typed `{{ }}` tags, surfaced at
    # analyze time instead of as an opaque parser error mid-render.
    jinja_errors: list[str] = Field(default_factory=list)
    # Valid tags naming a variable we don't supply — these WOULD raise
    # TemplateVariableError during generation, so HR sees them now.
    unknown_variables: list[str] = Field(default_factory=list)


class TemplateSlotDecisionRequest(BaseModel):
    """HR's verdict on one proposed fill-point.

    `variable` is required when confirming and ignored when rejecting — a
    confirmed slot with no variable would render as an empty string in a
    legally-binding document, which the DB also refuses.
    """

    status: str = Field(..., pattern=r"^(confirmed|rejected)$")
    variable: str | None = Field(default=None, max_length=80)


class TemplateSlotCreateRequest(BaseModel):
    """A fill-point HR located by hand because detection missed it.

    The recall safety net: no heuristic will find every blank in every
    customer's document, so HR must always be able to say "there is also a field
    here". Created already-confirmed, since HR pointing at it *is* the
    confirmation.
    """

    paragraph_index: int = Field(..., ge=0)
    start_offset: int = Field(..., ge=0)
    end_offset: int = Field(..., ge=0)
    action: str = Field(
        default="replace_span",
        pattern=r"^(replace_span|insert_after_label|insert_empty_cell)$",
    )
    variable: str = Field(..., min_length=1, max_length=80)


class TemplateSlotsResponse(BaseModel):
    document_id: str
    template_kind: str
    fill_strategy: str | None = None
    slots: list[TemplateFieldSlot] = Field(default_factory=list)
    available_variables: list[dict[str, str]] = Field(default_factory=list)
    pending_count: int = 0


class TemplateRenderPreviewResponse(BaseModel):
    """Returned by POST /templates/{id}/render-preview.

    Generating a preview does NOT mutate the stored template — it renders a
    throwaway copy from the confirmed slots.

    `unfilled_warnings` is the post-render safety net: spots in the OUTPUT that
    still look unfilled (`________`, a bare `Signature:`). Non-fatal by design —
    a document with one missed field is still useful to HR, but they should hear
    about it before a candidate does.
    """

    document_id: str
    template_kind: str
    preview_url: str | None = None
    filled_count: int = 0
    pending_count: int = 0
    unfilled_warnings: list[str] = Field(default_factory=list)
    preview_error: str | None = None


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


