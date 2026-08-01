"""Pydantic models for the onboarding step catalog router."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# What a candidate may upload. Kept short and boring on purpose: these end up
# in an `accept` attribute and in server-side validation, and a long tail of
# exotic formats is a support burden, not a feature.
ALLOWED_UPLOAD_FORMATS = ("pdf", "jpg", "jpeg", "png", "doc", "docx")


class CollectItemInput(BaseModel):
    """One document on a collection checklist."""

    # Present when editing an existing item; absent when adding a new one.
    # Preserving it is what keeps a rename from orphaning submissions.
    item_key: str | None = Field(default=None, max_length=64)
    label: str = Field(..., min_length=1, max_length=200)
    help_text: str | None = Field(default=None, max_length=500)
    required: bool = True
    accepted_formats: list[str] = Field(
        default_factory=lambda: ["pdf", "jpg", "jpeg", "png"],
        max_length=len(ALLOWED_UPLOAD_FORMATS),
    )


class CollectItemRead(BaseModel):
    item_key: str
    label: str
    help_text: str | None = None
    required: bool = True
    accepted_formats: list[str] = Field(default_factory=list)


class StepRead(BaseModel):
    step_key: str
    kind: str
    label: str
    description: str | None = None
    document_type_key: str | None = None
    bundle_key: str | None = None
    bundle_label: str | None = None
    position: int
    enabled: bool
    signer_roles: list[str] = Field(default_factory=list)
    locked: bool = False
    items: list[CollectItemRead] = Field(default_factory=list)


class CatalogRead(BaseModel):
    steps: list[StepRead]
    # False until the org saves a choice — drives the first-run setup screen,
    # the same way the four-boolean endpoint it replaces did.
    configured: bool


class CreateCollectStepRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=200)
    # Position expressed the way an org thinks about it: "ask for these right
    # after the letter of intent". None places it first among unlocked steps.
    after_step_key: str | None = Field(default=None, max_length=64)
    items: list[CollectItemInput] = Field(..., min_length=1, max_length=40)


class UpdateStepRequest(BaseModel):
    enabled: bool | None = None
    label: str | None = Field(default=None, min_length=1, max_length=200)


class ReplaceItemsRequest(BaseModel):
    items: list[CollectItemInput] = Field(..., min_length=1, max_length=40)


class SubmissionRead(BaseModel):
    """A document the candidate filed, as HR sees it."""

    id: str
    run_step_id: str
    item_key: str
    label: str
    original_filename: str | None = None
    file_bytes: int | None = None
    submitted_at: str | None = None
    review_status: str
    review_note: str | None = None
    signed_url: str | None = None


class ReviewSubmissionRequest(BaseModel):
    # Deliberately not a pipeline gate: the run advanced when the document was
    # submitted. This records a judgement and, on reject, re-opens the ask.
    review_status: str = Field(..., pattern="^(approved|rejected)$")
    review_note: str | None = Field(default=None, max_length=1000)


class CandidateItemRead(BaseModel):
    item_key: str
    label: str
    help_text: str | None = None
    required: bool
    accepted_formats: list[str] = Field(default_factory=list)
    submitted: bool = False
    original_filename: str | None = None
    submitted_at: str | None = None
    review_status: str | None = None
    review_note: str | None = None


class CandidateStepRead(BaseModel):
    step_key: str
    label: str
    bundle_key: str | None = None
    bundle_label: str | None = None
    status: str
    items: list[CandidateItemRead] = Field(default_factory=list)


class CandidateOnboardingRead(BaseModel):
    run_id: str
    candidate_name: str
    company_name: str
    role_title: str
    steps: list[CandidateStepRead] = Field(default_factory=list)


def catalog_step_to_read(
    step: dict[str, Any], items: list[dict[str, Any]] | None = None
) -> StepRead:
    return StepRead(
        step_key=step["step_key"],
        kind=step["kind"],
        label=step["label"],
        description=step.get("description"),
        document_type_key=step.get("document_type_key"),
        bundle_key=step.get("bundle_key"),
        bundle_label=step.get("bundle_label"),
        position=step.get("position") or 0,
        enabled=bool(step.get("enabled", True)),
        signer_roles=list(step.get("signer_roles") or []),
        locked=bool(step.get("locked", False)),
        items=[
            CollectItemRead(
                item_key=i["item_key"],
                label=i["label"],
                help_text=i.get("help_text"),
                required=bool(i.get("required", True)),
                accepted_formats=list(i.get("accepted_formats") or []),
            )
            for i in (items or [])
        ],
    )
