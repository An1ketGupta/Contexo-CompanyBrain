"""Request/response models for the document generation pipeline."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.services.documents.constants import (
    DATA_TYPES,
    REVIEW_STATUSES,
    SLOT_ACTIONS,
    TEMPLATE_STATUSES,
)

# ── Document types ─────────────────────────────────────────────────────────


class DocumentTypeRead(BaseModel):
    id: str
    key: str
    label: str
    description: str | None = None
    is_system: bool = False


class DocumentTypeCreate(BaseModel):
    key: str = Field(min_length=2, max_length=60, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)


# ── Templates ──────────────────────────────────────────────────────────────


class TemplateRead(BaseModel):
    id: str
    name: str
    description: str | None = None
    status: str
    is_default: bool = False
    document_type_id: str
    document_type_key: str | None = None
    document_type_label: str | None = None
    current_version_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class TemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    status: str | None = None
    document_type_id: str | None = None

    @field_validator("status")
    @classmethod
    def _known_status(cls, v: str | None) -> str | None:
        if v is not None and v not in TEMPLATE_STATUSES:
            raise ValueError(f"status must be one of {TEMPLATE_STATUSES}")
        return v


class TemplateVersionRead(BaseModel):
    id: str
    version_no: int
    original_filename: str
    mime_type: str
    file_bytes: int | None = None
    file_sha256: str
    analysis_status: str
    analysis_error: str | None = None
    analyzed_at: str | None = None
    detected_type_id: str | None = None
    detected_type_confidence: float | None = None
    created_at: str | None = None


# ── Schema ─────────────────────────────────────────────────────────────────


class VariableRead(BaseModel):
    id: str
    internal_name: str
    display_name: str
    description: str | None = None
    data_type: str
    is_required: bool
    default_value: str | None = None
    validation_rules: dict[str, Any] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)
    example_value: str | None = None
    confidence: float | None = None
    status: str
    source: str


class VariableCreate(BaseModel):
    internal_name: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=80)
    display_name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    data_type: str = "text"
    is_required: bool = True
    default_value: str | None = Field(default=None, max_length=500)
    validation_rules: dict[str, Any] = Field(default_factory=dict)

    @field_validator("data_type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v not in DATA_TYPES:
            raise ValueError(f"data_type must be one of {DATA_TYPES}")
        return v


class VariableUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    data_type: str | None = None
    is_required: bool | None = None
    default_value: str | None = Field(default=None, max_length=500)
    validation_rules: dict[str, Any] | None = None
    aliases: list[str] | None = None
    status: str | None = None

    @field_validator("data_type")
    @classmethod
    def _known_type(cls, v: str | None) -> str | None:
        if v is not None and v not in DATA_TYPES:
            raise ValueError(f"data_type must be one of {DATA_TYPES}")
        return v

    @field_validator("status")
    @classmethod
    def _known_status(cls, v: str | None) -> str | None:
        if v is not None and v not in REVIEW_STATUSES:
            raise ValueError(f"status must be one of {REVIEW_STATUSES}")
        return v


class SlotRead(BaseModel):
    id: str
    variable_id: str | None = None
    variable: str | None = None
    variable_label: str | None = None
    variable_type: str | None = None
    paragraph_index: int
    paragraph_kind: str
    start_offset: int
    end_offset: int
    action: str
    original_text: str = ""
    context_before: str = ""
    context_after: str = ""
    confidence: float | None = None
    status: str
    source: str


class SlotUpdate(BaseModel):
    variable_id: str | None = None
    status: str | None = None

    @field_validator("status")
    @classmethod
    def _known_status(cls, v: str | None) -> str | None:
        if v is not None and v not in REVIEW_STATUSES:
            raise ValueError(f"status must be one of {REVIEW_STATUSES}")
        return v


class SlotCreate(BaseModel):
    variable_id: str | None = None
    paragraph_index: int = Field(ge=0)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    action: str = "replace_span"

    @field_validator("action")
    @classmethod
    def _known_action(cls, v: str) -> str:
        if v not in SLOT_ACTIONS:
            raise ValueError(f"action must be one of {SLOT_ACTIONS}")
        return v


class SchemaRead(BaseModel):
    """Everything the template builder needs for one version."""

    version: TemplateVersionRead
    variables: list[VariableRead]
    slots: list[SlotRead]
    confirm_threshold: float


# ── Analysis ───────────────────────────────────────────────────────────────


class AnalyzeResponse(BaseModel):
    status: str
    detected_type: str | None = None
    detected_type_confidence: float | None = None
    candidates_found: int = 0
    variables_created: int = 0
    variables_refreshed: int = 0
    slots_created: int = 0
    slots_refreshed: int = 0
    truncated: bool = False
    error: str | None = None


# ── Preview ────────────────────────────────────────────────────────────────


class PreviewRequest(BaseModel):
    """Optional overrides; anything omitted falls back to the variable's
    example value, then a type-appropriate placeholder."""

    values: dict[str, Any] = Field(default_factory=dict)


class PreviewResponse(BaseModel):
    docx_url: str | None = None
    pdf_url: str | None = None
    warnings: list[str] = Field(default_factory=list)
    used_values: dict[str, Any] = Field(default_factory=dict)


# ── Generation ─────────────────────────────────────────────────────────────


class GenerateRequest(BaseModel):
    """Either `template_id` (this exact template) or `type_key` (the org's
    default for that type). The candidate comes from whichever id is supplied."""

    template_id: str | None = None
    type_key: str | None = None
    onboarding_run_id: str | None = None
    candidate_id: str | None = None
    requisition_id: str | None = None
    # HR-entered corrections, keyed by dotted profile path
    # (e.g. "manager.full_name"). Applied last — a human correcting a value is
    # the most authoritative source there is.
    overrides: dict[str, Any] = Field(default_factory=dict)


class GeneratedFileRead(BaseModel):
    format: str
    url: str | None = None


class GeneratedDocumentRead(BaseModel):
    id: str
    status: str
    template_id: str
    template_name: str | None = None
    version_id: str
    generation_no: int
    onboarding_run_id: str | None = None
    validation_report: dict[str, Any] = Field(default_factory=dict)
    context_snapshot: dict[str, Any] = Field(default_factory=dict)
    candidate_snapshot: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    files: list[GeneratedFileRead] = Field(default_factory=list)
    generated_at: str | None = None
    approved_at: str | None = None
    rejected_at: str | None = None
    rejection_reason: str | None = None
    created_at: str | None = None


class GenerateResponse(BaseModel):
    outcome: str
    document: GeneratedDocumentRead | None = None
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class RejectRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class TemplateReadiness(BaseModel):
    """Whether one document type can be generated right now.

    `reason` distinguishes the two ways it can fail: `no_template` (nothing
    uploaded and made default) and `no_confirmed_fields` (a template exists but
    nobody has confirmed what fills it). Both block generation; only the first
    was visible before.
    """

    type_key: str
    label: str
    ready: bool
    reason: str | None = None
    template_id: str | None = None
    template_name: str | None = None
    version_id: str | None = None
    confirmed_field_count: int = 0


class TemplateParagraph(BaseModel):
    """One addressable paragraph of a template.

    `index` is the position in the canonical enumeration the detector and the
    renderer both use, so an offset chosen against `text` here resolves to the
    same characters at generation time.
    """

    index: int
    kind: str
    text: str


class TemplateTextResponse(BaseModel):
    paragraphs: list[TemplateParagraph]
