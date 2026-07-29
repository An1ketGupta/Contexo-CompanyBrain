"""Template schema — the variables of a version and the slots that fill them.

This is where an analysis becomes rows, and where HR's decisions are protected
from being undone.

The rule that governs every write here: **re-running analysis never overwrites a
human decision.** A variable or slot HR has confirmed or rejected keeps its
status and its edits; only rows still sitting at `proposed` are refreshed. Two
reasons, both learned from the pipeline this replaces:

  * Re-analysis is the normal way to recover from a bad first pass, so it has to
    be safe to press. If it could revert confirmations, nobody would press it.

  * A rejected row is a decision, not an absence. Keeping it means the detector
    can propose the same wrong mapping again next time and be silently ignored,
    instead of pestering HR with it on every analysis.

Nothing here auto-confirms. `DEFAULT_CONFIRM_THRESHOLD` decides what the builder
pre-ticks for HR, not what gets used without being seen — a high-confidence
guess is still a guess, and this is a contract.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.database import get_service_client
from app.services.documents import audit
from app.services.documents.constants import (
    AUDIT_SLOT_CONFIRMED,
    AUDIT_SLOT_CREATED,
    AUDIT_SLOT_REJECTED,
    AUDIT_VARIABLE_CREATED,
    AUDIT_VARIABLE_UPDATED,
    ENTITY_SLOT,
    ENTITY_VARIABLE,
    SOURCE_AI,
    SOURCE_MANUAL,
    STATUS_CONFIRMED,
    STATUS_PROPOSED,
    STATUS_REJECTED,
)

log = logging.getLogger(__name__)

# Statuses that represent a human having made up their mind. Rows in these
# states are never touched by a re-analysis.
_HR_DECIDED = (STATUS_CONFIRMED, STATUS_REJECTED)

# Variable columns a re-analysis may refresh on a still-proposed row. Notably
# absent: `status`, `confirmed_by`, `confirmed_at`.
_REFRESHABLE_VARIABLE_FIELDS = (
    "display_name", "description", "data_type", "is_required",
    "example_value", "confidence",
)


class SchemaNotFound(LookupError):
    """No such variable or slot in this org."""


def _svc():
    return get_service_client()


async def _run(fn):
    return await asyncio.to_thread(fn)


# ── Reads ──────────────────────────────────────────────────────────────────


async def list_variables(
    *, org_id: str, version_id: str, statuses: tuple[str, ...] | None = None
) -> list[dict[str, Any]]:
    def _fetch() -> list[dict[str, Any]]:
        query = (
            _svc().table("doc_template_variables")
            .select("*")
            .eq("org_id", org_id)
            .eq("version_id", version_id)
        )
        if statuses:
            query = query.in_("status", list(statuses))
        return query.order("created_at").execute().data or []

    return await _run(_fetch)


async def list_slots(
    *, org_id: str, version_id: str, statuses: tuple[str, ...] | None = None
) -> list[dict[str, Any]]:
    """Slots with their variable's `internal_name` flattened onto each row as
    `variable`, which is the key the renderer and validation engine both use."""
    def _fetch() -> list[dict[str, Any]]:
        query = (
            _svc().table("doc_template_slots")
            .select("*, doc_template_variables(internal_name, display_name, data_type)")
            .eq("org_id", org_id)
            .eq("version_id", version_id)
        )
        if statuses:
            query = query.in_("status", list(statuses))
        return (
            query.order("paragraph_index").order("start_offset").execute().data or []
        )

    rows = await _run(_fetch)
    for row in rows:
        joined = row.pop("doc_template_variables", None) or {}
        row["variable"] = joined.get("internal_name")
        row["variable_label"] = joined.get("display_name")
        row["variable_type"] = joined.get("data_type")
    return rows


async def confirmed_schema(
    *, org_id: str, version_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The (variables, slots) a generation is allowed to use.

    Filtering to `confirmed` happens here, once, so that "what gets rendered" is
    decided in exactly one place rather than re-derived by every caller.
    """
    variables = await list_variables(
        org_id=org_id, version_id=version_id, statuses=(STATUS_CONFIRMED,)
    )
    slots = await list_slots(
        org_id=org_id, version_id=version_id, statuses=(STATUS_CONFIRMED,)
    )
    return variables, slots


# ── Persisting an analysis ─────────────────────────────────────────────────


async def save_analysis(
    *,
    org_id: str,
    version_id: str,
    variables: list[Any],
    slots: list[Any],
    source_file_sha256: str,
    paragraph_hashes: dict[int, str],
) -> dict[str, int]:
    """Write an analysis into `doc_template_variables` and `doc_template_slots`.

    `variables` are `AnalyzedVariable`s and `slots` are `AnalyzedSlot`s from
    `analysis.analyzer`. `paragraph_hashes` maps paragraph_index to the hash of
    that paragraph's run text, computed by the caller against the same parse the
    detector used — passing it in keeps this module free of docx knowledge.

    Returns counts for the audit trail. Idempotent: running it twice produces
    the same rows, and never disturbs a row HR has decided on.
    """
    existing_vars = {
        v["internal_name"]: v
        for v in await list_variables(org_id=org_id, version_id=version_id)
    }

    created_vars = 0
    refreshed_vars = 0
    var_ids: dict[str, str] = {}

    for variable in variables:
        name = variable.internal_name
        current = existing_vars.get(name)

        if current is None:
            row = {
                "org_id": org_id,
                "version_id": version_id,
                "internal_name": name,
                "display_name": variable.display_name,
                "description": variable.description or None,
                "data_type": variable.data_type,
                "is_required": variable.is_required,
                "example_value": variable.example_value or None,
                "confidence": variable.confidence,
                "status": STATUS_PROPOSED,
                "source": SOURCE_AI,
            }

            def _insert(row=row) -> dict[str, Any]:
                res = _svc().table("doc_template_variables").insert(row).execute()
                return (res.data or [{}])[0]

            inserted = await _run(_insert)
            var_ids[name] = inserted.get("id")
            created_vars += 1
            continue

        var_ids[name] = current["id"]

        # Already decided by a human — leave it entirely alone.
        if current["status"] in _HR_DECIDED:
            continue

        patch = {
            "display_name": variable.display_name,
            "description": variable.description or None,
            "data_type": variable.data_type,
            "is_required": variable.is_required,
            "example_value": variable.example_value or None,
            "confidence": variable.confidence,
        }
        patch = {k: v for k, v in patch.items() if k in _REFRESHABLE_VARIABLE_FIELDS}

        def _update(vid=current["id"], patch=patch) -> None:
            _svc().table("doc_template_variables").update(patch).eq("id", vid).execute()

        await _run(_update)
        refreshed_vars += 1

    # ── Slots ──────────────────────────────────────────────────────────────
    existing_slots = {
        (s["paragraph_index"], s["start_offset"], s["end_offset"]): s
        for s in await list_slots(org_id=org_id, version_id=version_id)
    }

    created_slots = 0
    refreshed_slots = 0

    for slot in slots:
        key = (slot.paragraph_index, slot.start_offset, slot.end_offset)
        current = existing_slots.get(key)
        variable_id = var_ids.get(slot.internal_name) if slot.internal_name else None

        if current is None:
            row = {
                "org_id": org_id,
                "version_id": version_id,
                "variable_id": variable_id,
                "paragraph_index": slot.paragraph_index,
                "paragraph_kind": slot.paragraph_kind,
                "paragraph_hash": paragraph_hashes.get(slot.paragraph_index, ""),
                "source_file_sha256": source_file_sha256,
                "start_offset": slot.start_offset,
                "end_offset": slot.end_offset,
                "action": slot.action,
                "original_text": slot.original_text or "",
                "context_before": slot.context_before or "",
                "context_after": slot.context_after or "",
                "confidence": slot.confidence,
                "status": STATUS_PROPOSED,
                "source": SOURCE_AI,
            }

            def _insert(row=row) -> None:
                _svc().table("doc_template_slots").insert(row).execute()

            await _run(_insert)
            created_slots += 1
            continue

        if current["status"] in _HR_DECIDED:
            continue

        patch = {
            "variable_id": variable_id,
            "confidence": slot.confidence,
            "original_text": slot.original_text or "",
            "context_before": slot.context_before or "",
            "context_after": slot.context_after or "",
            "paragraph_hash": paragraph_hashes.get(slot.paragraph_index, ""),
            "source_file_sha256": source_file_sha256,
        }

        def _update(sid=current["id"], patch=patch) -> None:
            _svc().table("doc_template_slots").update(patch).eq("id", sid).execute()

        await _run(_update)
        refreshed_slots += 1

    return {
        "variables_created": created_vars,
        "variables_refreshed": refreshed_vars,
        "slots_created": created_slots,
        "slots_refreshed": refreshed_slots,
    }


# ── HR decisions ───────────────────────────────────────────────────────────


async def cascade_slot_status(
    *, org_id: str, variable_id: str, status: str
) -> list[dict[str, Any]]:
    """Push a variable's decision onto every slot it fills.

    HR reviews *fields*, never individual positions: the builder shows a
    variable's slots read-only under "Where this appears" and offers one
    Confirm button for the field as a whole. A slot's own status is only ever
    set directly for a slot that has no variable yet — the "Unassigned
    positions" list, where HR either names the spot or dismisses it.

    So a mapped slot's status has to follow its variable's, and this is the one
    place that happens. Without it a fully reviewed template still produces no
    confirmed slots, `confirmed_schema` comes back empty, and every generation
    blocks on `no_confirmed_fields` — which the onboarding agent then reports as
    "no template uploaded" while the template sits there marked active.
    """
    patch = {"status": status}

    def _update() -> list[dict[str, Any]]:
        res = (
            _svc().table("doc_template_slots")
            .update(patch)
            .eq("org_id", org_id)
            .eq("variable_id", variable_id)
            .execute()
        )
        return res.data or []

    return await _run(_update)


async def update_variable(
    *,
    org_id: str,
    variable_id: str,
    patch: dict[str, Any],
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Apply an HR edit to a variable.

    `internal_name` is intentionally NOT editable. Slots reference variables by
    id, but mappings and the render context key on the name, so renaming in
    place would silently orphan every mapping configured against the old one.
    Renaming is: create the new variable, repoint the slots, delete the old.
    """
    allowed = {
        "display_name", "description", "data_type", "is_required",
        "default_value", "validation_rules", "aliases", "example_value", "status",
    }
    clean = {k: v for k, v in patch.items() if k in allowed}
    if not clean:
        return await _get_variable(org_id, variable_id)

    if clean.get("status") == STATUS_CONFIRMED:
        clean["confirmed_by"] = actor_user_id
        clean["confirmed_at"] = "now()"

    def _update() -> dict[str, Any] | None:
        res = (
            _svc().table("doc_template_variables")
            .update(clean)
            .eq("id", variable_id)
            .eq("org_id", org_id)
            .execute()
        )
        return (res.data or [None])[0]

    row = await _run(_update)
    if not row:
        raise SchemaNotFound(variable_id)

    if "status" in clean:
        await cascade_slot_status(
            org_id=org_id, variable_id=variable_id, status=clean["status"]
        )

    await audit.record(
        org_id=org_id,
        action=AUDIT_VARIABLE_UPDATED,
        entity_type=ENTITY_VARIABLE,
        entity_id=variable_id,
        actor_user_id=actor_user_id,
        payload={"fields": sorted(clean.keys())},
    )
    return row


async def _get_variable(org_id: str, variable_id: str) -> dict[str, Any]:
    def _fetch() -> dict[str, Any] | None:
        res = (
            _svc().table("doc_template_variables")
            .select("*")
            .eq("id", variable_id)
            .eq("org_id", org_id)
            .limit(1)
            .execute()
        )
        return (res.data or [None])[0]

    row = await _run(_fetch)
    if not row:
        raise SchemaNotFound(variable_id)
    return row


async def create_variable(
    *,
    org_id: str,
    version_id: str,
    internal_name: str,
    display_name: str,
    data_type: str = "text",
    is_required: bool = True,
    description: str | None = None,
    default_value: str | None = None,
    validation_rules: dict[str, Any] | None = None,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Add a variable by hand.

    The recall safety net: detection will never be perfect on arbitrary customer
    documents, and analysis can fail outright. HR must always be able to define
    a field themselves, which is why `analysis_status='failed'` is a supported
    state rather than a dead end.
    """
    row = {
        "org_id": org_id,
        "version_id": version_id,
        "internal_name": internal_name,
        "display_name": display_name,
        "description": description,
        "data_type": data_type,
        "is_required": is_required,
        "default_value": default_value,
        "validation_rules": validation_rules or {},
        "status": STATUS_CONFIRMED,
        "source": SOURCE_MANUAL,
        "confirmed_by": actor_user_id,
        "confirmed_at": "now()",
    }

    def _insert() -> dict[str, Any]:
        res = _svc().table("doc_template_variables").insert(row).execute()
        return (res.data or [{}])[0]

    created = await _run(_insert)
    await audit.record(
        org_id=org_id,
        action=AUDIT_VARIABLE_CREATED,
        entity_type=ENTITY_VARIABLE,
        entity_id=created.get("id"),
        actor_user_id=actor_user_id,
        payload={"internal_name": internal_name, "source": SOURCE_MANUAL},
    )
    return created


async def delete_variable(*, org_id: str, variable_id: str) -> None:
    """Remove a variable. Slots pointing at it fall back to NULL (ON DELETE SET
    NULL), which leaves them visibly unmapped rather than silently dropped.

    They are demoted to `proposed` first. `doc_template_slots_confirmed_var_check`
    forbids a confirmed slot with no variable, so deleting a confirmed field
    would otherwise fail on the constraint — and `proposed` is the honest state
    anyway: the spot is real, but nothing fills it any more.
    """
    await cascade_slot_status(
        org_id=org_id, variable_id=variable_id, status=STATUS_PROPOSED
    )

    def _delete() -> None:
        (
            _svc().table("doc_template_variables")
            .delete()
            .eq("id", variable_id)
            .eq("org_id", org_id)
            .execute()
        )

    await _run(_delete)


async def update_slot(
    *,
    org_id: str,
    slot_id: str,
    patch: dict[str, Any],
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Decide one position directly.

    Only reached for a slot with no variable — HR naming an unassigned spot or
    dismissing it. A mapped slot follows its variable instead, via
    `cascade_slot_status`.

    Who decided is recorded in `document_audit_logs`, not on the row: unlike
    `doc_template_variables`, this table carries no `confirmed_by` / `confirmed_at`.
    """
    allowed = {"variable_id", "status", "action"}
    clean = {k: v for k, v in patch.items() if k in allowed}
    if not clean:
        raise SchemaNotFound(slot_id)

    def _update() -> dict[str, Any] | None:
        res = (
            _svc().table("doc_template_slots")
            .update(clean)
            .eq("id", slot_id)
            .eq("org_id", org_id)
            .execute()
        )
        return (res.data or [None])[0]

    row = await _run(_update)
    if not row:
        raise SchemaNotFound(slot_id)

    status = clean.get("status")
    if status in (STATUS_CONFIRMED, STATUS_REJECTED):
        await audit.record(
            org_id=org_id,
            action=(
                AUDIT_SLOT_CONFIRMED if status == STATUS_CONFIRMED
                else AUDIT_SLOT_REJECTED
            ),
            entity_type=ENTITY_SLOT,
            entity_id=slot_id,
            actor_user_id=actor_user_id,
            payload={"variable_id": clean.get("variable_id") or row.get("variable_id")},
        )
    return row


async def create_slot(
    *,
    org_id: str,
    version_id: str,
    variable_id: str | None,
    paragraph_index: int,
    paragraph_kind: str,
    paragraph_hash: str,
    source_file_sha256: str,
    start_offset: int,
    end_offset: int,
    action: str,
    original_text: str = "",
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Add a fill-point by hand, for a blank detection could not see."""
    row = {
        "org_id": org_id,
        "version_id": version_id,
        "variable_id": variable_id,
        "paragraph_index": paragraph_index,
        "paragraph_kind": paragraph_kind,
        "paragraph_hash": paragraph_hash,
        "source_file_sha256": source_file_sha256,
        "start_offset": start_offset,
        "end_offset": end_offset,
        "action": action,
        "original_text": original_text,
        "status": STATUS_CONFIRMED if variable_id else STATUS_PROPOSED,
        "source": SOURCE_MANUAL,
    }

    def _insert() -> dict[str, Any]:
        res = _svc().table("doc_template_slots").insert(row).execute()
        return (res.data or [{}])[0]

    created = await _run(_insert)
    await audit.record(
        org_id=org_id,
        action=AUDIT_SLOT_CREATED,
        entity_type=ENTITY_SLOT,
        entity_id=created.get("id"),
        actor_user_id=actor_user_id,
        payload={"paragraph_index": paragraph_index, "source": SOURCE_MANUAL},
    )
    return created
