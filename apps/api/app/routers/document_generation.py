"""Generate documents from a template, and review them.

The second half of the pipeline's API flow:

    Select Candidate → Aggregate → Validate → Generate → Preview → Approve

Approval is where this pipeline ends. What happens to an approved document —
signing, sending, filing — belongs to whatever workflow asked for it.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import verify_jwt
from app.errors import NoOrganization
from app.models.documents_pipeline import (
    GeneratedDocumentRead,
    GeneratedFileRead,
    GenerateRequest,
    GenerateResponse,
    RejectRequest,
)
from app.services.documents import storage
from app.services.documents.generation import service as generation

log = logging.getLogger(__name__)

router = APIRouter(prefix="/generated-documents", tags=["document-generation"])

# Outcomes that mean "the request was fine, the situation isn't" — the caller
# gets 200 with an outcome code, because these are states HR resolves in the UI,
# not client errors to be thrown at them as a red toast.
_SOFT_OUTCOMES = {
    generation.OUTCOME_VALIDATION_FAILED,
    generation.OUTCOME_DRIFT,
    generation.OUTCOME_MISSING_TEMPLATE,
    generation.OUTCOME_NO_FIELDS,
    generation.OUTCOME_NO_CANDIDATE,
    generation.OUTCOME_RENDER_FAILED,
}


def _require_org(current_user: dict) -> tuple[str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    if not org_id or not user_id:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return org_id, user_id


async def _to_read(row: dict[str, Any]) -> GeneratedDocumentRead:
    """Shape a row for the UI, minting a fresh signed URL per artifact."""
    template = row.get("doc_templates") or {}
    files: list[GeneratedFileRead] = []
    for entry in row.get("generated_files") or []:
        url = await storage.mint_signed_url(entry["storage_path"])
        files.append(GeneratedFileRead(format=entry["format"], url=url))

    return GeneratedDocumentRead(
        id=row["id"],
        status=row["status"],
        template_id=row["template_id"],
        template_name=template.get("name"),
        version_id=row["version_id"],
        generation_no=row.get("generation_no", 1),
        onboarding_run_id=row.get("onboarding_run_id"),
        validation_report=row.get("validation_report") or {},
        context_snapshot=row.get("context_snapshot") or {},
        candidate_snapshot=row.get("candidate_snapshot") or {},
        error_message=row.get("error_message"),
        files=files,
        generated_at=row.get("generated_at"),
        approved_at=row.get("approved_at"),
        rejected_at=row.get("rejected_at"),
        rejection_reason=row.get("rejection_reason"),
        created_at=row.get("created_at"),
    )


@router.post("", response_model=GenerateResponse)
async def generate_document(
    body: GenerateRequest, current_user: dict = Depends(verify_jwt)
):
    """Produce a document.

    Returns 200 with an `outcome` for every expected failure — a missing
    template, a field HR hasn't filled in, a template edited since its fields
    were confirmed. Those are states to be shown and fixed, not errors.
    """
    org_id, user_id = _require_org(current_user)

    if not body.template_id and not body.type_key:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Choose a template, or a document type with a default template.",
        )

    outcome = await generation.generate(
        org_id=org_id,
        template_id=body.template_id,
        type_key=body.type_key,
        onboarding_run_id=body.onboarding_run_id,
        candidate_id=body.candidate_id,
        requisition_id=body.requisition_id,
        overrides=body.overrides,
        actor_user_id=user_id,
    )

    document = None
    if outcome.generated_document_id:
        row = await generation.get_generated(
            org_id=org_id, document_id=outcome.generated_document_id
        )
        if row:
            document = await _to_read(row)

    if outcome.outcome not in _SOFT_OUTCOMES and not outcome.ok:
        log.error("documents.generate_unexpected outcome=%s", outcome.outcome)

    return GenerateResponse(
        outcome=outcome.outcome,
        document=document,
        warnings=outcome.warnings,
        error=outcome.error,
    )


@router.get("", response_model=list[GeneratedDocumentRead])
async def list_documents(
    onboarding_run_id: str | None = None,
    limit: int = 50,
    current_user: dict = Depends(verify_jwt),
):
    org_id, _ = _require_org(current_user)
    rows = await generation.list_generated(
        org_id=org_id, onboarding_run_id=onboarding_run_id, limit=min(limit, 200)
    )
    return [await _to_read(row) for row in rows]


@router.get("/{document_id}", response_model=GeneratedDocumentRead)
async def get_document(document_id: str, current_user: dict = Depends(verify_jwt)):
    org_id, _ = _require_org(current_user)
    row = await generation.get_generated(org_id=org_id, document_id=document_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    return await _to_read(row)


@router.post("/{document_id}/approve", response_model=GeneratedDocumentRead)
async def approve_document(document_id: str, current_user: dict = Depends(verify_jwt)):
    org_id, user_id = _require_org(current_user)
    try:
        await generation.approve(
            org_id=org_id, document_id=document_id, actor_user_id=user_id
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.") from exc

    row = await generation.get_generated(org_id=org_id, document_id=document_id)
    return await _to_read(row or {})


@router.post("/{document_id}/reject", response_model=GeneratedDocumentRead)
async def reject_document(
    document_id: str,
    body: RejectRequest | None = None,
    current_user: dict = Depends(verify_jwt),
):
    org_id, user_id = _require_org(current_user)
    try:
        await generation.reject(
            org_id=org_id,
            document_id=document_id,
            actor_user_id=user_id,
            reason=body.reason if body else None,
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.") from exc

    row = await generation.get_generated(org_id=org_id, document_id=document_id)
    return await _to_read(row or {})
