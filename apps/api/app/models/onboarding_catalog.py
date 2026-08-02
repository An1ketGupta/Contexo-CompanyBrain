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
    # 'bgv' or 'policies' on system steps; what the step does, as opposed to
    # what it is called. Null on every other kind.
    system_action: str | None = None
    locked: bool = False
    # Whether HR must accept what the candidate did here before the run moves
    # on. Meaningless on a step the candidate never touches — a policy sign-off,
    # a document nobody signs — and the agent ignores it there.
    requires_hr_approval: bool = True
    items: list[CollectItemRead] = Field(default_factory=list)


class DocumentTypeRead(BaseModel):
    """A template an org can put in a "send official documents" step."""

    key: str
    label: str
    description: str | None = None
    # False when the org has no active default template for this type. The
    # picker still offers it — you configure the flow before you upload the
    # paperwork — but says so, because a step whose template is missing blocks
    # the run when it gets there.
    has_template: bool = False


class CatalogRead(BaseModel):
    steps: list[StepRead]
    # False until the org saves a choice — drives the first-run setup screen,
    # the same way the four-boolean endpoint it replaces did.
    configured: bool
    document_types: list[DocumentTypeRead] = Field(default_factory=list)


class CreateStepRequest(BaseModel):
    """Add a step of any kind.

    Which of the optional fields matter depends on `kind`:
      collect   — `items`, the checklist the candidate answers
      generate  — `document_type_keys` (more than one makes a bundle) and
                  `signer_roles`
      system    — `system_action`
    """

    kind: str = Field(..., pattern="^(generate|collect|system)$")
    label: str = Field(..., min_length=1, max_length=200)
    # Position expressed the way an org thinks about it: "ask for these right
    # after the letter of intent". None puts the step first.
    after_step_key: str | None = Field(default=None, max_length=64)
    items: list[CollectItemInput] = Field(default_factory=list, max_length=40)
    document_type_keys: list[str] = Field(default_factory=list, max_length=10)
    signer_roles: list[str] = Field(default_factory=list, max_length=2)
    system_action: str | None = Field(default=None, pattern="^(bgv|policies)$")
    requires_hr_approval: bool = True


class CreateCollectStepRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=200)
    after_step_key: str | None = Field(default=None, max_length=64)
    items: list[CollectItemInput] = Field(..., min_length=1, max_length=40)


class UpdateStepRequest(BaseModel):
    enabled: bool | None = None
    label: str | None = Field(default=None, min_length=1, max_length=200)
    # Order in the list is routing order — whoever is first signs first.
    signer_roles: list[str] | None = Field(default=None, max_length=2)
    requires_hr_approval: bool | None = None
    after_step_key: str | None = Field(default=None, max_length=64)
    move: str | None = Field(default=None, pattern="^(up|down)$")


class ReplaceItemsRequest(BaseModel):
    items: list[CollectItemInput] = Field(..., min_length=1, max_length=40)


class ReviewStepRequest(BaseModel):
    """HR's verdict on what the candidate did at one step.

    Unlike `ReviewSubmissionRequest`, which records an opinion about one file,
    this one moves the run: approving releases the step, rejecting sends the
    ask back to the candidate. `note` is optional on approval and required on
    rejection — the router enforces that, because "why" is the only instruction
    the candidate gets and an empty rejection is indistinguishable from silence.
    """

    decision: str = Field(..., pattern="^(approved|rejected)$")
    note: str | None = Field(default=None, max_length=1000)


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
    """A verdict on one file, not on the step.

    Marking files here is how HR says *which* documents are wrong before
    sending the step back with `ReviewStepRequest` — that is what moves the
    run. On its own this only records the judgement and stops the file counting
    as filed, so the item re-opens the next time the step is asked.
    """

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


class PublicCandidateDocumentsRead(CandidateOnboardingRead):
    """The same checklist, served to a link holder rather than a session.

    Carries the expiry so the page can say how long the link is good for
    instead of only finding out by failing.
    """

    expires_at: str | None = None


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
        system_action=step.get("system_action"),
        locked=bool(step.get("locked", False)),
        requires_hr_approval=bool(step.get("requires_hr_approval", True)),
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
