"""Step-keyed actions on a running onboarding pipeline.

HR approves a draft, or uploads a scan of one they signed on paper. Both used
to exist once per document kind — `/loi/approve-draft`, `/offer-bundle/approve`,
`/loi/upload-signed` — with the signer routing written into each: the LOI went
HR-then-candidate, the appointment bundle went candidate-only, and nothing else
could be signed at all.

Here they are addressed by step key instead, and who signs comes from the
step's `signer_roles`. An org that wants its NDA countersigned by HR only, or
its appointment letter sent unsigned, says so in the catalog and this routes it.

The legacy endpoints still exist in `onboarding_v2.py` and now delegate here.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import inngest
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.auth import verify_jwt
from app.database import get_service_client
from app.errors import NoOrganization
from app.inngest.client import get_inngest_client
from app.models.onboarding_catalog import ReviewStepRequest
from app.observability import get_logger
from app.services.agents.onboarding_v2 import catalog as ob_catalog
from app.services.agents.onboarding_v2 import storage as ob_storage

log = get_logger(__name__)

router = APIRouter(prefix="/onboarding", tags=["onboarding-steps"])

MAX_SIGNED_PDF_BYTES = 25 * 1024 * 1024


def _require_user(current_user: dict) -> tuple[str, str, str]:
    user_id = current_user.get("user_id")
    org_id = current_user.get("org_id")
    token = current_user.get("token")
    if not user_id or not org_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return user_id, org_id, token


async def _load_step(
    run_id: str, org_id: str, step_key: str
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """The run, the named step, and everything bundled with it."""
    svc = get_service_client()
    run = await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .select("*")
        .eq("id", run_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not run or not run.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found.")

    steps = await ob_catalog.get_run_steps(run_id)
    step = next((s for s in steps if s["step_key"] == step_key), None)
    if step is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"This run has no '{step_key}' step."
        )
    return run.data, step, ob_catalog.bundle_siblings(steps, step)


async def _hr_identity(run: dict[str, Any], fallback_user_id: str) -> tuple[str, str]:
    """Email and display name of the HR user who owns this run."""
    svc = get_service_client()
    user_id = run.get("triggered_by_user_id") or fallback_user_id
    try:
        au = await asyncio.to_thread(lambda: svc.auth.admin.get_user_by_id(user_id))
        email = getattr(getattr(au, "user", None), "email", None)
    except Exception:
        email = None
    if not email:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Couldn't resolve the HR user's email for the signing flow. "
            "Sign out and back in, or contact support.",
        )
    profile = await asyncio.to_thread(
        lambda: svc.table("users")
        .select("display_name")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    name = (profile.data or {}).get("display_name") or email.split("@", 1)[0]
    return email, name


async def _build_signers(
    run: dict[str, Any], roles: list[str], fallback_user_id: str
) -> list[dict[str, Any]]:
    """Turn the step's signer roles into an ordered envelope signer list.

    Order is routing order — roles[0] signs first. This is the whole point of
    making signers configurable, so it is preserved exactly as the org set it
    rather than normalised to some canonical HR-then-candidate sequence.
    """
    signers: list[dict[str, Any]] = []
    for index, role in enumerate(roles):
        if role == ob_catalog.SIGNER_HR:
            email, name = await _hr_identity(run, fallback_user_id)
        elif role == ob_catalog.SIGNER_CANDIDATE:
            email, name = run["candidate_email"], run["candidate_name"]
        else:
            # normalize_signer_roles rejects these on write; a row that predates
            # that check should not silently route a document to nobody.
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"This step lists '{role}' as a signer, which isn't a role we "
                "can route to. Fix the step in Settings → Onboarding.",
            )
        signers.append(
            {"role": role, "email": email, "name": name, "routing_order": index + 1}
        )
    return signers


async def _source_pdf(
    run_id: str, org_id: str, bundle: list[dict[str, Any]]
) -> tuple[str, list[str]]:
    """The path to sign, and the document kinds it covers.

    A bundle is merged into one PDF so its members go into a single envelope —
    a signer should sign "your appointment letter and NDA" once, not twice. HR's
    edited copy wins over the generated draft wherever one exists.
    """
    svc = get_service_client()
    kinds = [m["step_key"] for m in bundle]
    res = await asyncio.to_thread(
        lambda: svc.table("onboarding_documents")
        .select("kind, storage_path, hr_edited_pdf_path")
        .eq("run_id", run_id)
        .in_("kind", kinds)
        .execute()
    )
    by_kind = {d["kind"]: d for d in (res.data or [])}
    missing = [k for k in kinds if k not in by_kind]
    if missing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Nothing generated yet for: {', '.join(missing)}.",
        )

    paths = [
        by_kind[k].get("hr_edited_pdf_path") or by_kind[k]["storage_path"] for k in kinds
    ]
    if len(paths) == 1:
        return paths[0], kinds

    from app.services.integrations.inhouse_sign import merge_pdfs

    def _download(path: str) -> bytes:
        return svc.storage.from_(ob_storage.STORAGE_BUCKET).download(path)

    parts = [await asyncio.to_thread(_download, p) for p in paths]
    merged = merge_pdfs(parts)
    bundle_key = bundle[0].get("bundle_key") or bundle[0]["step_key"]
    merged_path = f"orgs/{org_id}/onboarding/{run_id}/{bundle_key}.pdf"

    def _upload() -> None:
        svc.storage.from_(ob_storage.STORAGE_BUCKET).upload(
            path=merged_path,
            file=merged,
            file_options={"content-type": "application/pdf", "upsert": "true"},
        )

    await asyncio.to_thread(_upload)
    return merged_path, kinds


def _is_loi(step: dict[str, Any]) -> bool:
    """Is this the step that renders the letter of intent?

    `onboarding_runs` still carries four LOI timestamp columns that the stage
    board and the e-sign stall cron read. Only this step writes them, and it is
    identified by document type — an org that renamed the step still has an LOI,
    and gating on the literal key `loi` would silently stop stamping them.
    """
    return (
        step.get("document_type_key") == ob_catalog.DOCUMENT_TYPE_LOI
        or step["step_key"] == "loi"
    )


async def _kick(run_id: str, org_id: str) -> None:
    client = get_inngest_client()
    await client.send(
        inngest.Event(
            name="onboarding_v2/resume",
            data={"onboarding_run_id": run_id, "org_id": org_id},
        )
    )


# Where a candidate answers each kind of ask, and the run column holding the
# token that gets them in without a login. `/candidate/documents` rather than
# `/documents` because the latter is a protected dashboard route and the proxy
# would bounce them to /login — the one thing these links exist to avoid.
_CANDIDATE_PORTALS = {
    "documents": ("documents_token", "/candidate/documents"),
    "references": ("references_form_token", "/references"),
}


def _candidate_link(run: dict[str, Any], portal: str) -> str | None:
    """A link the candidate can open without signing in, if there is one.

    The tokens are minted by the agent when it first asks. By the time HR is
    rejecting something the relevant one exists; if it somehow does not, the
    email still goes out without a button, which is better than not telling
    them their document was sent back.
    """
    from app.config import get_settings

    column, path = _CANDIDATE_PORTALS[portal]
    token = run.get(column)
    settings = get_settings()
    if not token or not settings.app_url:
        return None
    return f"{settings.app_url.rstrip('/')}{path}/{token}"


@router.post("/runs/{run_id}/steps/{step_key}/approve")
async def approve_step(
    run_id: str,
    step_key: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """HR approves a generated draft. What happens next is the step's business.

    * Nobody signs it — the documents are final, and the agent sends them on.
    * Somebody signs it and apps/esign is wired — one routed envelope for the
      whole bundle, the first signer emailed their link immediately.
    * Somebody signs it and apps/esign is not wired — the print-and-scan
      fallback, which only HR can do: they download, sign, and upload the scan.
      A candidate-only step has no offline equivalent, so it goes out unsigned
      with that recorded on the timeline rather than stalling the hire.
    """
    user_id, org_id, _ = _require_user(current_user)
    run, step, bundle = await _load_step(run_id, org_id, step_key)
    svc = get_service_client()

    if step["status"] != ob_catalog.STATUS_PENDING_HR_REVIEW:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"'{step['label']}' is {step['status'].replace('_', ' ')}, "
            "not waiting for your approval.",
        )

    label = step.get("bundle_label") or step["label"]
    roles = list(step.get("signer_roles") or [])
    now = datetime.now(UTC).isoformat()

    async def _move(step_status: str, message: str, event_type: str) -> None:
        for member in bundle:
            await ob_catalog.set_step_status(member["id"], step_status)
        await ob_storage.log_onboarding_event(
            org_id=org_id,
            run_id=run_id,
            actor_kind="hr",
            event_type=event_type,
            message=message,
            actor_user_id=user_id,
        )
        await _kick(run_id, org_id)

    if not roles:
        await _move(
            ob_catalog.STATUS_ACTIVE,
            f"HR approved {label}. No signatures required — sending to the candidate.",
            "step_approved",
        )
        return {"status": "sending", "step_key": step_key, "signers": []}

    from app.services.integrations.inhouse_sign import (
        InhouseSignError,
        InhouseSignUnavailable,
        create_envelope,
    )
    from app.services.integrations.inhouse_sign import is_configured as _esign_ready

    if not _esign_ready():
        if ob_catalog.SIGNER_HR in roles:
            await _move(
                ob_catalog.STATUS_PENDING_SIGNATURE,
                f"HR approved {label}. E-signature isn't configured, so it needs "
                "to be printed, signed and uploaded back.",
                "step_awaiting_manual_signature",
            )
            return {"status": "awaiting_manual_signature", "step_key": step_key}

        await _move(
            ob_catalog.STATUS_ACTIVE,
            f"HR approved {label}. E-signature isn't configured and only the "
            "candidate was set to sign, so it was sent unsigned.",
            "step_sent_unsigned",
        )
        return {"status": "sending", "step_key": step_key, "signers": []}

    signers = await _build_signers(run, roles, user_id)
    source_path, kinds = await _source_pdf(run_id, org_id, bundle)

    try:
        envelope = await create_envelope(
            org_id=org_id,
            run_id=run_id,
            document_kinds=kinds,
            storage_path=source_path,
            signers=signers,
            completion_event="onboarding_v2/esign_completed",
        )
    except (InhouseSignError, InhouseSignUnavailable) as exc:
        log.warning(
            "onboarding_v2.envelope_create_failed run=%s step=%s err=%s",
            run_id,
            step_key,
            exc,
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Couldn't create the signing envelope: {exc}",
        ) from exc

    first = envelope["signers"][0]
    from app.config import get_settings
    from app.services.email import send_email_event

    settings = get_settings()
    try:
        await send_email_event(
            event_type="onboarding_sign_your_turn",
            to=first["email"],
            user_id=(
                run.get("triggered_by_user_id")
                if first["role"] == ob_catalog.SIGNER_HR
                else None
            ),
            org_id=org_id,
            # Keyed by role, not just address: HR and the candidate are often
            # the same mailbox, and an email-only key made the second signer's
            # turn look like a duplicate of the first's and dropped it.
            dedupe_key=(
                f"sign-your-turn-{envelope['envelope_id']}-"
                f"{first['role']}-{first['email']}"
            ),
            data={
                "recipient_name": first.get("name") or first["email"],
                "document_label": label,
                "signing_url": first["signing_url"],
                "app_url": settings.app_url.rstrip("/"),
            },
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("onboarding_v2.sign_email_failed run=%s err=%s", run_id, exc)

    await asyncio.to_thread(
        lambda: svc.table("onboarding_documents")
        .update(
            {
                "esign_envelope_id": envelope["envelope_id"],
                "esign_status": "sent",
                "sign_status": (
                    "sent_to_hr"
                    if first["role"] == ob_catalog.SIGNER_HR
                    else "sent_to_candidate"
                ),
            }
        )
        .eq("run_id", run_id)
        .in_("kind", kinds)
        .execute()
    )
    if _is_loi(step):
        await asyncio.to_thread(
            lambda: svc.table("onboarding_runs")
            .update({"loi_approved_for_signing_at": now})
            .eq("id", run_id)
            .execute()
        )

    routing = " → ".join(f"{s['role']} ({s['email']})" for s in signers)
    await _move(
        ob_catalog.STATUS_PENDING_SIGNATURE,
        f"Signing envelope created for {label}. Routing: {routing}.",
        "step_envelope_created",
    )
    return {
        "status": "pending_signature",
        "step_key": step_key,
        "envelope_id": envelope["envelope_id"],
        "signers": [{"role": s["role"], "email": s["email"]} for s in signers],
    }


@router.post("/runs/{run_id}/steps/{step_key}/review")
async def review_step(
    run_id: str,
    step_key: str,
    body: ReviewStepRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """HR accepts or rejects what the candidate did at this step.

    The gate the whole feature is about. Three kinds of step reach it — a
    signed document, a finished upload checklist, a list of referees — and the
    accept path is the same for all three: record the decision, and let the
    agent carry on from a step it now considers cleared.

    Rejecting is where they differ, because "ask them again" means something
    different in each case, and each is handled by its own `_reject_*` below.
    What they share is that none of them rewinds the run. The step stays where
    it is in the pipeline and re-opens in place, so a document sent back on
    Friday does not undo the two steps that ran on Thursday.
    """
    user_id, org_id, _ = _require_user(current_user)
    run, step, bundle = await _load_step(run_id, org_id, step_key)

    if step["status"] != ob_catalog.STATUS_PENDING_HR_APPROVAL:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"'{step['label']}' is {step['status'].replace('_', ' ')}, "
            "not waiting for your review.",
        )

    label = step.get("bundle_label") or step["label"]
    if body.decision == ob_catalog.REVIEW_APPROVED:
        return await _approve_step(
            run=run,
            step=step,
            bundle=bundle,
            org_id=org_id,
            user_id=user_id,
            label=label,
            note=body.note,
        )
    return await _reject_step(
        run=run,
        step=step,
        bundle=bundle,
        org_id=org_id,
        user_id=user_id,
        label=label,
        note=body.note,
    )


async def _approve_step(
    *,
    run: dict[str, Any],
    step: dict[str, Any],
    bundle: list[dict[str, Any]],
    org_id: str,
    user_id: str,
    label: str,
    note: str | None,
) -> dict[str, Any]:
    """Accept the candidate's work and hand the step back to the agent.

    A collect step is finished by this — there is nothing after the documents.
    The other two have work left (deliver the signed copies, email the
    referees), so they go to `active` and the agent picks them up.
    """
    run_id = run["id"]
    finished = step["kind"] == ob_catalog.KIND_COLLECT
    next_status = (
        ob_catalog.STATUS_DONE if finished else ob_catalog.STATUS_ACTIVE
    )
    decided = {
        "review_decision": ob_catalog.REVIEW_APPROVED,
        "review_note": note,
        "reviewed_by": user_id,
        "reviewed_at": datetime.now(UTC).isoformat(),
    }
    for member in bundle:
        await ob_catalog.set_step_status(member["id"], next_status, extra=decided)

    await ob_storage.log_onboarding_event(
        org_id=org_id,
        run_id=run_id,
        actor_kind="hr",
        event_type="step_approved_by_hr",
        message=f"HR accepted {label}.",
        actor_user_id=user_id,
        metadata={"step_key": step["step_key"], "note": note},
    )
    await _kick(run_id, org_id)
    return {"status": next_status, "step_key": step["step_key"], "decision": "approved"}


async def _reject_step(
    *,
    run: dict[str, Any],
    step: dict[str, Any],
    bundle: list[dict[str, Any]],
    org_id: str,
    user_id: str,
    label: str,
    note: str | None,
) -> dict[str, Any]:
    """Send the ask back to the candidate, at this step, with a reason."""
    if not (note or "").strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Say what's wrong — the candidate only sees this note, and it's the "
            "only instruction they get about what to fix.",
        )
    note = note.strip()
    run_id = run["id"]
    rejected = {
        "review_decision": ob_catalog.REVIEW_REJECTED,
        "review_note": note,
        "reviewed_by": user_id,
        "reviewed_at": datetime.now(UTC).isoformat(),
        "approval_round": (step.get("approval_round") or 0) + 1,
    }

    if step["kind"] == ob_catalog.KIND_COLLECT:
        outcome = await _reject_collect(
            run=run, step=step, org_id=org_id, note=note, extra=rejected
        )
    elif step["kind"] == ob_catalog.KIND_SYSTEM:
        outcome = await _reject_references(
            run=run, step=step, org_id=org_id, note=note, extra=rejected
        )
    else:
        outcome = await _reject_signature(
            run=run,
            step=step,
            bundle=bundle,
            org_id=org_id,
            user_id=user_id,
            label=label,
            note=note,
            extra=rejected,
        )

    await ob_storage.log_onboarding_event(
        org_id=org_id,
        run_id=run_id,
        actor_kind="hr",
        event_type="step_rejected_by_hr",
        message=f"HR sent {label} back to {run['candidate_name']}: {note}",
        actor_user_id=user_id,
        metadata={
            "step_key": step["step_key"],
            "round": rejected["approval_round"],
            "note": note,
        },
    )
    await _kick(run_id, org_id)
    return {"decision": "rejected", "step_key": step["step_key"], **outcome}


async def _reject_collect(
    *,
    run: dict[str, Any],
    step: dict[str, Any],
    org_id: str,
    note: str,
    extra: dict[str, Any],
) -> dict[str, Any]:
    """Re-open an upload checklist.

    HR may have already marked individual files rejected in the submissions
    panel — those are the ones being sent back, and the rest stand. When they
    have marked none, the whole checklist is what they are rejecting, so all of
    it is re-asked. Rejecting the step while implicitly approving every file in
    it is not a state anyone means.

    A rejected submission stops counting as filed (`is_collect_satisfied`), so
    the step re-opens with exactly those items outstanding.
    """
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("onboarding_collect_submissions")
        .select("id, item_key, label, review_status, review_note")
        .eq("run_step_id", step["id"])
        .execute()
    )
    rows = res.data or []
    already = [r for r in rows if r.get("review_status") == ob_catalog.REVIEW_REJECTED]
    targets = already or rows

    now = datetime.now(UTC).isoformat()
    for row in targets:
        patch = {
            "review_status": ob_catalog.REVIEW_REJECTED,
            # Keep a per-file note HR already wrote; the step-level note is the
            # fallback for the ones they rejected wholesale.
            "review_note": row.get("review_note") or note,
            "reviewed_by": extra["reviewed_by"],
            "reviewed_at": now,
        }
        await asyncio.to_thread(
            lambda rid=row["id"], p=patch: svc.table("onboarding_collect_submissions")
            .update(p)
            .eq("id", rid)
            .execute()
        )

    await ob_catalog.set_step_status(
        step["id"], ob_catalog.STATUS_ACTIVE, extra=extra
    )
    await _email_candidate_rejection(
        run=run,
        org_id=org_id,
        step=step,
        note=note,
        round_number=extra["approval_round"],
        what=(
            f"{len(targets)} document{'' if len(targets) == 1 else 's'} "
            "needs re-uploading"
        ),
        action_url=_candidate_link(run, "documents"),
        action_label="Upload again",
    )
    return {
        "status": ob_catalog.STATUS_ACTIVE,
        "reopened_items": [r["item_key"] for r in targets],
    }


async def _reject_references(
    *,
    run: dict[str, Any],
    step: dict[str, Any],
    org_id: str,
    note: str,
    extra: dict[str, Any],
) -> dict[str, Any]:
    """Ask the candidate for a different set of referees.

    The rows are marked `superseded` rather than deleted: HR turning down a
    referee is a thing that happened to this hire, and the timeline should be
    able to show it. Clearing `references_submitted_at` is what re-opens the
    form the candidate already filled — the public endpoint refuses a second
    submission while it is set.

    Reachable only before the verification emails go out. The agent parks on
    this gate the moment the list arrives, so nothing has been sent to anyone
    at the point HR can reject it.
    """
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("onboarding_bgv_references")
        .update({"status": "superseded"})
        .eq("run_id", run["id"])
        .in_("status", ["pending", "sent", "opened"])
        .execute()
    )
    await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .update({"references_submitted_at": None})
        .eq("id", run["id"])
        .execute()
    )
    await ob_catalog.set_step_status(
        step["id"], ob_catalog.STATUS_ACTIVE, extra=extra
    )
    await _email_candidate_rejection(
        run=run,
        org_id=org_id,
        step=step,
        note=note,
        round_number=extra["approval_round"],
        what="your references need to be re-submitted",
        action_url=_candidate_link(run, "references"),
        action_label="Submit references again",
    )
    return {"status": ob_catalog.STATUS_ACTIVE, "references_reset": True}


async def _reject_signature(
    *,
    run: dict[str, Any],
    step: dict[str, Any],
    bundle: list[dict[str, Any]],
    org_id: str,
    user_id: str,
    label: str,
    note: str,
    extra: dict[str, Any],
) -> dict[str, Any]:
    """Ask the candidate to sign the same document again.

    The document itself was fine — HR approved that draft already. What failed
    is the signature, so the same PDF goes into a fresh envelope rather than
    being regenerated: re-rendering it would re-run the template against
    whatever the field values say today, and a candidate asked to re-sign
    should be re-signing the letter they were shown.

    The old envelope is left alone rather than voided. It is necessarily
    complete — the gate only opens once every signer has finished, which is
    what `_signatures_complete` means — so there is no live link to close, and
    `void_envelopes_for_run` works per *run*, not per step: calling it here
    would take down a signing link belonging to some other step that happens to
    be in flight. The document rows are detached from it below, which is what
    actually decides which envelope counts.
    """
    run_id = run["id"]
    svc = get_service_client()
    kinds = [m["step_key"] for m in bundle]

    from app.services.integrations.inhouse_sign import (
        InhouseSignError,
        InhouseSignUnavailable,
        create_envelope,
    )
    from app.services.integrations.inhouse_sign import is_configured as _esign_ready

    # The signed copy is no longer the document of record — clearing it is what
    # stops `_signatures_complete` seeing the rejected signature and treating
    # the next kick as ready to deliver.
    await asyncio.to_thread(
        lambda: svc.table("onboarding_documents")
        .update(
            {
                "signed_pdf_path": None,
                "signed_uploaded_at": None,
                "signed_uploaded_by": None,
                "esign_envelope_id": None,
                "esign_status": None,
                "esign_signing_url": None,
                "esign_completed_at": None,
                "sign_status": "draft",
            }
        )
        .eq("run_id", run_id)
        .in_("kind", kinds)
        .execute()
    )

    roles = list(step.get("signer_roles") or [])
    for member in bundle:
        await ob_catalog.set_step_status(
            member["id"], ob_catalog.STATUS_PENDING_SIGNATURE, extra=extra
        )

    if not _esign_ready():
        # Print-and-scan: HR uploads a fresh scan through the same endpoint the
        # first attempt used. Nothing to email the candidate about a document
        # that never leaves the office.
        await _email_candidate_rejection(
            run=run,
            org_id=org_id,
            step=step,
            note=note,
            round_number=extra["approval_round"],
            what=f"{label} has to be signed again",
            action_url=None,
            action_label=None,
        )
        return {"status": ob_catalog.STATUS_PENDING_SIGNATURE, "envelope_id": None}

    signers = await _build_signers(run, roles, user_id)
    source_path, envelope_kinds = await _source_pdf(run_id, org_id, bundle)
    try:
        envelope = await create_envelope(
            org_id=org_id,
            run_id=run_id,
            document_kinds=envelope_kinds,
            storage_path=source_path,
            signers=signers,
            completion_event="onboarding_v2/esign_completed",
        )
    except (InhouseSignError, InhouseSignUnavailable) as exc:
        log.warning(
            "onboarding_v2.resign_envelope_failed run=%s step=%s err=%s",
            run_id,
            step["step_key"],
            exc,
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Couldn't create the new signing envelope: {exc}",
        ) from exc

    await asyncio.to_thread(
        lambda: svc.table("onboarding_documents")
        .update(
            {
                "esign_envelope_id": envelope["envelope_id"],
                "esign_status": "sent",
                "sign_status": (
                    "sent_to_hr"
                    if envelope["signers"][0]["role"] == ob_catalog.SIGNER_HR
                    else "sent_to_candidate"
                ),
            }
        )
        .eq("run_id", run_id)
        .in_("kind", envelope_kinds)
        .execute()
    )

    first = envelope["signers"][0]
    await _email_candidate_rejection(
        run=run,
        org_id=org_id,
        step=step,
        note=note,
        round_number=extra["approval_round"],
        what=f"{label} has to be signed again",
        action_url=first.get("signing_url"),
        action_label="Sign again",
        # The first signer in the routing order is who gets the new link, and
        # it is not always the candidate — an HR-then-candidate document starts
        # over from the top.
        to=first["email"],
        recipient_name=first.get("name"),
    )
    return {
        "status": ob_catalog.STATUS_PENDING_SIGNATURE,
        "envelope_id": envelope["envelope_id"],
        "signers": [{"role": s["role"], "email": s["email"]} for s in signers],
    }


async def _email_candidate_rejection(
    *,
    run: dict[str, Any],
    org_id: str,
    step: dict[str, Any],
    note: str,
    round_number: int,
    what: str,
    action_url: str | None,
    action_label: str | None,
    to: str | None = None,
    recipient_name: str | None = None,
) -> None:
    """Tell whoever has to redo it that it was sent back, and why.

    Best-effort: the step has already re-opened in the database by the time
    this runs, and a bounced email must not undo that. The run page shows the
    same note, and HR can nudge from there.

    Keyed by round so the second rejection of the same step is a new email
    rather than a duplicate the worker drops.
    """
    from app.config import get_settings
    from app.services.email import send_email_event

    settings = get_settings()
    try:
        await send_email_event(
            event_type="onboarding_step_rejected",
            to=to or run["candidate_email"],
            user_id=run.get("pre_join_user_id"),
            org_id=org_id,
            dedupe_key=(
                f"rejected-{run['id']}-{step['step_key']}-{round_number}"
            ),
            data={
                "candidate_name": recipient_name or run["candidate_name"],
                "step_label": step.get("bundle_label") or step.get("label"),
                "what_happened": what,
                "reason": note,
                "action_url": action_url,
                "action_label": action_label,
                "app_url": settings.app_url.rstrip("/") if settings.app_url else None,
            },
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("onboarding_v2.rejection_email_failed run=%s err=%s", run["id"], exc)


@router.post("/runs/{run_id}/steps/{step_key}/upload-signed")
async def upload_signed_step(
    run_id: str,
    step_key: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """HR uploads a scan of the document they signed on paper.

    The warnings are advisory, not rejections: a page count that looks wrong and
    a file byte-identical to the draft both usually mean "you forgot to sign it",
    but neither is certain enough to refuse the upload over.
    """
    user_id, org_id, _ = _require_user(current_user)
    _run, step, bundle = await _load_step(run_id, org_id, step_key)
    svc = get_service_client()

    if step["status"] != ob_catalog.STATUS_PENDING_SIGNATURE:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"'{step['label']}' isn't waiting for a signed copy.",
        )

    body = await file.read()
    if not body or not body.startswith(b"%PDF"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Upload must be a PDF file.")
    if len(body) > MAX_SIGNED_PDF_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "PDF too large (>25MB)."
        )

    label = step.get("bundle_label") or step["label"]
    warnings: list[str] = []
    try:
        import pymupdf  # type: ignore[import-not-found]

        with pymupdf.open(stream=body, filetype="pdf") as pdf:
            if pdf.page_count < 1:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "PDF has no pages.")
            if pdf.page_count > 50:
                warnings.append(
                    f"PDF has {pdf.page_count} pages — that's unusually long for "
                    f"{label}."
                )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("onboarding_v2.pdf_inspect_failed run=%s err=%s", run_id, exc)

    kinds = [m["step_key"] for m in bundle]
    try:
        existing = await asyncio.to_thread(
            lambda: svc.table("onboarding_documents")
            .select("storage_path")
            .eq("run_id", run_id)
            .eq("kind", kinds[0])
            .maybe_single()
            .execute()
        )
        draft_path = (existing.data or {}).get("storage_path") if existing else None
        if draft_path:
            draft = await asyncio.to_thread(
                lambda: svc.storage.from_(ob_storage.STORAGE_BUCKET).download(draft_path)
            )
            if draft and draft == body:
                warnings.append(
                    "This file is byte-identical to the unsigned draft — did you "
                    "forget to sign and scan it?"
                )
    except Exception as exc:  # noqa: BLE001
        log.warning("onboarding_v2.draft_compare_failed run=%s err=%s", run_id, exc)

    storage_path = f"orgs/{org_id}/onboarding/{run_id}/{step_key}_signed.pdf"

    def _upload() -> None:
        svc.storage.from_(ob_storage.STORAGE_BUCKET).upload(
            path=storage_path,
            file=body,
            file_options={"content-type": "application/pdf", "upsert": "true"},
        )

    await asyncio.to_thread(_upload)

    now = datetime.now(UTC).isoformat()
    # Every member of the bundle points at the one scan: HR signed the merged
    # document, so that is the signed copy of each of them.
    await asyncio.to_thread(
        lambda: svc.table("onboarding_documents")
        .update(
            {
                "signed_pdf_path": storage_path,
                "sign_status": "signed_by_hr",
                "signed_uploaded_by": user_id,
                "signed_uploaded_at": now,
            }
        )
        .eq("run_id", run_id)
        .in_("kind", kinds)
        .execute()
    )
    if _is_loi(step):
        await asyncio.to_thread(
            lambda: svc.table("onboarding_runs")
            .update({"loi_signed_at": now})
            .eq("id", run_id)
            .execute()
        )

    for member in bundle:
        await ob_catalog.set_step_status(member["id"], ob_catalog.STATUS_ACTIVE)

    await ob_storage.log_onboarding_event(
        org_id=org_id,
        run_id=run_id,
        actor_kind="hr",
        event_type="step_signed_uploaded",
        message=f"HR uploaded the signed {label}.",
        actor_user_id=user_id,
    )
    await _kick(run_id, org_id)
    return {"status": "signed_uploaded", "step_key": step_key, "warnings": warnings}
