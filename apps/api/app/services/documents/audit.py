"""Audit trail for the document pipeline.

Every action across the workflow — uploads, analyses, variable decisions,
mapping edits, validations, generations, approvals, sends — appends one row to
`document_audit_logs`.

Two deliberate properties:

  * **Best-effort.** A failure to write an audit row logs a warning and returns;
    it never propagates. Losing the record that a document was approved is bad,
    but failing the approval *because* the record could not be written is worse,
    and leaves HR with a document stuck in limbo.

  * **Never carries document content.** `payload` is for identifiers, field
    names, counts, and reasons. Candidate PII and rendered document text belong
    in `generated_documents.candidate_snapshot` / `context_snapshot`, which are
    RLS-scoped and deleted with the org. An audit log tends to be the
    longest-lived table in a system and the one most often exported.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.database import get_service_client

log = logging.getLogger(__name__)

ACTOR_USER = "user"
ACTOR_AGENT = "agent"
ACTOR_SYSTEM = "system"


async def record(
    *,
    org_id: str,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    actor_user_id: str | None = None,
    actor_kind: str = ACTOR_USER,
    payload: dict[str, Any] | None = None,
) -> None:
    """Append one audit row. Never raises.

    `action` is free text by design — `document_audit_logs.action` has no CHECK
    constraint, so a new kind of event is a new string and not a migration. Use
    the `AUDIT_*` constants in `constants.py` for the ones this package already
    emits, so the values stay greppable.
    """
    svc = get_service_client()

    row = {
        "org_id": org_id,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "actor_user_id": actor_user_id,
        "actor_kind": actor_kind,
        "payload": payload or {},
    }

    try:
        await asyncio.to_thread(
            lambda: svc.table("document_audit_logs").insert(row).execute()
        )
    except Exception as exc:  # noqa: BLE001 — auditing must never break the caller
        log.warning(
            "documents.audit_write_failed action=%s entity=%s/%s err=%s",
            action,
            entity_type,
            entity_id,
            exc,
        )


async def list_for_entity(
    *,
    org_id: str,
    entity_type: str,
    entity_id: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Read the audit trail for one entity, newest first.

    Used by the template builder's history panel and the generated-document
    detail view. Reads through the service client and filters on `org_id`
    explicitly, because the service role bypasses RLS.
    """
    svc = get_service_client()

    def _fetch() -> list[dict[str, Any]]:
        res = (
            svc.table("document_audit_logs")
            .select("*")
            .eq("org_id", org_id)
            .eq("entity_type", entity_type)
            .eq("entity_id", entity_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []

    try:
        return await asyncio.to_thread(_fetch)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "documents.audit_read_failed entity=%s/%s err=%s",
            entity_type,
            entity_id,
            exc,
        )
        return []
