"""Generate one document, end to end.

    resolve template → resolve candidate → map → validate → render → persist

Returns an outcome object rather than raising for expected failures. Every step
here has a failure mode that is somebody's ordinary Tuesday — no template
uploaded, a manager not filled in, HR edited the template after confirming — and
those are states to be reported, not exceptions to be caught. Only genuine bugs
raise.

Two rules the schema enforces and this module honours:

  * **A generation is never overwritten.** Regenerating mints a new row with the
    next `generation_no`, and the artifacts are written to a path containing
    that number, so the document a candidate actually received is always
    recoverable.

  * **Validation blocks.** A failing report produces a `validation_failed` row
    with the report attached and no artifacts at all. There is no code path that
    produces a partially-filled legal document.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from docx import Document

from app.database import get_service_client
from app.services.documents import audit, schema, storage, templates
from app.services.documents.analysis.detector import scan_for_unfilled_signals
from app.services.documents.candidates.resolver import (
    CandidateNotResolvable,
    flatten,
    resolve_profile,
)
from app.services.documents.constants import (
    AUDIT_GENERATION_COMPLETED,
    AUDIT_GENERATION_FAILED,
    AUDIT_GENERATION_STARTED,
    AUDIT_VALIDATION_FAILED,
    ENTITY_GENERATED_DOCUMENT,
    FORMAT_DOCX,
    FORMAT_PDF,
    GEN_GENERATED,
    GEN_GENERATION_FAILED,
    GEN_VALIDATION_FAILED,
    MIME_DOCX,
    MIME_PDF,
)
from app.services.documents.docx_positions import (
    canonical_paragraphs,
    paragraph_run_text,
)
from app.services.documents.generation.renderer import (
    DocumentRenderError,
    SlotDriftError,
    render_to_pdf,
)
from app.services.documents.mapping.engine import (
    load_configured_mappings,
    resolve_values,
)
from app.services.documents.validation.engine import build_context, validate

log = logging.getLogger(__name__)

# Outcome codes the caller branches on. Distinct from the persisted row status
# because two of these never produce a row at all.
OUTCOME_GENERATED = "generated"
OUTCOME_MISSING_TEMPLATE = "missing_template"
OUTCOME_NO_FIELDS = "no_confirmed_fields"
OUTCOME_VALIDATION_FAILED = "validation_failed"
OUTCOME_DRIFT = "template_drift"
OUTCOME_RENDER_FAILED = "render_failed"
OUTCOME_NO_CANDIDATE = "no_candidate"


@dataclass
class GenerationOutcome:
    """What happened, in a shape a caller can branch on without try/except."""

    outcome: str
    generated_document_id: str | None = None
    template_id: str | None = None
    version_id: str | None = None
    docx_bytes: bytes | None = None
    pdf_bytes: bytes | None = None
    docx_path: str | None = None
    pdf_path: str | None = None
    validation_report: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.outcome == OUTCOME_GENERATED


def _svc():
    return get_service_client()


async def _run(fn):
    return await asyncio.to_thread(fn)


async def _next_generation_no(
    *, org_id: str, onboarding_run_id: str | None, version_id: str
) -> int:
    """Next number for this (run, version) pair, or this version alone when the
    document has no run behind it."""
    def _fetch() -> int:
        query = (
            _svc().table("generated_documents")
            .select("generation_no")
            .eq("org_id", org_id)
            .eq("version_id", version_id)
        )
        if onboarding_run_id:
            query = query.eq("onboarding_run_id", onboarding_run_id)
        rows = (
            query.order("generation_no", desc=True).limit(1).execute().data or []
        )
        return (rows[0]["generation_no"] + 1) if rows else 1

    return await _run(_fetch)


async def _insert_generation(row: dict[str, Any]) -> dict[str, Any]:
    def _insert() -> dict[str, Any]:
        res = _svc().table("generated_documents").insert(row).execute()
        return (res.data or [{}])[0]

    return await _run(_insert)


async def _insert_file(row: dict[str, Any]) -> None:
    def _insert() -> None:
        _svc().table("generated_files").insert(row).execute()

    await _run(_insert)


async def generate(
    *,
    org_id: str,
    type_key: str | None = None,
    template_id: str | None = None,
    onboarding_run_id: str | None = None,
    candidate_id: str | None = None,
    requisition_id: str | None = None,
    overrides: dict[str, Any] | None = None,
    variable_values: dict[str, Any] | None = None,
    actor_user_id: str | None = None,
) -> GenerationOutcome:
    """Produce one document.

    Supply either `template_id` (this exact template) or `type_key` (the org's
    default template for that type). The candidate is resolved from whichever
    of `onboarding_run_id` / `candidate_id` / `requisition_id` is given.

    Two ways to hand-correct a value, and they answer different questions.
    `overrides` patches the candidate profile by dotted path, so every template
    that maps to that path sees the correction. `variable_values` sets one
    template field by `internal_name` — the answer to a validation error naming
    a field, including one no profile path can supply.
    """
    # ── 1. Template ────────────────────────────────────────────────────────
    if template_id:
        try:
            template = await templates.get_template(org_id, template_id)
        except templates.TemplateNotFound:
            return GenerationOutcome(
                outcome=OUTCOME_MISSING_TEMPLATE,
                error="That template no longer exists.",
            )
        if not template.get("current_version_id"):
            return GenerationOutcome(
                outcome=OUTCOME_MISSING_TEMPLATE,
                template_id=template_id,
                error="That template has no file uploaded yet.",
            )
        version = await templates.get_version(org_id, template["current_version_id"])
    elif type_key:
        resolved = await templates.resolve_default_version(
            org_id=org_id, type_key=type_key
        )
        if not resolved:
            return GenerationOutcome(
                outcome=OUTCOME_MISSING_TEMPLATE,
                error=(
                    f"No default template is set for '{type_key}'. Upload one and "
                    "mark it as the default."
                ),
            )
        template, version = resolved
    else:
        raise ValueError("generate() needs either template_id or type_key")

    # ── 2. Confirmed schema ────────────────────────────────────────────────
    variables, slots = await schema.confirmed_schema(
        org_id=org_id, version_id=version["id"]
    )
    if not slots:
        return GenerationOutcome(
            outcome=OUTCOME_NO_FIELDS,
            template_id=template["id"],
            version_id=version["id"],
            error=(
                f"No fields have been confirmed on '{template['name']}'. "
                "Open the template and confirm its fields before generating."
            ),
        )

    await audit.record(
        org_id=org_id,
        action=AUDIT_GENERATION_STARTED,
        entity_type=ENTITY_GENERATED_DOCUMENT,
        actor_user_id=actor_user_id,
        actor_kind="user" if actor_user_id else "agent",
        payload={"template_id": template["id"], "version_id": version["id"]},
    )

    # ── 3. Candidate ───────────────────────────────────────────────────────
    try:
        profile = await resolve_profile(
            org_id=org_id,
            onboarding_run_id=onboarding_run_id,
            candidate_id=candidate_id,
            requisition_id=requisition_id,
            overrides=overrides,
        )
    except CandidateNotResolvable as exc:
        return GenerationOutcome(
            outcome=OUTCOME_NO_CANDIDATE,
            template_id=template["id"],
            version_id=version["id"],
            error=str(exc),
        )

    # ── 4. Mapping ─────────────────────────────────────────────────────────
    configured = await load_configured_mappings(
        org_id=org_id,
        version_id=version["id"],
        document_type_id=template.get("document_type_id"),
    )
    values, sources = resolve_values(
        variables=variables,
        flat_profile=flatten(profile),
        configured=configured,
        manual=variable_values,
    )
    context = build_context(variables=variables, values=values)

    # ── 5. Validation ──────────────────────────────────────────────────────
    report = validate(variables=variables, context=context, slots=slots)

    generation_no = await _next_generation_no(
        org_id=org_id, onboarding_run_id=onboarding_run_id, version_id=version["id"]
    )
    base_row = {
        "org_id": org_id,
        "template_id": template["id"],
        "version_id": version["id"],
        "onboarding_run_id": onboarding_run_id,
        "requisition_id": requisition_id,
        "candidate_id": candidate_id,
        "generation_no": generation_no,
        "candidate_snapshot": profile,
        "context_snapshot": {"values": context, "sources": sources},
        "validation_report": report.to_dict(),
        "created_by": actor_user_id,
    }

    if not report.ok:
        row = await _insert_generation({**base_row, "status": GEN_VALIDATION_FAILED})
        await audit.record(
            org_id=org_id,
            action=AUDIT_VALIDATION_FAILED,
            entity_type=ENTITY_GENERATED_DOCUMENT,
            entity_id=row.get("id"),
            actor_user_id=actor_user_id,
            payload={"errors": [e.code for e in report.errors]},
        )
        return GenerationOutcome(
            outcome=OUTCOME_VALIDATION_FAILED,
            generated_document_id=row.get("id"),
            template_id=template["id"],
            version_id=version["id"],
            validation_report=report.to_dict(),
            context=context,
            error="; ".join(e.message for e in report.errors[:3]),
        )

    # ── 6. Render ──────────────────────────────────────────────────────────
    try:
        docx_bytes, _version_row = await templates.load_version_bytes(
            org_id=org_id, version_id=version["id"]
        )
    except (templates.TemplateNotFound, storage.TemplateStorageError) as exc:
        return GenerationOutcome(
            outcome=OUTCOME_RENDER_FAILED,
            template_id=template["id"],
            version_id=version["id"],
            error=str(exc),
        )

    try:
        filled_docx, pdf_bytes = await render_to_pdf(
            docx_bytes=docx_bytes,
            slots=slots,
            context=context,
            template_name=template["name"],
        )
    except SlotDriftError as exc:
        row = await _insert_generation({
            **base_row,
            "status": GEN_GENERATION_FAILED,
            "error_message": str(exc),
        })
        await audit.record(
            org_id=org_id,
            action=AUDIT_GENERATION_FAILED,
            entity_type=ENTITY_GENERATED_DOCUMENT,
            entity_id=row.get("id"),
            actor_user_id=actor_user_id,
            payload={"reason": "template_drift"},
        )
        return GenerationOutcome(
            outcome=OUTCOME_DRIFT,
            generated_document_id=row.get("id"),
            template_id=template["id"],
            version_id=version["id"],
            error=str(exc),
        )
    except DocumentRenderError as exc:
        row = await _insert_generation({
            **base_row,
            "status": GEN_GENERATION_FAILED,
            "error_message": str(exc),
        })
        await audit.record(
            org_id=org_id,
            action=AUDIT_GENERATION_FAILED,
            entity_type=ENTITY_GENERATED_DOCUMENT,
            entity_id=row.get("id"),
            actor_user_id=actor_user_id,
            payload={"reason": "render_failed"},
        )
        return GenerationOutcome(
            outcome=OUTCOME_RENDER_FAILED,
            generated_document_id=row.get("id"),
            template_id=template["id"],
            version_id=version["id"],
            error=str(exc),
        )

    # ── 7. Persist ─────────────────────────────────────────────────────────
    row = await _insert_generation({
        **base_row,
        "status": GEN_GENERATED,
        "generated_at": datetime.now(UTC).isoformat(),
    })
    document_id = row.get("id")

    docx_path = storage.generated_file_path(
        org_id=org_id,
        generated_document_id=document_id,
        generation_no=generation_no,
        fmt=FORMAT_DOCX,
    )
    await storage.upload(path=docx_path, data=filled_docx, mime_type=MIME_DOCX)
    await _insert_file({
        "org_id": org_id,
        "generated_document_id": document_id,
        "format": FORMAT_DOCX,
        "storage_path": docx_path,
        "file_bytes": len(filled_docx),
        "file_sha256": hashlib.sha256(filled_docx).hexdigest(),
    })

    pdf_path = None
    warnings: list[str] = []
    if pdf_bytes:
        pdf_path = storage.generated_file_path(
            org_id=org_id,
            generated_document_id=document_id,
            generation_no=generation_no,
            fmt=FORMAT_PDF,
        )
        await storage.upload(path=pdf_path, data=pdf_bytes, mime_type=MIME_PDF)
        await _insert_file({
            "org_id": org_id,
            "generated_document_id": document_id,
            "format": FORMAT_PDF,
            "storage_path": pdf_path,
            "file_bytes": len(pdf_bytes),
            "file_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        })
    else:
        warnings.append(
            "PDF conversion failed — the Word document is still available."
        )

    warnings.extend(_unfilled_warnings(filled_docx))
    warnings.extend(w["message"] for w in report.to_dict().get("warnings", []))

    await audit.record(
        org_id=org_id,
        action=AUDIT_GENERATION_COMPLETED,
        entity_type=ENTITY_GENERATED_DOCUMENT,
        entity_id=document_id,
        actor_user_id=actor_user_id,
        actor_kind="user" if actor_user_id else "agent",
        payload={
            "template_id": template["id"],
            "generation_no": generation_no,
            "has_pdf": bool(pdf_bytes),
            "warnings": len(warnings),
        },
    )

    return GenerationOutcome(
        outcome=OUTCOME_GENERATED,
        generated_document_id=document_id,
        template_id=template["id"],
        version_id=version["id"],
        docx_bytes=filled_docx,
        pdf_bytes=pdf_bytes,
        docx_path=docx_path,
        pdf_path=pdf_path,
        validation_report=report.to_dict(),
        context=context,
        warnings=warnings,
    )


def _unfilled_warnings(filled_docx: bytes) -> list[str]:
    """Scan the RENDERED document for fill-points nobody mapped.

    Detection cannot have perfect recall on arbitrary customer documents, so
    this is the last line of defence: if a generated offer letter still contains
    `________`, some field was missed and HR should hear about it before the
    candidate does. Strictly non-fatal — a document with one unfilled line is
    more useful than a failed generation.
    """
    try:
        doc = Document(io.BytesIO(filled_docx))
        text = "\n".join(
            paragraph_run_text(p) for p, _kind in canonical_paragraphs(doc)
        )
        return scan_for_unfilled_signals(text)
    except Exception as exc:  # noqa: BLE001 — a warning scan must never fail a render
        log.warning("documents.unfilled_scan_failed err=%s", exc)
        return []


