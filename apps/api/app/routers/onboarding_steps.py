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
