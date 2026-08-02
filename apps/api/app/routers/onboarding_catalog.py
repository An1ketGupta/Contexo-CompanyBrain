"""The onboarding step catalog, and the candidate portal that hangs off it.

Three audiences, one table. HR edits the catalog (`/onboarding/catalog/*`);
the candidate sees and answers the document asks it produces
(`/onboarding/candidate/*`); HR reviews what came back
(`/onboarding/runs/{id}/submissions/*`).

The candidate routes here are authenticated, for a candidate who does have a
session — they land on the same screen from the dashboard once they have signed
in. The identical view and upload served against a URL token, for the candidate
who has not, lives in `onboarding_public.py`; both call `build_candidate_view`
and `store_candidate_upload` below, so a rule about what may be uploaded when is
written once and holds on either path.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import inngest
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.inngest.client import get_inngest_client
from app.models.onboarding_catalog import (
    ALLOWED_UPLOAD_FORMATS,
    CandidateItemRead,
    CandidateOnboardingRead,
    CandidateStepRead,
    CatalogRead,
    CreateStepRequest,
    DocumentTypeRead,
    ReplaceItemsRequest,
    ReviewSubmissionRequest,
    StepRead,
    SubmissionRead,
    UpdateStepRequest,
    catalog_step_to_read,
)
from app.observability import get_logger
from app.services import org_config
from app.services.agents.onboarding_v2 import catalog as ob_catalog
from app.services.agents.onboarding_v2 import storage as ob_storage
from app.services.documents import templates as doc_templates

log = get_logger(__name__)

router = APIRouter(prefix="/onboarding", tags=["onboarding-catalog"])

# Uploads are scans and photos of paperwork. 25MB matches the cap the signed-LOI
# upload already enforces, so the two paths reject the same oversized file.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _require_user(current_user: dict) -> tuple[str, str, str]:
    user_id = current_user.get("user_id")
    org_id = current_user.get("org_id")
    token = current_user.get("token")
    if not user_id or not org_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return user_id, org_id, token


async def _require_admin(user_id: str, token: str) -> None:
    client = get_user_client(token)
    me = await asyncio.to_thread(
        lambda: client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not me.data or me.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can change the onboarding flow.",
        )


def _catalog_error(exc: ob_catalog.CatalogError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


async def _read_catalog(org_id: str) -> CatalogRead:
    defs = await ob_catalog.get_step_defs(org_id)
    steps: list[StepRead] = []
    for d in defs:
        items = (
            await ob_catalog.get_collect_items(d["id"])
            if d["kind"] == ob_catalog.KIND_COLLECT
            else []
        )
        steps.append(catalog_step_to_read(d, items))
    configured = (await org_config.get_onboarding_steps(org_id)).configured
    return CatalogRead(
        steps=steps,
        configured=configured,
        document_types=await _document_types(org_id),
    )


async def _document_types(org_id: str) -> list[DocumentTypeRead]:
    """What an org can pick from when adding a "send official documents" step.

    The system types plus the org's own, each flagged with whether a default
    template actually exists — configuring the flow before uploading the
    paperwork is a normal order to work in, but a step whose template is
    missing blocks the run when it reaches it, so the picker says so up front.
    """
    try:
        types = await doc_templates.list_document_types(org_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("onboarding_catalog.document_types_failed err=%s", exc)
        return []

    out: list[DocumentTypeRead] = []
    for t in types:
        try:
            resolved = await doc_templates.resolve_default_version(
                org_id=org_id, type_key=t["key"]
            )
        except Exception:  # noqa: BLE001
            resolved = None
        out.append(
            DocumentTypeRead(
                key=t["key"],
                label=t.get("label") or t["key"],
                description=t.get("description"),
                has_template=resolved is not None,
            )
        )
    return out


# ── HR: the catalog ─────────────────────────────────────────────────────────


@router.get("/catalog", response_model=CatalogRead)
async def get_catalog(current_user: dict = Depends(verify_jwt)) -> CatalogRead:
    """The org's onboarding steps in pipeline order."""
    _, org_id, _ = _require_user(current_user)
    return await _read_catalog(org_id)


@router.post("/catalog/confirm", response_model=CatalogRead)
async def confirm_catalog(current_user: dict = Depends(verify_jwt)) -> CatalogRead:
    """Admin-only. Mark the org's flow as decided.

    An org that has accepted the defaults and one that has never opened the
    setup screen look identical in the catalog — both run everything. This is
    what tells them apart, so the first-run screen stops appearing once someone
    has actually looked at the flow and agreed with it.
    """
    user_id, org_id, token = _require_user(current_user)
    await _require_admin(user_id, token)
    # Writing the legacy key is what records "configured"; there is no separate
    # flag, which is also why accepting the defaults still counts as a decision.
    await ob_catalog.sync_legacy_toggles(org_id)
    return await _read_catalog(org_id)


@router.patch("/catalog/steps/{step_key}", response_model=CatalogRead)
async def update_catalog_step(
    step_key: str,
    body: UpdateStepRequest,
    current_user: dict = Depends(verify_jwt),
) -> CatalogRead:
    """Admin-only. Rename a step, move it, change who signs it, or switch it off."""
    user_id, org_id, token = _require_user(current_user)
    await _require_admin(user_id, token)
    try:
        await ob_catalog.update_step(
            org_id=org_id,
            step_key=step_key,
            enabled=body.enabled,
            label=body.label,
            signer_roles=body.signer_roles,
            requires_hr_approval=body.requires_hr_approval,
            after_step_key=body.after_step_key,
            move=body.move,
        )
    except ob_catalog.CatalogError as exc:
        raise _catalog_error(exc) from exc
    return await _read_catalog(org_id)


@router.post(
    "/catalog/steps",
    response_model=CatalogRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_catalog_step(
    body: CreateStepRequest,
    current_user: dict = Depends(verify_jwt),
) -> CatalogRead:
    """Admin-only. Add a step of any kind at a chosen point in the pipeline."""
    user_id, org_id, token = _require_user(current_user)
    await _require_admin(user_id, token)

    items = [i.model_dump() for i in body.items]
    if body.kind == ob_catalog.KIND_COLLECT:
        if not items:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Add at least one document to ask the candidate for.",
            )
        _validate_formats(items)

    try:
        await ob_catalog.create_step(
            org_id=org_id,
            kind=body.kind,
            label=body.label,
            after_step_key=body.after_step_key,
            items=items,
            document_type_keys=body.document_type_keys,
            signer_roles=body.signer_roles,
            system_action=body.system_action,
            requires_hr_approval=body.requires_hr_approval,
        )
    except ob_catalog.CatalogError as exc:
        raise _catalog_error(exc) from exc
    return await _read_catalog(org_id)


@router.put("/catalog/steps/{step_key}/items", response_model=CatalogRead)
async def replace_step_items(
    step_key: str,
    body: ReplaceItemsRequest,
    current_user: dict = Depends(verify_jwt),
) -> CatalogRead:
    """Admin-only. Set a collection step's checklist.

    Runs already in flight keep the checklist they started with — they hold
    their own snapshot, so this only changes what future candidates are asked.
    """
    user_id, org_id, token = _require_user(current_user)
    await _require_admin(user_id, token)

    defs = await ob_catalog.get_step_defs(org_id)
    step = next((d for d in defs if d["step_key"] == step_key), None)
    if step is None or step["kind"] != ob_catalog.KIND_COLLECT:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No document collection step by that name.",
        )

    items = [i.model_dump() for i in body.items]
    _validate_formats(items)
    try:
        await ob_catalog.replace_collect_items(
            org_id=org_id, step_def_id=step["id"], items=items
        )
    except ob_catalog.CatalogError as exc:
        raise _catalog_error(exc) from exc
    return await _read_catalog(org_id)


@router.delete("/catalog/steps/{step_key}", response_model=CatalogRead)
async def delete_catalog_step(
    step_key: str,
    current_user: dict = Depends(verify_jwt),
) -> CatalogRead:
    """Admin-only. Remove a step the org added; built-ins can only be disabled."""
    user_id, org_id, token = _require_user(current_user)
    await _require_admin(user_id, token)
    try:
        await ob_catalog.delete_step(org_id=org_id, step_key=step_key)
    except ob_catalog.CatalogError as exc:
        raise _catalog_error(exc) from exc
    return await _read_catalog(org_id)


def _validate_formats(items: list[dict[str, Any]]) -> None:
    for item in items:
        bad = [
            f
            for f in (item.get("accepted_formats") or [])
            if f.lower() not in ALLOWED_UPLOAD_FORMATS
        ]
        if bad:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"'{item.get('label')}' allows a file type we can't accept: "
                    f"{', '.join(bad)}. Choose from "
                    f"{', '.join(ALLOWED_UPLOAD_FORMATS)}."
                ),
            )


# ── Candidate: the portal ───────────────────────────────────────────────────


async def _active_run_for_candidate(user_id: str) -> dict[str, Any] | None:
    """The run this signed-in user is the candidate of, if any.

    Matched on `pre_join_user_id` rather than on email: the account is what the
    run points at, and an email match would let anyone who later joined with a
    recycled address see someone else's paperwork.
    """
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .select("*")
        .eq("pre_join_user_id", user_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        return None
    run = rows[0]
    if run.get("status") in ("cancelled", "failed"):
        return None
    return run


async def build_candidate_view(run: dict[str, Any]) -> CandidateOnboardingRead:
    """What this run has asked the candidate for, and what they've filed.

    Only steps the run has actually reached are returned. A step further down
    the pipeline is not merely hidden in the UI — it is absent from the
    response, so an early-provisioned candidate cannot pre-empt an ask that
    hasn't been made.
    """
    steps = await ob_catalog.get_run_steps(run["id"])
    submissions = await ob_catalog.get_submissions(run["id"])
    by_step: dict[str, list[dict[str, Any]]] = {}
    for s in submissions:
        by_step.setdefault(s["run_step_id"], []).append(s)

    svc = get_service_client()
    org = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select("name")
        .eq("id", run["org_id"])
        .maybe_single()
        .execute()
    )

    visible: list[CandidateStepRead] = []
    for step in steps:
        if step.get("kind") != ob_catalog.KIND_COLLECT:
            continue
        if step.get("status") in (ob_catalog.STATUS_PENDING, ob_catalog.STATUS_SKIPPED):
            continue
        filed = {s["item_key"]: s for s in by_step.get(step["id"], [])}
        visible.append(
            CandidateStepRead(
                step_key=step["step_key"],
                label=step.get("label") or "Documents",
                bundle_key=step.get("bundle_key"),
                bundle_label=step.get("bundle_label"),
                status=step["status"],
                items=[
                    CandidateItemRead(
                        item_key=i["item_key"],
                        label=i["label"],
                        help_text=i.get("help_text"),
                        required=bool(i.get("required", True)),
                        accepted_formats=list(i.get("accepted_formats") or []),
                        submitted=i["item_key"] in filed,
                        original_filename=filed.get(i["item_key"], {}).get(
                            "original_filename"
                        ),
                        submitted_at=filed.get(i["item_key"], {}).get("submitted_at"),
                        review_status=filed.get(i["item_key"], {}).get("review_status"),
                        review_note=filed.get(i["item_key"], {}).get("review_note"),
                    )
                    for i in (step.get("config") or {}).get("items") or []
                ],
            )
        )

    return CandidateOnboardingRead(
        run_id=run["id"],
        candidate_name=run["candidate_name"],
        company_name=(org.data or {}).get("name") or "your new company",
        role_title=run["role_title"],
        steps=visible,
    )


@router.get("/candidate", response_model=CandidateOnboardingRead)
async def get_candidate_onboarding(
    current_user: dict = Depends(verify_jwt),
) -> CandidateOnboardingRead:
    """The signed-in candidate's checklist."""
    user_id = current_user.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to continue."
        )

    run = await _active_run_for_candidate(user_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You don't have an onboarding in progress.",
        )
    return await build_candidate_view(run)


async def store_candidate_upload(
    *,
    run: dict[str, Any],
    step_key: str,
    item_key: str,
    file: UploadFile,
    submitted_by: str | None,
) -> dict[str, Any]:
    """File one document against one checklist item.

    Re-uploading replaces the previous file: correcting a blurry scan is not an
    audit event, and the storage path is deterministic so the bytes are
    overwritten rather than accumulating orphans.

    `submitted_by` is the candidate's user id when they came in signed in, and
    None when they came in on a link — the run and the step already say whose
    document this is, and the column is a provenance note rather than the
    authorisation.
    """
    steps = await ob_catalog.get_run_steps(run["id"])
    step = next((s for s in steps if s["step_key"] == step_key), None)
    if step is None or step.get("kind") != ob_catalog.KIND_COLLECT:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such document request."
        )
    # Server-side, not just hidden in the UI: a step the run hasn't reached
    # cannot be uploaded against even by someone constructing the request
    # by hand.
    if step.get("status") in (ob_catalog.STATUS_PENDING, ob_catalog.STATUS_SKIPPED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This document hasn't been requested yet.",
        )
    # Mid-review is a bad moment to swap a file: HR is looking at the version
    # that was there when they opened it, and accepting would then accept
    # something they never saw. The way back in is HR sending it back, which
    # re-opens exactly the items they named.
    if step.get("status") == ob_catalog.STATUS_PENDING_HR_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "These documents are being checked right now. You'll get an "
                "email if anything needs re-uploading."
            ),
        )

    items = (step.get("config") or {}).get("items") or []
    item = next((i for i in items if i["item_key"] == item_key), None)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such document request."
        )

    body = await file.read()
    if not body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="That file is empty."
        )
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"That file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)}MB. "
                "Try a lower-resolution scan."
            ),
        )

    filename = file.filename or "upload"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    allowed = [f.lower() for f in (item.get("accepted_formats") or [])]
    if allowed and ext not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{item['label']} needs to be a {', '.join(allowed)} file. "
                f"You uploaded a .{ext or 'file'}."
            ),
        )

    svc = get_service_client()
    path = (
        f"orgs/{run['org_id']}/onboarding/{run['id']}/collect/"
        f"{step_key}/{item_key}.{ext or 'bin'}"
    )

    def _upload() -> None:
        svc.storage.from_(ob_storage.STORAGE_BUCKET).upload(
            path=path,
            file=body,
            file_options={
                "content-type": file.content_type or "application/octet-stream",
                "upsert": "true",
            },
        )

    await asyncio.to_thread(_upload)

    payload = {
        "org_id": run["org_id"],
        "run_id": run["id"],
        "run_step_id": step["id"],
        "item_key": item_key,
        "label": item["label"],
        "storage_path": path,
        "original_filename": filename,
        "mime_type": file.content_type,
        "file_bytes": len(body),
        "submitted_by": submitted_by,
        "submitted_at": datetime.now(UTC).isoformat(),
        # A replacement is a fresh document, so an earlier rejection must not
        # cling to it.
        "review_status": "pending",
        "reviewed_by": None,
        "reviewed_at": None,
        "review_note": None,
    }

    existing = await asyncio.to_thread(
        lambda: svc.table("onboarding_collect_submissions")
        .select("id")
        .eq("run_step_id", step["id"])
        .eq("item_key", item_key)
        .maybe_single()
        .execute()
    )
    if existing and existing.data:
        await asyncio.to_thread(
            lambda: svc.table("onboarding_collect_submissions")
            .update(payload)
            .eq("id", existing.data["id"])
            .execute()
        )
    else:
        await asyncio.to_thread(
            lambda: svc.table("onboarding_collect_submissions")
            .insert(payload)
            .execute()
        )

    await ob_storage.log_onboarding_event(
        org_id=run["org_id"],
        run_id=run["id"],
        actor_kind="candidate",
        event_type="candidate_document_submitted",
        message=f"{run['candidate_name']} uploaded {item['label']}.",
        actor_user_id=submitted_by,
        metadata={"step_key": step_key, "item_key": item_key},
    )

    # A replacement for something HR sent back is new work they have not seen,
    # so the step's approval is withdrawn. Without this an approved step would
    # stay approved through every later resubmission — the exact hole the gate
    # exists to close.
    await ob_catalog.clear_step_review(step["id"])

    # Re-drive the run: this upload may have been the last required document.
    # What happens then is the agent's call — it either parks the step for HR
    # to check or, when the org turned that off, completes it.
    submissions = await ob_catalog.get_submissions(run["id"])
    satisfied = ob_catalog.is_collect_satisfied(step, submissions)
    if satisfied:
        await ob_storage.log_onboarding_event(
            org_id=run["org_id"],
            run_id=run["id"],
            actor_kind="candidate",
            event_type="candidate_documents_complete",
            message=f"All documents received for {step.get('label')}.",
            metadata={"step_key": step_key},
        )
        await get_inngest_client().send(
            inngest.Event(
                name="onboarding_v2/collect_submitted",
                data={
                    "onboarding_run_id": run["id"],
                    "org_id": run["org_id"],
                    "step_key": step_key,
                },
            )
        )

    return {"ok": True, "item_key": item_key, "step_complete": satisfied}


@router.post("/candidate/steps/{step_key}/items/{item_key}/upload")
async def upload_candidate_document(
    step_key: str,
    item_key: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """The signed-in candidate files one document."""
    user_id = current_user.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to continue."
        )

    run = await _active_run_for_candidate(user_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You don't have an onboarding in progress.",
        )
    return await store_candidate_upload(
        run=run,
        step_key=step_key,
        item_key=item_key,
        file=file,
        submitted_by=user_id,
    )


# ── HR: reviewing what came back ────────────────────────────────────────────


@router.get("/runs/{run_id}/submissions", response_model=list[SubmissionRead])
async def list_run_submissions(
    run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> list[SubmissionRead]:
    """Every document the candidate filed on this run, with a fresh link."""
    _, org_id, _ = _require_user(current_user)
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("onboarding_collect_submissions")
        .select("*")
        .eq("run_id", run_id)
        .eq("org_id", org_id)
        .execute()
    )
    rows = res.data or []
    urls = await asyncio.gather(
        *(ob_storage.mint_signed_url(r["storage_path"]) for r in rows)
    )
    return [
        SubmissionRead(
            id=r["id"],
            run_step_id=r["run_step_id"],
            item_key=r["item_key"],
            label=r["label"],
            original_filename=r.get("original_filename"),
            file_bytes=r.get("file_bytes"),
            submitted_at=r.get("submitted_at"),
            review_status=r.get("review_status") or "pending",
            review_note=r.get("review_note"),
            signed_url=url,
        )
        for r, url in zip(rows, urls)
    ]


@router.post("/runs/{run_id}/submissions/{submission_id}/review")
async def review_submission(
    run_id: str,
    submission_id: str,
    body: ReviewSubmissionRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Approve or reject one filed document.

    A per-file note, not a decision about the step. It is how HR marks which
    documents in a checklist are wrong before sending the whole step back
    through `/steps/{key}/review`, which is what actually moves the run and
    emails the candidate.

    Marking one rejected does stop it counting as filed, so the item re-opens
    when the step is next asked. On a step already past its gate that is a
    record rather than an instruction — nothing re-asks a finished step, and
    unwinding a completed pipeline is not what "this scan is blurry" means.
    """
    user_id, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    existing = await asyncio.to_thread(
        lambda: svc.table("onboarding_collect_submissions")
        .select("*")
        .eq("id", submission_id)
        .eq("run_id", run_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not existing or not existing.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such submission."
        )

    await asyncio.to_thread(
        lambda: svc.table("onboarding_collect_submissions")
        .update(
            {
                "review_status": body.review_status,
                "review_note": body.review_note,
                "reviewed_by": user_id,
                "reviewed_at": datetime.now(UTC).isoformat(),
            }
        )
        .eq("id", submission_id)
        .execute()
    )

    await ob_storage.log_onboarding_event(
        org_id=org_id,
        run_id=run_id,
        actor_kind="hr",
        event_type=f"candidate_document_{body.review_status}",
        message=f"{existing.data['label']} {body.review_status}.",
        actor_user_id=user_id,
        metadata={"item_key": existing.data["item_key"], "note": body.review_note},
    )
    return {"ok": True, "review_status": body.review_status}
