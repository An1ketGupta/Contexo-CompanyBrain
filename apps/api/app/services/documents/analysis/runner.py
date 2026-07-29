"""Orchestrates one analysis pass: load → detect → understand → persist.

Separated from both `detector` and `analyzer` because those two are pure
functions of bytes and should stay that way — they are the parts worth testing
exhaustively, and neither should need a database to run.

Everything that can fail here fails *forward*. Detection produces nothing? The
version still lands in a reviewable state with zero fields, and HR adds them by
hand. The model is down? `analysis_status='failed'` with the reason recorded,
and HR adds them by hand. There is no path where a template becomes unusable
because analysis did not go well.
"""
from __future__ import annotations

import io
import logging
from datetime import UTC, datetime
from typing import Any

from docx import Document

from app.services.documents import audit, schema, templates
from app.services.documents.analysis.analyzer import (
    TemplateAnalysisError,
    analyze_template,
)
from app.services.documents.analysis.detector import find_all_candidates
from app.services.documents.constants import (
    ANALYSIS_COMPLETED,
    ANALYSIS_FAILED,
    ANALYSIS_RUNNING,
    AUDIT_ANALYSIS_COMPLETED,
    AUDIT_ANALYSIS_FAILED,
    AUDIT_ANALYSIS_STARTED,
    ENTITY_VERSION,
    MIME_DOCX,
)
from app.services.documents.docx_positions import (
    canonical_paragraphs,
    paragraph_hash,
    paragraph_run_text,
)

log = logging.getLogger(__name__)


class AnalysisNotSupported(RuntimeError):
    """This file cannot be analysed automatically.

    A PDF has no run model to compute offsets against, so fill-points cannot be
    anchored in one. PDFs are still accepted and stored — HR defines fields
    manually — but nothing here can look inside them.
    """


def _paragraph_hashes(docx_bytes: bytes) -> dict[int, str]:
    """Hash every paragraph's run text, keyed by canonical index.

    Computed here rather than inside `schema` so that persistence stays free of
    docx knowledge, and against the same enumeration the detector used so the
    stored hash is the one the renderer will re-check.
    """
    doc = Document(io.BytesIO(docx_bytes))
    return {
        idx: paragraph_hash(paragraph_run_text(paragraph))
        for idx, (paragraph, _kind) in enumerate(canonical_paragraphs(doc))
    }


async def run_analysis(
    *,
    org_id: str,
    version_id: str,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Analyse one template version and persist the result.

    Returns a summary the router hands straight back to the UI. Never raises for
    an analysis-quality problem — only for a genuinely missing version or an
    unreadable file.
    """
    data, version = await templates.load_version_bytes(
        org_id=org_id, version_id=version_id
    )

    if version.get("mime_type") != MIME_DOCX:
        raise AnalysisNotSupported(
            "Automatic field detection only works on Word (.docx) files. "
            "You can still add this template's fields manually."
        )

    await templates.set_analysis_status(version_id=version_id, status=ANALYSIS_RUNNING)
    await audit.record(
        org_id=org_id,
        action=AUDIT_ANALYSIS_STARTED,
        entity_type=ENTITY_VERSION,
        entity_id=version_id,
        actor_user_id=actor_user_id,
    )

    candidates = find_all_candidates(data)
    doc_types = await templates.list_document_types(org_id)
    allowed_keys = sorted({t["key"] for t in doc_types})

    try:
        analysis = await analyze_template(
            docx_bytes=data,
            candidates=candidates,
            allowed_type_keys=allowed_keys,
        )
    except TemplateAnalysisError as exc:
        await templates.set_analysis_status(
            version_id=version_id, status=ANALYSIS_FAILED, error=str(exc)
        )
        await audit.record(
            org_id=org_id,
            action=AUDIT_ANALYSIS_FAILED,
            entity_type=ENTITY_VERSION,
            entity_id=version_id,
            actor_user_id=actor_user_id,
            payload={"error": str(exc), "candidates_found": len(candidates)},
        )
        log.warning("documents.analysis_failed version=%s err=%s", version_id, exc)
        return {
            "status": ANALYSIS_FAILED,
            "error": str(exc),
            "candidates_found": len(candidates),
            "variables_created": 0,
            "slots_created": 0,
        }

    counts = await schema.save_analysis(
        org_id=org_id,
        version_id=version_id,
        variables=analysis.variables,
        slots=analysis.slots,
        source_file_sha256=version["file_sha256"],
        paragraph_hashes=_paragraph_hashes(data),
    )

    detected_type_id: str | None = None
    if analysis.document_type_key:
        match = next(
            (t for t in doc_types if t["key"] == analysis.document_type_key), None
        )
        detected_type_id = match["id"] if match else None

    await templates.set_analysis_status(
        version_id=version_id,
        status=ANALYSIS_COMPLETED,
        error=None,
        detected_type_id=detected_type_id,
        detected_type_confidence=analysis.document_type_confidence,
        analyzed_at=datetime.now(UTC).isoformat(),
    )

    await audit.record(
        org_id=org_id,
        action=AUDIT_ANALYSIS_COMPLETED,
        entity_type=ENTITY_VERSION,
        entity_id=version_id,
        actor_user_id=actor_user_id,
        payload={
            "detected_type": analysis.document_type_key,
            "candidates_found": len(candidates),
            "rejected_literals": len(analysis.rejected_literals),
            **counts,
        },
    )

    return {
        "status": ANALYSIS_COMPLETED,
        "detected_type": analysis.document_type_key,
        "detected_type_confidence": analysis.document_type_confidence,
        "candidates_found": len(candidates),
        "truncated": analysis.truncated,
        **counts,
    }
