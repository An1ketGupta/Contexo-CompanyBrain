"""Template library — types, templates, and immutable versions.

Every write here goes through the service-role client and filters on `org_id`
explicitly, because the service role bypasses RLS. The select policies in
migration 099 protect direct reads from the frontend; this module is the only
thing that writes.

The invariant this module owns: **an uploaded file is never modified or
replaced.** "Editing" a template means adding a version and promoting it. The
previous version's bytes stay exactly where they were, which is what lets a
`generated_documents` row from six months ago still resolve to the document it
was actually produced from.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

from app.database import get_service_client
from app.services.documents import audit, storage
from app.services.documents.constants import (
    ANALYSIS_PENDING,
    AUDIT_TEMPLATE_ARCHIVED,
    AUDIT_TEMPLATE_CREATED,
    AUDIT_VERSION_PROMOTED,
    AUDIT_VERSION_UPLOADED,
    ENTITY_TEMPLATE,
    ENTITY_VERSION,
    MIME_DOCX,
    TEMPLATE_ACTIVE,
    TEMPLATE_ARCHIVED,
    TEMPLATE_DRAFT,
)

log = logging.getLogger(__name__)


class TemplateNotFound(LookupError):
    """No such template, or it belongs to another org."""


class TemplateError(RuntimeError):
    """A template operation could not be completed. Message is HR-readable."""


def _svc():
    return get_service_client()


async def _run(fn):
    return await asyncio.to_thread(fn)


# ── Document types ─────────────────────────────────────────────────────────


async def list_document_types(org_id: str) -> list[dict[str, Any]]:
    """System types plus this org's own, in display order."""
    def _fetch() -> list[dict[str, Any]]:
        res = (
            _svc().table("document_types")
            .select("*")
            .or_(f"org_id.is.null,org_id.eq.{org_id}")
            .order("sort_order")
            .order("label")
            .execute()
        )
        return res.data or []

    return await _run(_fetch)


async def resolve_document_type(org_id: str, key: str) -> dict[str, Any] | None:
    """Find a type by key, preferring the org's own override of a system key."""
    types = await list_document_types(org_id)
    own = [t for t in types if t["key"] == key and t.get("org_id")]
    if own:
        return own[0]
    system = [t for t in types if t["key"] == key]
    return system[0] if system else None


async def create_document_type(
    *, org_id: str, key: str, label: str, description: str | None = None
) -> dict[str, Any]:
    """Add an org-specific document type.

    This is the operation that makes "new document types without code changes"
    real: no CHECK constraint to widen, no migration, no deploy.
    """
    row = {
        "org_id": org_id,
        "key": key,
        "label": label,
        "description": description,
        "is_system": False,
    }

    def _insert() -> dict[str, Any]:
        res = _svc().table("document_types").insert(row).execute()
        return (res.data or [{}])[0]

    try:
        return await _run(_insert)
    except Exception as exc:  # noqa: BLE001
        raise TemplateError(
            f"Couldn't add the document type '{label}'. "
            "A type with that key may already exist."
        ) from exc


# ── Templates ──────────────────────────────────────────────────────────────


async def create_template(
    *,
    org_id: str,
    document_type_id: str,
    name: str,
    description: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    row = {
        "org_id": org_id,
        "document_type_id": document_type_id,
        "name": name,
        "description": description,
        "status": TEMPLATE_DRAFT,
        "created_by": created_by,
    }

    def _insert() -> dict[str, Any]:
        res = _svc().table("doc_templates").insert(row).execute()
        return (res.data or [{}])[0]

    template = await _run(_insert)
    await audit.record(
        org_id=org_id,
        action=AUDIT_TEMPLATE_CREATED,
        entity_type=ENTITY_TEMPLATE,
        entity_id=template.get("id"),
        actor_user_id=created_by,
        payload={"name": name},
    )
    return template


async def get_template(org_id: str, template_id: str) -> dict[str, Any]:
    def _fetch() -> dict[str, Any] | None:
        res = (
            _svc().table("doc_templates")
            .select("*, document_types(key, label)")
            .eq("id", template_id)
            .eq("org_id", org_id)
            .limit(1)
            .execute()
        )
        return (res.data or [None])[0]

    row = await _run(_fetch)
    if not row:
        raise TemplateNotFound(template_id)
    return row


async def list_templates(
    *, org_id: str, include_archived: bool = False
) -> list[dict[str, Any]]:
    def _fetch() -> list[dict[str, Any]]:
        query = (
            _svc().table("doc_templates")
            .select("*, document_types(key, label)")
            .eq("org_id", org_id)
        )
        if not include_archived:
            query = query.neq("status", TEMPLATE_ARCHIVED)
        return query.order("updated_at", desc=True).execute().data or []

    return await _run(_fetch)


async def update_template(
    *, org_id: str, template_id: str, patch: dict[str, Any], actor_user_id: str | None = None
) -> dict[str, Any]:
    """Update mutable template metadata. Never touches versions or bytes."""
    allowed = {"name", "description", "status", "document_type_id"}
    clean = {k: v for k, v in patch.items() if k in allowed}
    if not clean:
        return await get_template(org_id, template_id)

    def _update() -> dict[str, Any] | None:
        res = (
            _svc().table("doc_templates")
            .update(clean)
            .eq("id", template_id)
            .eq("org_id", org_id)
            .execute()
        )
        return (res.data or [None])[0]

    row = await _run(_update)
    if not row:
        raise TemplateNotFound(template_id)
    return row


async def archive_template(
    *, org_id: str, template_id: str, actor_user_id: str | None = None
) -> dict[str, Any]:
    """Archive rather than delete.

    A template referenced by a generated document must stay resolvable —
    `generated_documents.template_id` is ON DELETE RESTRICT precisely so that
    history cannot be rewritten by tidying up the library.
    """
    row = await update_template(
        org_id=org_id,
        template_id=template_id,
        patch={"status": TEMPLATE_ARCHIVED, "is_default": False},
    )
    await audit.record(
        org_id=org_id,
        action=AUDIT_TEMPLATE_ARCHIVED,
        entity_type=ENTITY_TEMPLATE,
        entity_id=template_id,
        actor_user_id=actor_user_id,
    )
    return row


async def set_default(
    *, org_id: str, template_id: str, actor_user_id: str | None = None
) -> dict[str, Any]:
    """Make this the template automated generation picks for its type.

    Clears the previous default first: the partial unique index on
    (org_id, document_type_id) WHERE is_default AND status='active' would
    otherwise reject the update, which is the point of having it.
    """
    template = await get_template(org_id, template_id)

    def _clear() -> None:
        (
            _svc().table("doc_templates")
            .update({"is_default": False})
            .eq("org_id", org_id)
            .eq("document_type_id", template["document_type_id"])
            .eq("is_default", True)
            .execute()
        )

    await _run(_clear)

    def _set() -> dict[str, Any] | None:
        res = (
            _svc().table("doc_templates")
            .update({"is_default": True, "status": TEMPLATE_ACTIVE})
            .eq("id", template_id)
            .eq("org_id", org_id)
            .execute()
        )
        return (res.data or [None])[0]

    row = await _run(_set)
    if not row:
        raise TemplateNotFound(template_id)
    return row


# ── Versions ───────────────────────────────────────────────────────────────


async def _next_version_no(template_id: str) -> int:
    def _fetch() -> int:
        res = (
            _svc().table("doc_template_versions")
            .select("version_no")
            .eq("template_id", template_id)
            .order("version_no", desc=True)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return (rows[0]["version_no"] + 1) if rows else 1

    return await _run(_fetch)


async def add_version(
    *,
    org_id: str,
    template_id: str,
    data: bytes,
    original_filename: str,
    mime_type: str = MIME_DOCX,
    uploaded_by: str | None = None,
    promote: bool = True,
) -> dict[str, Any]:
    """Store a new file for a template and record it as the next version.

    The upload happens before the row is written, so a storage failure leaves no
    version row pointing at bytes that were never saved. The reverse ordering
    would leave the template permanently broken.
    """
    await get_template(org_id, template_id)  # ownership check

    version_no = await _next_version_no(template_id)
    path = storage.template_version_path(
        org_id=org_id,
        template_id=template_id,
        version_no=version_no,
        filename=original_filename,
    )
    await storage.upload(path=path, data=data, mime_type=mime_type)

    row = {
        "org_id": org_id,
        "template_id": template_id,
        "version_no": version_no,
        "storage_path": path,
        "original_filename": original_filename,
        "mime_type": mime_type,
        "file_bytes": len(data),
        "file_sha256": hashlib.sha256(data).hexdigest(),
        "analysis_status": ANALYSIS_PENDING,
        "uploaded_by": uploaded_by,
    }

    def _insert() -> dict[str, Any]:
        res = _svc().table("doc_template_versions").insert(row).execute()
        return (res.data or [{}])[0]

    version = await _run(_insert)

    if promote:
        await promote_version(
            org_id=org_id, template_id=template_id, version_id=version["id"]
        )

    await audit.record(
        org_id=org_id,
        action=AUDIT_VERSION_UPLOADED,
        entity_type=ENTITY_VERSION,
        entity_id=version.get("id"),
        actor_user_id=uploaded_by,
        payload={
            "template_id": template_id,
            "version_no": version_no,
            "filename": original_filename,
            "bytes": len(data),
        },
    )
    return version


async def promote_version(
    *, org_id: str, template_id: str, version_id: str, actor_user_id: str | None = None
) -> None:
    """Point the template at a version. One UPDATE; no bytes move."""
    def _update() -> None:
        (
            _svc().table("doc_templates")
            .update({"current_version_id": version_id})
            .eq("id", template_id)
            .eq("org_id", org_id)
            .execute()
        )

    await _run(_update)
    await audit.record(
        org_id=org_id,
        action=AUDIT_VERSION_PROMOTED,
        entity_type=ENTITY_TEMPLATE,
        entity_id=template_id,
        actor_user_id=actor_user_id,
        payload={"version_id": version_id},
    )


async def get_version(org_id: str, version_id: str) -> dict[str, Any]:
    def _fetch() -> dict[str, Any] | None:
        res = (
            _svc().table("doc_template_versions")
            .select("*")
            .eq("id", version_id)
            .eq("org_id", org_id)
            .limit(1)
            .execute()
        )
        return (res.data or [None])[0]

    row = await _run(_fetch)
    if not row:
        raise TemplateNotFound(version_id)
    return row


async def list_versions(*, org_id: str, template_id: str) -> list[dict[str, Any]]:
    def _fetch() -> list[dict[str, Any]]:
        res = (
            _svc().table("doc_template_versions")
            .select("*")
            .eq("org_id", org_id)
            .eq("template_id", template_id)
            .order("version_no", desc=True)
            .execute()
        )
        return res.data or []

    return await _run(_fetch)


async def set_analysis_status(
    *,
    version_id: str,
    status: str,
    error: str | None = None,
    detected_type_id: str | None = None,
    detected_type_confidence: float | None = None,
    analyzed_at: str | None = None,
) -> None:
    patch: dict[str, Any] = {"analysis_status": status, "analysis_error": error}
    if detected_type_id is not None:
        patch["detected_type_id"] = detected_type_id
    if detected_type_confidence is not None:
        patch["detected_type_confidence"] = detected_type_confidence
    if analyzed_at is not None:
        patch["analyzed_at"] = analyzed_at

    def _update() -> None:
        (
            _svc().table("doc_template_versions")
            .update(patch)
            .eq("id", version_id)
            .execute()
        )

    await _run(_update)


async def load_version_bytes(*, org_id: str, version_id: str) -> tuple[bytes, dict[str, Any]]:
    """Fetch a version row and its stored file."""
    version = await get_version(org_id, version_id)
    data = await storage.download(version["storage_path"])
    return data, version


# ── Resolution for automated generation ────────────────────────────────────


async def resolve_default_version(
    *, org_id: str, type_key: str
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The (template, version) an agent should use for a document type.

    Returns None when the org has no active default template for that type, so
    the caller can park the run in a clean "missing template" state rather than
    guessing. Deliberately does NOT fall back to "most recently created", which
    is what the previous pipeline did and which silently changed which document
    was legally in force whenever anyone uploaded another one.
    """
    doc_type = await resolve_document_type(org_id, type_key)
    if not doc_type:
        return None

    def _fetch() -> dict[str, Any] | None:
        res = (
            _svc().table("doc_templates")
            .select("*")
            .eq("org_id", org_id)
            .eq("document_type_id", doc_type["id"])
            .eq("status", TEMPLATE_ACTIVE)
            .eq("is_default", True)
            .limit(1)
            .execute()
        )
        return (res.data or [None])[0]

    template = await _run(_fetch)
    if not template or not template.get("current_version_id"):
        return None

    version = await get_version(org_id, template["current_version_id"])
    return template, version
