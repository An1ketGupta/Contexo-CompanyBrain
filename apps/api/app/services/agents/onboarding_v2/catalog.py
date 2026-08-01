"""The onboarding step catalog: which steps an org runs, and their per-run state.

Two tables, two different jobs. `onboarding_step_defs` is the org's catalog —
edited from settings, shared by every future run. `onboarding_run_steps` is a
snapshot of that catalog taken when a run starts, plus the live state of each
step in that run.

The snapshot is the point. Before this, `OnboardingV2Agent` re-read the org's
step toggles on every kick, so an admin flipping a switch could change what a
half-finished run did next. Copying the catalog per run fixes that: a candidate
is never asked for a document that was added to the checklist after they
finished uploading, and a run's history stays readable months later even if the
catalog has moved on. `step_def_id` is kept as provenance only — every field a
run needs is copied onto the run step, so nothing dereferences a row that HR may
since have edited or deleted.

During the shadow phase the agent still dispatches off `onboarding_runs.status`;
`sync_from_legacy_status()` keeps the step rows tracking that ladder so the new
model can be validated against real runs before anything depends on it.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.database import get_service_client
from app.observability import get_logger
from app.services import org_config

log = get_logger(__name__)

# Step kinds. `generate` renders a template through the document generation
# pipeline; `collect` asks the candidate to upload files; `system` is a step
# whose logic lives in the agent (BGV references, policy acknowledgement) and
# which the catalog only positions and enables.
KIND_GENERATE = "generate"
KIND_COLLECT = "collect"
KIND_SYSTEM = "system"

# Per-step lifecycle. Mirrors the CHECK in migration 107.
STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_GENERATING = "generating"
STATUS_PENDING_HR_REVIEW = "pending_hr_review"
STATUS_PENDING_SIGNATURE = "pending_signature"
STATUS_SUBMITTED = "submitted"
STATUS_PENDING_HR_APPROVAL = "pending_hr_approval"
STATUS_BLOCKED_MISSING_TEMPLATE = "blocked_missing_template"
STATUS_BLOCKED_TEMPLATE_DRIFT = "blocked_template_drift"
STATUS_SKIPPED = "skipped"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

# A step in one of these states needs nothing further from anyone.
TERMINAL_STEP_STATUSES = frozenset({STATUS_DONE, STATUS_SKIPPED})

# The pipeline as it was hardcoded before the catalog existed. Migration 107
# seeds exactly this for every org that already existed; this copy seeds orgs
# created afterwards. Keep the two in step — they describe the same default
# product behaviour, and an org whose catalog disagrees with its peers because
# of which side of a deploy it was created on is a support ticket nobody can
# diagnose.
#
# Signer roles record what the pipeline actually did, not an idealised version:
# the LOI is routed HR → candidate, the appointment bundle is candidate-only,
# and the induction pack is sent unsigned.
DEFAULT_STEP_DEFS: tuple[dict[str, Any], ...] = (
    {
        "step_key": "loi",
        "kind": KIND_GENERATE,
        "label": "Letter of intent",
        "description": (
            "Generate the LOI, route it for signature, and send it to the candidate."
        ),
        "document_type_key": "letter_of_intent",
        "bundle_key": None,
        "bundle_label": None,
        "position": 0,
        "signer_roles": ["hr", "candidate"],
        "locked": True,
        "legacy_toggle": None,
    },
    {
        "step_key": "bgv",
        "kind": KIND_SYSTEM,
        "label": "Background verification",
        "description": (
            "Ask the candidate for references, then email each one a verification form."
        ),
        "document_type_key": None,
        "bundle_key": None,
        "bundle_label": None,
        "position": 10,
        "signer_roles": [],
        "locked": False,
        "legacy_toggle": "bgv",
    },
    {
        "step_key": "appointment_letter",
        "kind": KIND_GENERATE,
        "label": "Appointment letter",
        "description": "Generate the appointment letter for HR review.",
        "document_type_key": "appointment_letter",
        "bundle_key": "appointment_bundle",
        "bundle_label": "Appointment letter + NDA",
        "position": 20,
        "signer_roles": ["candidate"],
        "locked": False,
        "legacy_toggle": "appointment_bundle",
    },
    {
        "step_key": "nda",
        "kind": KIND_GENERATE,
        "label": "NDA",
        "description": "Generate the NDA for HR review.",
        "document_type_key": "nda",
        "bundle_key": "appointment_bundle",
        "bundle_label": "Appointment letter + NDA",
        "position": 21,
        "signer_roles": ["candidate"],
        "locked": False,
        "legacy_toggle": "appointment_bundle",
    },
    {
        "step_key": "policies",
        "kind": KIND_SYSTEM,
        "label": "Policy acknowledgement",
        "description": (
            "Assign policies that need signing off and wait for the candidate "
            "to acknowledge."
        ),
        "document_type_key": None,
        "bundle_key": None,
        "bundle_label": None,
        "position": 30,
        "signer_roles": [],
        "locked": False,
        "legacy_toggle": "policies",
    },
    {
        "step_key": "induction",
        "kind": KIND_GENERATE,
        "label": "Induction",
        "description": "Generate the induction pack and email it to the candidate.",
        "document_type_key": "induction",
        "bundle_key": None,
        "bundle_label": None,
        "position": 40,
        "signer_roles": [],
        "locked": False,
        "legacy_toggle": "induction",
    },
)

# Where each legacy run status sits in the catalog, as (step_key, step_status).
# The shadow-phase bridge between the status ladder the agent still dispatches
# on and the step rows that will eventually replace it. Mirrors the `progress`
# CTE in migration 107.
#
# The blocked statuses are deliberately absent: the ladder cannot say which step
# stalled, only `blocked_template_kind` can, so they are resolved separately.
LEGACY_STATUS_STEP: dict[str, tuple[str, str]] = {
    "draft": ("loi", STATUS_GENERATING),
    "loi_generating": ("loi", STATUS_GENERATING),
    "loi_pending_hr_review": ("loi", STATUS_PENDING_HR_REVIEW),
    "loi_pending_hr_sign": ("loi", STATUS_PENDING_SIGNATURE),
    "loi_pending_esign_signature": ("loi", STATUS_PENDING_SIGNATURE),
    "loi_signed_uploaded": ("loi", STATUS_ACTIVE),
    "loi_sent_to_candidate": ("bgv", STATUS_PENDING),
    "awaiting_candidate_references": ("bgv", STATUS_ACTIVE),
    "bgv_pending": ("bgv", STATUS_ACTIVE),
    "bgv_complete": ("appointment_letter", STATUS_PENDING),
    "appointment_bundle_generating": ("appointment_letter", STATUS_GENERATING),
    "appointment_pending_hr_review": ("appointment_letter", STATUS_PENDING_HR_REVIEW),
    "appointment_sent_to_candidate": ("policies", STATUS_PENDING),
    "policies_assigned": ("policies", STATUS_ACTIVE),
    "policies_acknowledged": ("induction", STATUS_PENDING),
    "induction_generating": ("induction", STATUS_GENERATING),
    "induction_sent": ("induction", STATUS_DONE),
    "completed": ("induction", STATUS_DONE),
}

LEGACY_BLOCKED_STATUSES = frozenset(
    {STATUS_BLOCKED_MISSING_TEMPLATE, STATUS_BLOCKED_TEMPLATE_DRIFT}
)

# Runs whose status says nothing about how far they got. Reconstructing step
# state for these would write a history that never happened.
LEGACY_TERMINAL_STATUSES = frozenset({"failed", "cancelled"})

# Columns copied from a step def onto a run step. Named once so the snapshot
# and the tests can't drift apart.
_SNAPSHOT_FIELDS = (
    "step_key",
    "kind",
    "label",
    "document_type_key",
    "bundle_key",
    "bundle_label",
    "position",
    "signer_roles",
)


# ── Pure helpers ───────────────────────────────────────────────────────────


def bundle_siblings(
    steps: list[dict[str, Any]], step: dict[str, Any]
) -> list[dict[str, Any]]:
    """Every step asked, waited on and reviewed as one unit with `step`.

    A step with no `bundle_key` is its own bundle of one, so callers can treat
    the bundled and unbundled cases identically instead of branching on None.
    Ordered by position so the caller renders and generates them predictably.
    """
    bundle_key = step.get("bundle_key")
    if not bundle_key:
        return [step]
    return sorted(
        (s for s in steps if s.get("bundle_key") == bundle_key),
        key=lambda s: s.get("position") or 0,
    )


def next_actionable(steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The first step still needing something, in catalog order.

    None means every step is done or skipped — the run is finished.
    """
    for step in sorted(steps, key=lambda s: s.get("position") or 0):
        if step.get("status") not in TERMINAL_STEP_STATUSES:
            return step
    return None


def resolve_legacy_placement(
    *, status: str, blocked_template_kind: str | None
) -> tuple[str, str] | None:
    """Map a legacy run status onto (step_key, step_status).

    Returns None when the status cannot be placed — a terminal run, or a
    blocked run that never recorded which template stalled it. Callers skip
    those rather than guessing a position.
    """
    if status in LEGACY_TERMINAL_STATUSES:
        return None
    if status in LEGACY_BLOCKED_STATUSES:
        kind = (blocked_template_kind or "").strip().lower()
        return (kind, status) if kind else None
    return LEGACY_STATUS_STEP.get(status)


def apply_legacy_placement(
    steps: list[dict[str, Any]],
    *,
    step_key: str,
    step_status: str,
) -> dict[str, str]:
    """Statuses the whole run should have, given which step it is sitting on.

    Steps before the current one are done, later ones pending, and a disabled
    step stays skipped wherever it falls. Bundle siblings share the current
    step's status: the legacy ladder only ever named one member of the
    appointment bundle, but both are being reviewed.

    Returns {run_step_id: status}, containing only the rows that need changing.
    """
    by_key = {s["step_key"]: s for s in steps}
    current = by_key.get(step_key)
    if current is None:
        return {}

    current_position = current.get("position") or 0
    bundle_key = current.get("bundle_key")
    changes: dict[str, str] = {}

    for step in steps:
        if step.get("status") == STATUS_SKIPPED:
            continue
        # A collect step is not on the legacy ladder — the candidate finishing
        # it is what completes it, so passing its position must not mark it
        # done. Its state belongs to the gate.
        if step.get("kind") == KIND_COLLECT:
            continue
        if step["step_key"] == step_key or (
            bundle_key and step.get("bundle_key") == bundle_key
        ):
            resolved = step_status
        elif (step.get("position") or 0) < current_position:
            resolved = STATUS_DONE
        else:
            resolved = STATUS_PENDING
        if step.get("status") != resolved:
            changes[step["id"]] = resolved

    return changes


# ── Catalog reads / writes ─────────────────────────────────────────────────


async def get_step_defs(org_id: str) -> list[dict[str, Any]]:
    """The org's step catalog in pipeline order, seeding defaults if empty.

    Self-healing on read: an org created after migration 107 has no catalog
    until something asks for one, and the alternative — every caller checking
    whether the org was seeded — puts the same branch in a dozen places.
    """
    svc = get_service_client()

    def _fetch() -> list[dict[str, Any]]:
        res = (
            svc.table("onboarding_step_defs")
            .select("*")
            .eq("org_id", org_id)
            .order("position")
            .execute()
        )
        return res.data or []

    rows = await asyncio.to_thread(_fetch)
    if rows:
        return rows

    await seed_default_step_defs(org_id)
    return await asyncio.to_thread(_fetch)


async def seed_default_step_defs(org_id: str) -> None:
    """Write the default catalog for an org that has none.

    Idempotent by way of the (org_id, step_key) unique constraint: two workers
    racing on the same cold org both try, one loses the insert, and both then
    read the same six rows. Losing that race is not an error worth raising.
    """
    svc = get_service_client()
    payload = [
        {
            "org_id": org_id,
            "step_key": d["step_key"],
            "kind": d["kind"],
            "label": d["label"],
            "description": d["description"],
            "document_type_key": d["document_type_key"],
            "bundle_key": d["bundle_key"],
            "bundle_label": d["bundle_label"],
            "position": d["position"],
            "enabled": True,
            "signer_roles": d["signer_roles"],
            "locked": d["locked"],
        }
        for d in DEFAULT_STEP_DEFS
    ]
    try:
        await asyncio.to_thread(
            lambda: svc.table("onboarding_step_defs").insert(payload).execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.info("onboarding_catalog.seed_skipped", org_id=org_id, error=str(exc))


async def get_collect_items(step_def_id: str) -> list[dict[str, Any]]:
    """The checklist under a collect-kind step def, in display order."""
    svc = get_service_client()

    def _fetch() -> list[dict[str, Any]]:
        res = (
            svc.table("onboarding_collect_items")
            .select("*")
            .eq("step_def_id", step_def_id)
            .order("position")
            .execute()
        )
        return res.data or []

    return await asyncio.to_thread(_fetch)


# ── Catalog edits ──────────────────────────────────────────────────────────


class CatalogError(ValueError):
    """A catalog edit the caller may not make. Routers surface the message."""


def slugify_step_key(label: str, *, taken: set[str]) -> str:
    """A stable, collision-free key for an HR-named step or item.

    Keys are what run steps, storage paths and submissions are addressed by, so
    they have to survive the label being renamed later — which is why one is
    minted here at creation and never derived from the label again.
    """
    base = "".join(c if c.isalnum() else "_" for c in label.strip().lower())
    base = "_".join(part for part in base.split("_") if part) or "step"
    if base[0].isdigit():
        base = f"s_{base}"
    base = base[:48]
    if base not in taken:
        return base
    for n in range(2, 1000):
        candidate = f"{base}_{n}"
        if candidate not in taken:
            return candidate
    raise CatalogError("Could not derive a unique key for this step.")


async def _renumber(org_id: str, ordered_ids: list[str]) -> None:
    """Rewrite positions to 0, 10, 20, … in the given order.

    Spaced by ten so an insert has somewhere to land without a second pass, and
    rewritten wholesale rather than patched because a partial reorder is how
    two steps end up claiming the same slot.
    """
    svc = get_service_client()
    for index, step_id in enumerate(ordered_ids):
        await asyncio.to_thread(
            lambda i=index, sid=step_id: svc.table("onboarding_step_defs")
            .update({"position": i * 10})
            .eq("id", sid)
            .eq("org_id", org_id)
            .execute()
        )


async def create_collect_step(
    *,
    org_id: str,
    label: str,
    after_step_key: str | None,
    items: list[dict[str, Any]],
    bundle_key: str | None = None,
    bundle_label: str | None = None,
) -> dict[str, Any]:
    """Add a document-collection step, positioned after an existing one.

    `after_step_key` is how position is expressed to callers: an org thinks
    "ask for these before background verification", not "position 15". None
    puts the step first among the non-locked steps — never ahead of the LOI,
    which the pipeline requires to run first.
    """
    if not label.strip():
        raise CatalogError("Give the step a name.")
    if not items:
        raise CatalogError("A document collection step needs at least one document.")

    defs = await get_step_defs(org_id)
    taken = {d["step_key"] for d in defs}
    step_key = slugify_step_key(label, taken=taken)

    svc = get_service_client()
    created = await asyncio.to_thread(
        lambda: svc.table("onboarding_step_defs")
        .insert(
            {
                "org_id": org_id,
                "step_key": step_key,
                "kind": KIND_COLLECT,
                "label": label.strip(),
                "document_type_key": None,
                "bundle_key": bundle_key,
                "bundle_label": bundle_label,
                # Placed by the renumber below; any value would do here.
                "position": 999,
                "enabled": True,
                "signer_roles": [],
                "locked": False,
            }
        )
        .execute()
    )
    step = (created.data or [{}])[0]

    await replace_collect_items(org_id=org_id, step_def_id=step["id"], items=items)

    # Splice into the order. A locked step (the LOI) always leads, so an
    # unanchored insert lands after it rather than displacing it.
    ordered = [d["id"] for d in defs]
    if after_step_key:
        anchor = next((d for d in defs if d["step_key"] == after_step_key), None)
        if anchor is None:
            raise CatalogError(f"No step named '{after_step_key}' to place this after.")
        ordered.insert(ordered.index(anchor["id"]) + 1, step["id"])
    else:
        lead = sum(1 for d in defs if d.get("locked"))
        ordered.insert(lead, step["id"])
    await _renumber(org_id, ordered)

    return step


async def update_step(
    *,
    org_id: str,
    step_key: str,
    enabled: bool | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Rename or enable/disable one step."""
    defs = await get_step_defs(org_id)
    step = next((d for d in defs if d["step_key"] == step_key), None)
    if step is None:
        raise CatalogError(f"No step named '{step_key}'.")

    patch: dict[str, Any] = {}
    if enabled is not None:
        if step.get("locked") and not enabled:
            raise CatalogError(
                f"{step['label']} can't be turned off — every later step "
                "builds on what it produces."
            )
        patch["enabled"] = enabled
    if label is not None:
        if not label.strip():
            raise CatalogError("Give the step a name.")
        patch["label"] = label.strip()
    if not patch:
        return step

    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("onboarding_step_defs")
        .update(patch)
        .eq("id", step["id"])
        .eq("org_id", org_id)
        .execute()
    )

    # Bundle members are asked and reviewed as one unit, so they are enabled as
    # one unit too — a bundle with half its documents turned off has no
    # meaningful behaviour.
    if enabled is not None and step.get("bundle_key"):
        for sibling in defs:
            if (
                sibling.get("bundle_key") == step["bundle_key"]
                and sibling["id"] != step["id"]
            ):
                await asyncio.to_thread(
                    lambda sid=sibling["id"]: svc.table("onboarding_step_defs")
                    .update({"enabled": enabled})
                    .eq("id", sid)
                    .eq("org_id", org_id)
                    .execute()
                )

    if enabled is not None:
        await sync_legacy_toggles(org_id)

    return {**step, **patch}


async def delete_step(*, org_id: str, step_key: str) -> None:
    """Remove a step an org added. Built-in steps can only be disabled.

    Runs already underway are unaffected — they hold their own snapshot, and
    onboarding_run_steps.step_def_id is ON DELETE SET NULL precisely so a
    catalog edit cannot erase a step a candidate already acted on.
    """
    defs = await get_step_defs(org_id)
    step = next((d for d in defs if d["step_key"] == step_key), None)
    if step is None:
        raise CatalogError(f"No step named '{step_key}'.")
    if step.get("kind") != KIND_COLLECT:
        raise CatalogError(
            f"{step['label']} is part of the standard pipeline and can be "
            "turned off, but not removed."
        )

    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("onboarding_step_defs")
        .delete()
        .eq("id", step["id"])
        .eq("org_id", org_id)
        .execute()
    )
    await _renumber(org_id, [d["id"] for d in defs if d["id"] != step["id"]])


async def replace_collect_items(
    *, org_id: str, step_def_id: str, items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Set a collect step's checklist to exactly `items`.

    Item keys are preserved across an edit where the label is unchanged, so
    renaming "PAN card" to "PAN card (self-attested)" does not orphan the
    submissions already filed against it.
    """
    if not items:
        raise CatalogError("A document collection step needs at least one document.")

    existing = await get_collect_items(step_def_id)
    by_label = {i["label"].strip().lower(): i["item_key"] for i in existing}

    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("onboarding_collect_items")
        .delete()
        .eq("step_def_id", step_def_id)
        .execute()
    )

    taken: set[str] = set()
    rows: list[dict[str, Any]] = []
    for position, item in enumerate(items):
        label = str(item.get("label") or "").strip()
        if not label:
            raise CatalogError("Every document needs a name.")
        item_key = item.get("item_key") or by_label.get(label.lower())
        if not item_key or item_key in taken:
            item_key = slugify_step_key(label, taken=taken)
        taken.add(item_key)
        rows.append(
            {
                "org_id": org_id,
                "step_def_id": step_def_id,
                "item_key": item_key,
                "label": label,
                "help_text": item.get("help_text"),
                "required": bool(item.get("required", True)),
                "accepted_formats": item.get("accepted_formats")
                or ["pdf", "jpg", "jpeg", "png"],
                "position": position,
            }
        )

    await asyncio.to_thread(
        lambda: svc.table("onboarding_collect_items").insert(rows).execute()
    )
    return await get_collect_items(step_def_id)


async def sync_legacy_toggles(org_id: str) -> None:
    """Push the catalog's enabled flags back onto the four legacy booleans.

    Two things read "does this org run BGV" right now: the catalog, which the
    settings UI edits, and `organizations.metadata.onboarding_v2_steps`, which
    the agent still dispatches on. Until the step engine takes over dispatch
    the catalog is the source of truth and this keeps the old copy honest —
    otherwise turning a step off in the new UI would change what HR sees
    without changing what the pipeline does.
    """
    defs = await get_step_defs(org_id)
    by_key = {d["step_key"]: d for d in defs}

    def _on(*step_keys: str) -> bool:
        return any(by_key.get(k, {}).get("enabled", True) for k in step_keys)

    try:
        await org_config.update_onboarding_steps(
            org_id=org_id,
            bgv=_on("bgv"),
            appointment_bundle=_on("appointment_letter", "nda"),
            policies=_on("policies"),
            induction=_on("induction"),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "onboarding_catalog.legacy_toggle_sync_failed",
            org_id=org_id,
            error=str(exc),
        )


# ── Per-run snapshot ───────────────────────────────────────────────────────


async def get_run_steps(run_id: str) -> list[dict[str, Any]]:
    """This run's steps in pipeline order."""
    svc = get_service_client()

    def _fetch() -> list[dict[str, Any]]:
        res = (
            svc.table("onboarding_run_steps")
            .select("*")
            .eq("run_id", run_id)
            .order("position")
            .execute()
        )
        return res.data or []

    return await asyncio.to_thread(_fetch)


async def materialize_run_steps(
    *, org_id: str, run_id: str
) -> list[dict[str, Any]]:
    """Snapshot the org's catalog onto a run, once.

    Returns the run's steps either way, so callers can treat "just created" and
    "already there" alike. A disabled step is written as `skipped` rather than
    omitted: a run should be able to show what it did not do, and a step
    re-enabled mid-run must not silently appear in a pipeline that already
    passed its position.

    A collect step's checklist is snapshotted into `config.items` for the same
    reason the rest of the row is copied — the candidate answers the list as it
    stood when they were asked, not as HR later edited it.
    """
    existing = await get_run_steps(run_id)
    if existing:
        return existing

    step_defs = await get_step_defs(org_id)
    if not step_defs:
        log.warning("onboarding_catalog.no_step_defs", org_id=org_id, run_id=run_id)
        return []

    rows: list[dict[str, Any]] = []
    for sd in step_defs:
        config = dict(sd.get("config") or {})
        if sd.get("kind") == KIND_COLLECT:
            items = await get_collect_items(sd["id"])
            config["items"] = [
                {
                    "item_key": i["item_key"],
                    "label": i["label"],
                    "help_text": i.get("help_text"),
                    "required": i.get("required", True),
                    "accepted_formats": i.get("accepted_formats") or [],
                }
                for i in items
            ]
        rows.append(
            {
                "org_id": org_id,
                "run_id": run_id,
                "step_def_id": sd["id"],
                **{f: sd.get(f) for f in _SNAPSHOT_FIELDS},
                "config": config,
                "status": STATUS_PENDING if sd.get("enabled", True) else STATUS_SKIPPED,
            }
        )

    svc = get_service_client()
    try:
        await asyncio.to_thread(
            lambda: svc.table("onboarding_run_steps").insert(rows).execute()
        )
    except Exception as exc:  # noqa: BLE001
        # Another worker materialized first (unique on run_id, step_key). Fall
        # through to the read — its rows are as good as the ones we built.
        log.info(
            "onboarding_catalog.materialize_raced",
            run_id=run_id,
            error=str(exc),
        )

    return await get_run_steps(run_id)


async def get_submissions(run_id: str) -> list[dict[str, Any]]:
    """Everything the candidate has uploaded for this run."""
    svc = get_service_client()

    def _fetch() -> list[dict[str, Any]]:
        res = (
            svc.table("onboarding_collect_submissions")
            .select("*")
            .eq("run_id", run_id)
            .execute()
        )
        return res.data or []

    return await asyncio.to_thread(_fetch)


def required_items(step: dict[str, Any]) -> list[dict[str, Any]]:
    """The checklist entries that gate the run, from the step's snapshot."""
    items = (step.get("config") or {}).get("items") or []
    return [i for i in items if i.get("required", True)]


def is_collect_satisfied(
    step: dict[str, Any], submissions: list[dict[str, Any]]
) -> bool:
    """Has the candidate submitted everything this step requires?

    Submission, not approval. HR rejecting a blurry scan is handled out of
    band — blocking the pipeline on a review queue would mean a hire stalls
    because nobody opened the tab, which is not what the org asked for when
    they added a document to the checklist.
    """
    submitted = {
        s["item_key"] for s in submissions if s.get("run_step_id") == step.get("id")
    }
    return all(i["item_key"] in submitted for i in required_items(step))


def pending_collect_steps(
    steps: list[dict[str, Any]],
    submissions: list[dict[str, Any]],
    *,
    reached_position: int,
) -> list[dict[str, Any]]:
    """Collect steps the run has reached but the candidate hasn't finished.

    `reached_position` is where the run currently sits in the catalog. A
    collect step at or before that point is one the run has walked past, so it
    holds the pipeline until its required items are in; a step positioned later
    is simply not due yet.

    This is what makes a collect step's position mean something while the agent
    still dispatches off the legacy status ladder: the ladder decides what to
    do next, and these gates decide whether it may proceed.
    """
    return [
        step
        for step in sorted(steps, key=lambda s: s.get("position") or 0)
        if step.get("kind") == KIND_COLLECT
        and step.get("status") not in TERMINAL_STEP_STATUSES
        and (step.get("position") or 0) <= reached_position
        and not is_collect_satisfied(step, submissions)
    ]


async def set_step_status(
    run_step_id: str,
    status: str,
    *,
    blocked_reason: str | None = None,
) -> None:
    """Move one step, stamping the timestamp its new state implies."""
    payload: dict[str, Any] = {"status": status, "blocked_reason": blocked_reason}
    now = datetime.now(UTC).isoformat()
    if status in TERMINAL_STEP_STATUSES:
        payload["completed_at"] = now
    elif status not in (STATUS_PENDING,):
        payload["started_at"] = now

    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("onboarding_run_steps")
        .update(payload)
        .eq("id", run_step_id)
        .execute()
    )


async def sync_from_legacy_status(
    *,
    org_id: str,
    run_id: str,
    status: str,
    blocked_template_kind: str | None = None,
    blocked_reason: str | None = None,
) -> None:
    """Track the legacy status ladder onto this run's step rows.

    The shadow-phase bridge: the agent still decides what to do from
    `onboarding_runs.status`, and this mirrors each transition onto the step
    model so it can be checked against real runs before anything dispatches on
    it. Best-effort throughout — a run must never fail because its shadow
    bookkeeping did.
    """
    try:
        placement = resolve_legacy_placement(
            status=status, blocked_template_kind=blocked_template_kind
        )
        if placement is None:
            return

        step_key, step_status = placement
        steps = await materialize_run_steps(org_id=org_id, run_id=run_id)
        if not steps:
            return

        changes = apply_legacy_placement(
            steps, step_key=step_key, step_status=step_status
        )
        for run_step_id, resolved in changes.items():
            await set_step_status(
                run_step_id,
                resolved,
                blocked_reason=(
                    blocked_reason if resolved in LEGACY_BLOCKED_STATUSES else None
                ),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "onboarding_catalog.shadow_sync_failed",
            run_id=run_id,
            status=status,
            error=str(exc),
        )
