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
KINDS = (KIND_GENERATE, KIND_COLLECT, KIND_SYSTEM)

# What a system-kind step does. Held separately from `step_key` so an org can
# rename "Background verification" to "Reference checks", or run two of them at
# different points, without changing which handler executes.
SYSTEM_ACTION_BGV = "bgv"
SYSTEM_ACTION_POLICIES = "policies"
SYSTEM_ACTIONS = (SYSTEM_ACTION_BGV, SYSTEM_ACTION_POLICIES)

# Who can be asked to sign a generated document. Routing order is the order of
# the list: signer_roles[0] signs before signer_roles[1]. An empty list means
# the document is sent, not signed.
SIGNER_HR = "hr"
SIGNER_CANDIDATE = "candidate"
SIGNER_ROLES = (SIGNER_HR, SIGNER_CANDIDATE)

# The letter of intent's document type. The LOI review endpoints and the panel
# they serve predate the catalog and were written against the literal step key
# `loi`; an org that renamed or re-created that step renders the same document
# under a different key, so it is addressed by type instead.
DOCUMENT_TYPE_LOI = "letter_of_intent"
DOCUMENT_TYPE_INDUCTION = "induction"

# The seeded appointment letter + NDA pair. Only this bundle gets the bespoke
# "AL + NDA are ready" copy — a bundle an org composed itself is named and
# described by its own label instead.
BUNDLE_APPOINTMENT = "appointment_bundle"

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

# HR's verdict on the current round of candidate work. NULL until they look.
REVIEW_APPROVED = "approved"
REVIEW_REJECTED = "rejected"
REVIEW_DECISIONS = (REVIEW_APPROVED, REVIEW_REJECTED)

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
        "system_action": None,
        "locked": False,
        "requires_hr_approval": True,
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
        "system_action": SYSTEM_ACTION_BGV,
        "locked": False,
        "requires_hr_approval": True,
        "legacy_toggle": "bgv",
    },
    {
        "step_key": "appointment_letter",
        "kind": KIND_GENERATE,
        "label": "Appointment letter",
        "description": "Generate the appointment letter for HR review.",
        "document_type_key": "appointment_letter",
        "bundle_key": BUNDLE_APPOINTMENT,
        "bundle_label": "Appointment letter + NDA",
        "position": 20,
        "signer_roles": ["candidate"],
        "system_action": None,
        "locked": False,
        "requires_hr_approval": True,
        "legacy_toggle": "appointment_bundle",
    },
    {
        "step_key": "nda",
        "kind": KIND_GENERATE,
        "label": "NDA",
        "description": "Generate the NDA for HR review.",
        "document_type_key": "nda",
        "bundle_key": BUNDLE_APPOINTMENT,
        "bundle_label": "Appointment letter + NDA",
        "position": 21,
        "signer_roles": ["candidate"],
        "system_action": None,
        "locked": False,
        "requires_hr_approval": True,
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
        "system_action": SYSTEM_ACTION_POLICIES,
        "locked": False,
        "requires_hr_approval": True,
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
        "system_action": None,
        "locked": False,
        "requires_hr_approval": True,
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
    "system_action",
    "requires_hr_approval",
)

# What to write to `onboarding_runs.status` for a given (step_key, step status).
# The step rows are authoritative; this exists so the runs list, the stage board
# and the run-detail page keep reading the vocabulary they were built against
# while a built-in step is running.
#
# Inverting LEGACY_STATUS_STEP would not do: that map is many-to-one (four LOI
# statuses collapse onto two step states), and it records where a run *is*
# rather than what to call it. These are the labels the old ladder would have
# written at each point.
LEGACY_STATUS_FOR_STEP: dict[tuple[str, str], str] = {
    ("loi", STATUS_GENERATING): "loi_generating",
    ("loi", STATUS_PENDING_HR_REVIEW): "loi_pending_hr_review",
    ("loi", STATUS_PENDING_SIGNATURE): "loi_pending_hr_sign",
    ("loi", STATUS_DONE): "loi_sent_to_candidate",
    ("bgv", STATUS_ACTIVE): "bgv_pending",
    ("bgv", STATUS_DONE): "bgv_complete",
    ("appointment_letter", STATUS_GENERATING): "appointment_bundle_generating",
    ("appointment_letter", STATUS_PENDING_HR_REVIEW): "appointment_pending_hr_review",
    ("appointment_letter", STATUS_DONE): "appointment_sent_to_candidate",
    ("nda", STATUS_GENERATING): "appointment_bundle_generating",
    ("nda", STATUS_PENDING_HR_REVIEW): "appointment_pending_hr_review",
    ("nda", STATUS_DONE): "appointment_sent_to_candidate",
    ("policies", STATUS_ACTIVE): "policies_assigned",
    ("policies", STATUS_DONE): "policies_acknowledged",
    ("induction", STATUS_GENERATING): "induction_generating",
    ("induction", STATUS_DONE): "induction_sent",
}

# The label a step that has no legacy equivalent reports. Generic on purpose:
# an org-named step has no place in a vocabulary that predates it, and inventing
# `joining_documents_pending_hr_review` per org is how the CHECK constraint got
# widened five times in the first place.
GENERIC_RUN_STATUS: dict[str, str] = {
    STATUS_PENDING: "step_active",
    STATUS_ACTIVE: "step_active",
    STATUS_GENERATING: "step_generating",
    STATUS_PENDING_HR_REVIEW: "step_pending_hr_review",
    STATUS_PENDING_SIGNATURE: "step_pending_signature",
    STATUS_SUBMITTED: "step_active",
    STATUS_PENDING_HR_APPROVAL: "step_pending_hr_approval",
    STATUS_BLOCKED_MISSING_TEMPLATE: "blocked_missing_template",
    STATUS_BLOCKED_TEMPLATE_DRIFT: "blocked_template_drift",
    STATUS_FAILED: "failed",
}


def run_status_for(step: dict[str, Any], step_status: str) -> str:
    """What `onboarding_runs.status` should say while `step` is in this state.

    Built-in steps keep their historical label so nothing downstream has to
    learn a new vocabulary; a collect step reports that it is waiting on the
    candidate; anything else falls back to a generic per-state label.

    Waiting on HR outranks both. A step sitting in the approval gate is not
    waiting on the candidate — they have done their part — and no legacy label
    exists for a state the old ladder had no concept of, so the generic one is
    the only honest thing the list view can say.
    """
    if step_status == STATUS_PENDING_HR_APPROVAL:
        return GENERIC_RUN_STATUS[STATUS_PENDING_HR_APPROVAL]
    legacy = LEGACY_STATUS_FOR_STEP.get((step.get("step_key") or "", step_status))
    if legacy:
        return legacy
    if step.get("kind") == KIND_COLLECT and step_status not in TERMINAL_STEP_STATUSES:
        return "awaiting_candidate_documents"
    return GENERIC_RUN_STATUS.get(step_status, "step_active")


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


# ── The HR approval gate ───────────────────────────────────────────────────
#
# Three kinds of step let the candidate act, and each parks in
# `pending_hr_approval` afterwards so HR can look before the run carries on:
# a signed document, a completed upload checklist, and the referee list a BGV
# step gathers. The predicates below decide when the gate applies; the agent
# decides what to do on either side of it.


def gate_applies(step: dict[str, Any]) -> bool:
    """Does this step stop for HR once the candidate has acted?

    Off entirely when the org turned the toggle off for this step. Otherwise it
    depends on whether the candidate does anything here at all:

      collect  — always; the whole step is the candidate uploading files.
      system   — background verification only. A policy step's acknowledgement
                 is a signature on a record HR already wrote, not a document
                 they need to inspect, and gating it would park every hire on a
                 tick-box.
      generate — only when the candidate is one of the signers. A document HR
                 signs alone, or one sent unsigned, has nothing of the
                 candidate's in it for HR to accept or reject; the draft review
                 they already do is the whole check.
    """
    if not step.get("requires_hr_approval", False):
        return False
    kind = step.get("kind")
    if kind == KIND_COLLECT:
        return True
    if kind == KIND_SYSTEM:
        return step.get("system_action") == SYSTEM_ACTION_BGV
    return SIGNER_CANDIDATE in (step.get("signer_roles") or [])


def gate_cleared(step: dict[str, Any]) -> bool:
    """May the run move past this step's gate?

    True when the gate does not apply, or when HR has approved this round.
    `review_decision` is reset to NULL every time the candidate acts again, so
    an approval never carries over to work HR has not seen.
    """
    return not gate_applies(step) or step.get("review_decision") == REVIEW_APPROVED


def find_document_step(
    steps: list[dict[str, Any]], document_type_key: str
) -> dict[str, Any] | None:
    """The step that renders a given document type, earliest one wins.

    How anything outside the agent finds "the LOI" now that `step_key` is
    whatever the org named it. A catalog may legitimately render the same type
    twice — a second NDA later in the pipeline — so this returns the first by
    position rather than refusing to choose.
    """
    matched = [
        s
        for s in steps
        if s.get("kind") == KIND_GENERATE
        and s.get("document_type_key") == document_type_key
    ]
    if not matched:
        return None
    return min(matched, key=lambda s: s.get("position") or 0)


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
            "system_action": d["system_action"],
            "locked": d["locked"],
            "requires_hr_approval": d["requires_hr_approval"],
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


def normalize_signer_roles(roles: list[str] | None) -> list[str]:
    """Clean a signer list, preserving the caller's routing order.

    Order matters — it is who signs first — so this de-duplicates in place
    rather than sorting. An unknown role is rejected outright: silently
    dropping one would produce a document nobody was asked to sign, which
    looks identical to a document deliberately sent unsigned.
    """
    cleaned: list[str] = []
    for role in roles or []:
        key = str(role).strip().lower()
        if not key:
            continue
        if key not in SIGNER_ROLES:
            raise CatalogError(
                f"'{role}' can't sign a document. Choose HR, the candidate, or both."
            )
        if key not in cleaned:
            cleaned.append(key)
    return cleaned


async def _splice(
    org_id: str,
    defs: list[dict[str, Any]],
    new_ids: list[str],
    after_step_key: str | None,
) -> None:
    """Place newly created steps into the order, then renumber everything.

    `after_step_key` is how position is expressed to callers: an org thinks
    "ask for these before background verification", not "position 15". None
    puts them first — nothing is pinned ahead of them any more.
    """
    ordered = [d["id"] for d in defs]
    if after_step_key:
        anchor = next((d for d in defs if d["step_key"] == after_step_key), None)
        if anchor is None:
            raise CatalogError(f"No step named '{after_step_key}' to place this after.")
        at = ordered.index(anchor["id"]) + 1
    else:
        at = 0
    ordered[at:at] = new_ids
    await _renumber(org_id, ordered)


async def create_step(
    *,
    org_id: str,
    kind: str,
    label: str,
    after_step_key: str | None = None,
    items: list[dict[str, Any]] | None = None,
    document_type_keys: list[str] | None = None,
    signer_roles: list[str] | None = None,
    system_action: str | None = None,
    requires_hr_approval: bool = True,
) -> list[dict[str, Any]]:
    """Add a step of any kind, positioned after an existing one.

    Returns a list because one request can create several rows: picking more
    than one document type produces a bundle — several `generate` steps sharing
    a `bundle_key`, generated together, reviewed together and signed in one
    envelope. That is how "appointment letter + NDA" was always modelled; this
    just lets an org compose its own.
    """
    label = label.strip()
    if not label:
        raise CatalogError("Give the step a name.")
    if kind not in KINDS:
        raise CatalogError(f"'{kind}' is not a kind of step.")

    defs = await get_step_defs(org_id)
    taken = {d["step_key"] for d in defs}
    svc = get_service_client()

    if kind == KIND_COLLECT:
        rows = [_collect_row(org_id, label, taken)]
    elif kind == KIND_SYSTEM:
        rows = [_system_row(org_id, label, system_action, taken)]
    else:
        rows = _generate_rows(
            org_id, label, document_type_keys, normalize_signer_roles(signer_roles), taken
        )
    for row in rows:
        row["requires_hr_approval"] = requires_hr_approval

    created = await asyncio.to_thread(
        lambda: svc.table("onboarding_step_defs").insert(rows).execute()
    )
    steps = created.data or []
    if not steps:
        raise CatalogError("Couldn't save the step. Try again.")

    if kind == KIND_COLLECT:
        await replace_collect_items(
            org_id=org_id, step_def_id=steps[0]["id"], items=items or []
        )

    await _splice(org_id, defs, [s["id"] for s in steps], after_step_key)
    return steps


def _collect_row(org_id: str, label: str, taken: set[str]) -> dict[str, Any]:
    return {
        "org_id": org_id,
        "step_key": slugify_step_key(label, taken=taken),
        "kind": KIND_COLLECT,
        "label": label,
        "document_type_key": None,
        "bundle_key": None,
        "bundle_label": None,
        # Placed by the splice above; any value would do here.
        "position": 999,
        "enabled": True,
        "signer_roles": [],
        "system_action": None,
        "locked": False,
    }


def _system_row(
    org_id: str, label: str, system_action: str | None, taken: set[str]
) -> dict[str, Any]:
    if system_action not in SYSTEM_ACTIONS:
        raise CatalogError(
            "Choose what this step does: a background check, or a policy sign-off."
        )
    return {
        "org_id": org_id,
        "step_key": slugify_step_key(label, taken=taken),
        "kind": KIND_SYSTEM,
        "label": label,
        "document_type_key": None,
        "bundle_key": None,
        "bundle_label": None,
        "position": 999,
        "enabled": True,
        "signer_roles": [],
        "system_action": system_action,
        "locked": False,
    }


def _generate_rows(
    org_id: str,
    label: str,
    document_type_keys: list[str] | None,
    signer_roles: list[str],
    taken: set[str],
) -> list[dict[str, Any]]:
    """One row per document type, bundled together when there is more than one.

    The bundle key is minted from the step's name and reserved in `taken` the
    same way step keys are, so a second bundle called "Joining paperwork" cannot
    silently merge into the first.
    """
    keys = [k.strip() for k in (document_type_keys or []) if k and k.strip()]
    if not keys:
        raise CatalogError("Choose at least one document to send.")
    # Deduplicate: sending the same template twice in one step is never intended
    # and would produce two rows fighting over the same onboarding_documents.kind.
    seen: set[str] = set()
    keys = [k for k in keys if not (k in seen or seen.add(k))]

    if len(keys) == 1:
        return [
            {
                "org_id": org_id,
                "step_key": slugify_step_key(label, taken=taken),
                "kind": KIND_GENERATE,
                "label": label,
                "document_type_key": keys[0],
                "bundle_key": None,
                "bundle_label": None,
                "position": 999,
                "enabled": True,
                "signer_roles": signer_roles,
                "system_action": None,
                "locked": False,
            }
        ]

    bundle_key = slugify_step_key(f"{label} bundle", taken=taken)
    taken.add(bundle_key)
    rows: list[dict[str, Any]] = []
    for key in keys:
        step_key = slugify_step_key(key, taken=taken)
        taken.add(step_key)
        rows.append(
            {
                "org_id": org_id,
                "step_key": step_key,
                "kind": KIND_GENERATE,
                "label": key.replace("_", " ").title(),
                "document_type_key": key,
                "bundle_key": bundle_key,
                "bundle_label": label,
                "position": 999,
                "enabled": True,
                "signer_roles": signer_roles,
                "system_action": None,
                "locked": False,
            }
        )
    return rows


async def create_collect_step(
    *,
    org_id: str,
    label: str,
    after_step_key: str | None,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Add a document-collection step. Thin wrapper over `create_step`."""
    if not items:
        raise CatalogError("A document collection step needs at least one document.")
    steps = await create_step(
        org_id=org_id,
        kind=KIND_COLLECT,
        label=label,
        after_step_key=after_step_key,
        items=items,
    )
    return steps[0]


async def update_step(
    *,
    org_id: str,
    step_key: str,
    enabled: bool | None = None,
    label: str | None = None,
    signer_roles: list[str] | None = None,
    requires_hr_approval: bool | None = None,
    after_step_key: str | None = None,
    move: str | None = None,
) -> dict[str, Any]:
    """Rename, enable/disable, re-sign or re-position one step.

    `move` is 'up' or 'down' — one place in the pipeline, which is how the
    settings list drives it. `after_step_key` places the step explicitly and
    wins if both are given. A bundle moves as a unit: its members are adjacent
    by construction and reordering one past its own sibling would split it.
    """
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
        # Renaming a bundle member renames the bundle, not the member: the
        # settings list shows one row per bundle, so that row's name is the one
        # being edited.
        if step.get("bundle_key"):
            patch["bundle_label"] = label.strip()
        else:
            patch["label"] = label.strip()
    if signer_roles is not None:
        if step.get("kind") != KIND_GENERATE:
            raise CatalogError(
                f"{step['label']} doesn't produce a document, so nobody signs it."
            )
        patch["signer_roles"] = normalize_signer_roles(signer_roles)
    if requires_hr_approval is not None:
        patch["requires_hr_approval"] = requires_hr_approval

    if after_step_key is not None or move is not None:
        await _reposition(org_id, defs, step, after_step_key=after_step_key, move=move)
        defs = await get_step_defs(org_id)

    if not patch:
        return next((d for d in defs if d["step_key"] == step_key), step)

    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("onboarding_step_defs")
        .update(patch)
        .eq("id", step["id"])
        .eq("org_id", org_id)
        .execute()
    )

    # A bundle is asked, reviewed and signed as one unit, so everything except
    # a member's own label applies to all of it. A bundle with half its
    # documents turned off, or with the NDA routed to a different signer than
    # the appointment letter it ships with, has no meaningful behaviour.
    shared = {k: v for k, v in patch.items() if k != "label"}
    if shared and step.get("bundle_key"):
        for sibling in defs:
            if (
                sibling.get("bundle_key") == step["bundle_key"]
                and sibling["id"] != step["id"]
            ):
                await asyncio.to_thread(
                    lambda sid=sibling["id"]: svc.table("onboarding_step_defs")
                    .update(shared)
                    .eq("id", sid)
                    .eq("org_id", org_id)
                    .execute()
                )

    if enabled is not None:
        await sync_legacy_toggles(org_id)

    return {**step, **patch}


async def _reposition(
    org_id: str,
    defs: list[dict[str, Any]],
    step: dict[str, Any],
    *,
    after_step_key: str | None = None,
    move: str | None = None,
) -> None:
    """Move a step (or its whole bundle) to a new place in the pipeline."""
    groups: list[list[dict[str, Any]]] = []
    seen: set[str] = set()
    for d in defs:
        key = d.get("bundle_key")
        if not key:
            groups.append([d])
            continue
        if key in seen:
            continue
        seen.add(key)
        groups.append([s for s in defs if s.get("bundle_key") == key])

    index = next(
        (i for i, g in enumerate(groups) if any(s["id"] == step["id"] for s in g)),
        None,
    )
    if index is None:
        return

    group = groups.pop(index)
    if after_step_key:
        anchor = next(
            (
                i
                for i, g in enumerate(groups)
                if any(s["step_key"] == after_step_key for s in g)
            ),
            None,
        )
        if anchor is None:
            raise CatalogError(f"No step named '{after_step_key}' to place this after.")
        target = anchor + 1
    elif move == "up":
        target = max(0, index - 1)
    elif move == "down":
        target = min(len(groups), index + 1)
    else:
        raise CatalogError("Say where to move the step.")

    groups.insert(target, group)
    await _renumber(org_id, [s["id"] for g in groups for s in g])


async def delete_step(*, org_id: str, step_key: str) -> None:
    """Remove a step from the catalog, bundle and all.

    Any step may go. The pipeline is whatever the org composed, so there is no
    longer such a thing as a step that is part of "the standard flow" and may
    only be disabled — an org that does not send an NDA should not have to look
    at a switched-off NDA row forever.

    Runs already underway are unaffected — they hold their own snapshot, and
    onboarding_run_steps.step_def_id is ON DELETE SET NULL precisely so a
    catalog edit cannot erase a step a candidate already acted on.
    """
    defs = await get_step_defs(org_id)
    step = next((d for d in defs if d["step_key"] == step_key), None)
    if step is None:
        raise CatalogError(f"No step named '{step_key}'.")
    if step.get("locked"):
        raise CatalogError(
            f"{step['label']} can't be removed — every later step builds on "
            "what it produces."
        )

    # Deleting one member of a bundle would leave the rest generated, reviewed
    # and signed as a bundle of one, which is not what "remove this step" means
    # to whoever clicked it.
    doomed = {s["id"] for s in bundle_siblings(defs, step)}

    svc = get_service_client()
    for step_id in doomed:
        await asyncio.to_thread(
            lambda sid=step_id: svc.table("onboarding_step_defs")
            .delete()
            .eq("id", sid)
            .eq("org_id", org_id)
            .execute()
        )
    await _renumber(org_id, [d["id"] for d in defs if d["id"] not in doomed])
    await sync_legacy_toggles(org_id)


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

    def _any(predicate: Any) -> bool:
        matched = [d for d in defs if predicate(d)]
        return any(d.get("enabled", True) for d in matched) if matched else False

    # Matched on what a step *does*, not what it is called: an org may rename
    # "Background verification" or add a second one, and the legacy boolean is
    # supposed to mean "does this org run reference checks at all".
    try:
        await org_config.update_onboarding_steps(
            org_id=org_id,
            bgv=_any(lambda d: d.get("system_action") == SYSTEM_ACTION_BGV),
            appointment_bundle=_any(
                lambda d: d.get("document_type_key") in ("appointment_letter", "nda")
            ),
            policies=_any(lambda d: d.get("system_action") == SYSTEM_ACTION_POLICIES),
            induction=_any(lambda d: d.get("document_type_key") == "induction"),
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
    """Has the candidate filed everything this step requires?

    A file HR sent back does not count. It used to: the row stayed in place
    with `review_status = 'rejected'`, the checklist read as complete, and the
    "please re-upload this" ask had nothing holding it open — the run had
    already walked past the step by then anyway.

    This says the checklist is *answered*, not that HR has accepted it. The
    approval gate is the separate question `gate_cleared` asks.
    """
    filed = {
        s["item_key"]
        for s in submissions
        if s.get("run_step_id") == step.get("id")
        and s.get("review_status") != REVIEW_REJECTED
    }
    return all(i["item_key"] in filed for i in required_items(step))


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
    extra: dict[str, Any] | None = None,
) -> None:
    """Move one step, stamping the timestamp its new state implies.

    `extra` carries the review columns when a transition is a decision — HR
    approving or rejecting moves the step and records who said so, and the two
    have to land in one write or a crash between them leaves a step advanced
    with nobody's name against it.
    """
    payload: dict[str, Any] = {"status": status, "blocked_reason": blocked_reason}
    now = datetime.now(UTC).isoformat()
    if status in TERMINAL_STEP_STATUSES:
        payload["completed_at"] = now
    elif status not in (STATUS_PENDING,):
        payload["started_at"] = now
    if extra:
        payload.update(extra)

    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("onboarding_run_steps")
        .update(payload)
        .eq("id", run_step_id)
        .execute()
    )


async def clear_step_review(run_step_id: str) -> None:
    """Reopen the gate because the candidate has acted again.

    Called when a replacement document is uploaded, a fresh referee list is
    submitted, or a re-signed envelope completes. Without this a step HR
    approved once would stay approved through every later resubmission, which
    is the exact hole the gate exists to close.
    """
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("onboarding_run_steps")
        .update({"review_decision": None, "review_note": None})
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
