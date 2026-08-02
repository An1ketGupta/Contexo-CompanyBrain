"""Inngest pipeline for OnboardingV2Agent.

The agent is re-entrant: each step transitions `onboarding_runs.status` and
the next event re-kicks the agent which inspects status and dispatches. We
expose a small set of events:

  onboarding_v2/start             - HR clicks "Mark Hired & Start Onboarding"
  onboarding_v2/loi_signed_uploaded - HR uploads signed-LOIPDF
  onboarding_v2/bgv_response      - A reference submits the public form
  onboarding_v2/policy_ack_changed - An acknowledgement row flipped
  onboarding_v2/template_uploaded  - HR uploaded a previously-missing template
  onboarding_v2/resume            - generic "kick the run again"

Plus a daily cron for BGV reminders.

Concurrency: capped at 1 per onboarding_run so two retries can't race against
the same row. Org-level concurrency is left open — different hires onboard
independently.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import inngest

from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.observability import get_logger
from app.services.agents.onboarding_v2 import OnboardingV2Agent
from app.services.agents.onboarding_v2 import catalog as ob_catalog
from app.services.email import send_email_event

log = get_logger(__name__)

_inngest_client = get_inngest_client()


async def _drive_agent(
    *, run_id: str, org_id: str, triggered_by_user_id: str | None = None
) -> dict[str, Any]:
    agent = OnboardingV2Agent(
        org_id=org_id,
        run_id=run_id,
        triggered_by_user_id=triggered_by_user_id,
    )
    try:
        return await agent.run_safely()
    except Exception as exc:
        log.warning("onboarding_v2.run_failed run_id=%s err=%s", run_id, exc)
        raise


@_inngest_client.create_function(
    fn_id="onboarding-v2-start",
    trigger=inngest.TriggerEvent(event="onboarding_v2/start"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.onboarding_run_id", scope="fn"),
    ],
)
async def onboarding_v2_start(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    run_id = data.get("onboarding_run_id")
    org_id = data.get("org_id")
    if not run_id or not org_id:
        return {"status": "skipped", "reason": "missing_required_fields"}
    return await _drive_agent(
        run_id=run_id,
        org_id=org_id,
        triggered_by_user_id=data.get("triggered_by_user_id"),
    )


@_inngest_client.create_function(
    fn_id="onboarding-v2-loi-signed-uploaded",
    trigger=inngest.TriggerEvent(event="onboarding_v2/loi_signed_uploaded"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.onboarding_run_id", scope="fn"),
    ],
)
async def onboarding_v2_loi_signed(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    run_id = data.get("onboarding_run_id")
    org_id = data.get("org_id")
    if not run_id or not org_id:
        return {"status": "skipped", "reason": "missing_required_fields"}

    # The HR upload handler already set the status to loi_signed_uploaded and
    # stamped onboarding_documents.signed_pdf_path. Drive the agent to pick up
    # from there.
    return await _drive_agent(run_id=run_id, org_id=org_id)


@_inngest_client.create_function(
    fn_id="onboarding-v2-bgv-response",
    trigger=inngest.TriggerEvent(event="onboarding_v2/bgv_response"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.onboarding_run_id", scope="fn"),
    ],
)
async def onboarding_v2_bgv_response(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    run_id = data.get("onboarding_run_id")
    org_id = data.get("org_id")
    if not run_id or not org_id:
        return {"status": "skipped"}
    return await _drive_agent(run_id=run_id, org_id=org_id)


@_inngest_client.create_function(
    fn_id="onboarding-v2-policy-ack-changed",
    trigger=inngest.TriggerEvent(event="onboarding_v2/policy_ack_changed"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.onboarding_run_id", scope="fn"),
    ],
)
async def onboarding_v2_policy_ack(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    run_id = data.get("onboarding_run_id")
    org_id = data.get("org_id")
    if not run_id or not org_id:
        return {"status": "skipped"}
    return await _drive_agent(run_id=run_id, org_id=org_id)


@_inngest_client.create_function(
    fn_id="onboarding-v2-template-uploaded",
    trigger=inngest.TriggerEvent(event="onboarding_v2/template_uploaded"),
    retries=2,
)
async def onboarding_v2_template_uploaded(ctx: inngest.Context) -> dict[str, Any]:
    """Fan-out: HR uploaded a template (e.g. LOI). Resume any blocked runs in
    this org that were stuck on the corresponding template_kind."""
    data = ctx.event.data
    org_id = data.get("org_id")
    template_kind = data.get("template_kind")
    if not org_id or not template_kind:
        return {"status": "skipped"}

    svc = get_service_client()
    res = svc.table("onboarding_runs").select("id").eq("org_id", org_id).eq(
        "status", "blocked_missing_template"
    ).eq("blocked_template_kind", template_kind).execute()
    rows = res.data or []
    resumed = 0
    for r in rows:
        await _drive_agent(run_id=r["id"], org_id=org_id)
        resumed += 1
    return {"status": "ok", "resumed": resumed}


@_inngest_client.create_function(
    fn_id="onboarding-v2-esign-completed",
    trigger=inngest.TriggerEvent(event="onboarding_v2/esign_completed"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.onboarding_run_id", scope="fn"),
    ],
)
async def onboarding_v2_esign_completed(ctx: inngest.Context) -> dict[str, Any]:
    """The candidate completed the AL+NDA signing envelope. Kick the agent
    so it transitions to the policies step. apps/esign already updated
    onboarding_documents.sign_status to signed_by_candidate directly (no
    webhook hop — see apps/esign/app/routers/public_sign.py)."""
    data = ctx.event.data
    run_id = data.get("onboarding_run_id")
    org_id = data.get("org_id")
    if not run_id or not org_id:
        return {"status": "skipped"}
    return await _drive_agent(run_id=run_id, org_id=org_id)


async def _sign_document_label(run_id: str | None, kinds: tuple[str, ...]) -> str:
    """What to call the thing a signer is being asked to sign.

    Read from the run's own steps, because the documents in an envelope are
    whatever the org bundled together — a fixed map of kind-tuples to labels
    could only ever name the two combinations that used to be possible.
    """
    if not run_id or not kinds:
        return "your document"
    try:
        steps = await ob_catalog.get_run_steps(run_id)
        matched = [s for s in steps if s["step_key"] in kinds]
        if matched:
            lead = min(matched, key=lambda s: s.get("position") or 0)
            return lead.get("bundle_label") or lead.get("label") or "your document"
    except Exception as exc:  # noqa: BLE001
        log.warning("onboarding_v2.sign_label_lookup_failed run=%s err=%s", run_id, exc)
    return "your document"


@_inngest_client.create_function(
    fn_id="onboarding-v2-esign-signer-turn",
    trigger=inngest.TriggerEvent(event="esign/signer_turn"),
    retries=2,
)
async def onboarding_v2_esign_signer_turn(ctx: inngest.Context) -> dict[str, Any]:
    """apps/esign fires this once a signer completes and another signer is
    next in a routed envelope (e.g. HR signed the LOI, candidate is up).
    This is the one piece of orchestration apps/esign delegates back to
    apps/api — sending email stays centralised here (Resend creds, existing
    templates/dispatcher) rather than duplicated in the signing service."""
    from app.config import get_settings

    data = ctx.event.data
    signer_email = data.get("signer_email")
    signer_name = data.get("signer_name")
    signer_role = data.get("signer_role") or "signer"
    envelope_id = data.get("envelope_id")
    signing_url = data.get("signing_url")
    if not signer_email or not envelope_id or not signing_url:
        return {"status": "skipped", "reason": "missing_required_fields"}

    settings = get_settings()
    kinds = tuple(data.get("document_kinds") or ())
    document_label = await _sign_document_label(data.get("run_id"), kinds)

    # Resolve HR's user_id from onboarding_runs for dedupe/analytics — the
    # candidate signer has none, which send_email_event already handles.
    # Decided by role rather than by comparing addresses: HR and the candidate
    # are often the same mailbox, and an address comparison read HR's own turn
    # as the candidate's and dropped their user_id.
    svc = get_service_client()
    run_row = await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .select("triggered_by_user_id")
        .eq("id", data.get("run_id"))
        .maybe_single()
        .execute()
    )
    user_id = (
        (run_row.data or {}).get("triggered_by_user_id")
        if run_row and signer_role == "hr"
        else None
    )

    await send_email_event(
        event_type="onboarding_sign_your_turn",
        to=signer_email,
        user_id=user_id,
        org_id=data.get("org_id"),
        # Role is part of the key for the same reason: a shared mailbox made
        # the second signer's turn indistinguishable from the first's.
        dedupe_key=f"sign-your-turn-{envelope_id}-{signer_role}-{signer_email}",
        data={
            "recipient_name": signer_name or signer_email,
            "document_label": document_label,
            "signing_url": signing_url,
            "app_url": settings.app_url.rstrip("/"),
        },
    )
    return {"status": "ok"}


@_inngest_client.create_function(
    fn_id="onboarding-v2-resume",
    trigger=inngest.TriggerEvent(event="onboarding_v2/resume"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.onboarding_run_id", scope="fn"),
    ],
)
async def onboarding_v2_resume(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    run_id = data.get("onboarding_run_id")
    org_id = data.get("org_id")
    if not run_id or not org_id:
        return {"status": "skipped"}
    return await _drive_agent(run_id=run_id, org_id=org_id)


@_inngest_client.create_function(
    fn_id="onboarding-v2-collect-submitted",
    trigger=inngest.TriggerEvent(event="onboarding_v2/collect_submitted"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.onboarding_run_id", scope="fn"),
    ],
)
async def onboarding_v2_collect_submitted(ctx: inngest.Context) -> dict[str, Any]:
    """The candidate finished a document-collection step.

    Fired only once every required document is in, so this re-drives a run
    that has been parked at that step's gate — the gate now opens and the
    pipeline picks up wherever the status ladder left it.
    """
    data = ctx.event.data
    run_id = data.get("onboarding_run_id")
    org_id = data.get("org_id")
    if not run_id or not org_id:
        return {"status": "skipped"}
    return await _drive_agent(run_id=run_id, org_id=org_id)


# ── BGV reminders cron — runs daily, nudges references who haven't responded ──

@_inngest_client.create_function(
    fn_id="onboarding-v2-bgv-reminders",
    trigger=inngest.TriggerCron(cron="0 9 * * *"),  # daily 09:00 UTC
    retries=1,
)
async def onboarding_v2_bgv_reminders(ctx: inngest.Context) -> dict[str, Any]:
    """For every BGV reference still in `sent` or `opened` state for >3 days
    and with <3 reminders, send a polite nudge. Bounded so a buggy run can't
    spam someone."""
    svc = get_service_client()
    cutoff = datetime.now(UTC).replace(microsecond=0)

    res = (
        svc.table("onboarding_bgv_references")
        .select(
            "id, org_id, run_id, reference_email, reference_name, token, "
            "reminder_count, email_sent_at, last_reminder_at, status"
        )
        .in_("status", ["sent", "opened"])
        .lt("reminder_count", 3)
        .execute()
    )
    rows = res.data or []
    nudged = 0
    for r in rows:
        # Last touch = last_reminder_at or email_sent_at. Skip if <3 days.
        last_touch_iso = r.get("last_reminder_at") or r.get("email_sent_at")
        if not last_touch_iso:
            continue
        try:
            last_touch = datetime.fromisoformat(last_touch_iso.replace("Z", "+00:00"))
        except ValueError:
            continue
        if (cutoff - last_touch).days < 3:
            continue

        # Pull the run's candidate name + org name for personalisation.
        run_row = svc.table("onboarding_runs").select(
            "candidate_name, role_title"
        ).eq("id", r["run_id"]).maybe_single().execute()
        org_row = svc.table("organizations").select("name").eq(
            "id", r["org_id"]
        ).maybe_single().execute()
        candidate_name = (run_row.data or {}).get("candidate_name", "the candidate")
        company_name = (org_row.data or {}).get("name", "the team")

        from app.config import get_settings
        settings = get_settings()
        base = (
            settings.bgv_public_url.rstrip("/")
            if settings.bgv_public_url
            else settings.app_url.rstrip("/")
        )
        form_url = f"{base}/bgv/{r['token']}"

        try:
            await send_email_event(
                event_type="onboarding_bgv_reminder",
                to=r["reference_email"],
                user_id=None,
                org_id=r["org_id"],
                dedupe_key=f"bgv-rem-{r['id']}-{r['reminder_count']}",
                data={
                    "reference_name": r["reference_name"],
                    "candidate_name": candidate_name,
                    "company_name": company_name,
                    "form_url": form_url,
                },
            )
            svc.table("onboarding_bgv_references").update(
                {
                    "reminder_count": (r.get("reminder_count") or 0) + 1,
                    "last_reminder_at": cutoff.isoformat(),
                }
            ).eq("id", r["id"]).execute()
            nudged += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("onboarding_v2.bgv_reminder_failed ref=%s err=%s", r["id"], exc)
    return {"status": "ok", "nudged": nudged}


# ── Candidate references reminder cron ──────────────────────────────────────


@_inngest_client.create_function(
    fn_id="onboarding-v2-candidate-refs-reminders",
    trigger=inngest.TriggerCron(cron="0 10 * * *"),  # daily 10:00 UTC
    retries=1,
)
async def onboarding_v2_candidate_refs_reminders(
    ctx: inngest.Context,
) -> dict[str, Any]:
    """Nudge candidates parked in awaiting_candidate_references whose form
    has been idle for >3 days. Capped at 3 reminders per run so a ghosted
    candidate doesn't keep getting pinged."""
    svc = get_service_client()
    cutoff = datetime.now(UTC).replace(microsecond=0)

    res = (
        svc.table("onboarding_runs")
        .select(
            "id, org_id, candidate_name, candidate_email, role_title, "
            "references_form_token, references_form_expires_at, "
            "references_reminder_count, references_last_reminder_at, "
            "loi_sent_to_candidate_at"
        )
        .eq("status", "awaiting_candidate_references")
        .lt("references_reminder_count", 3)
        .execute()
    )
    rows = res.data or []
    nudged = 0
    for r in rows:
        last_touch_iso = (
            r.get("references_last_reminder_at") or r.get("loi_sent_to_candidate_at")
        )
        if not last_touch_iso:
            continue
        try:
            last_touch = datetime.fromisoformat(last_touch_iso.replace("Z", "+00:00"))
        except ValueError:
            continue
        if (cutoff - last_touch).days < 3:
            continue

        # Don't reminder past form expiry — the link is dead.
        expires_at_iso = r.get("references_form_expires_at")
        if expires_at_iso:
            try:
                expires_at = datetime.fromisoformat(
                    expires_at_iso.replace("Z", "+00:00")
                )
                if expires_at < cutoff:
                    continue
            except ValueError:
                pass

        from app.config import get_settings
        settings = get_settings()
        form_url = (
            f"{settings.app_url.rstrip('/')}/references/{r['references_form_token']}"
            if settings.app_url and r.get("references_form_token") else None
        )
        if not form_url:
            continue

        org_row = svc.table("organizations").select("name").eq(
            "id", r["org_id"]
        ).maybe_single().execute()
        company_name = (org_row.data or {}).get("name", "the hiring team")

        try:
            await send_email_event(
                event_type="onboarding_candidate_refs_reminder",
                to=r["candidate_email"],
                user_id=None,
                org_id=r["org_id"],
                dedupe_key=f"cand-refs-rem-{r['id']}-{r['references_reminder_count']}",
                data={
                    "candidate_name": r["candidate_name"],
                    "company_name": company_name,
                    "role_title": r.get("role_title") or "",
                    "form_url": form_url,
                },
            )
            svc.table("onboarding_runs").update(
                {
                    "references_reminder_count": (r.get("references_reminder_count") or 0) + 1,
                    "references_last_reminder_at": cutoff.isoformat(),
                }
            ).eq("id", r["id"]).execute()
            nudged += 1
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "onboarding_v2.candidate_refs_reminder_failed run=%s err=%s",
                r["id"], exc,
            )
    return {"status": "ok", "nudged": nudged}


@_inngest_client.create_function(
    fn_id="onboarding-v2-esign-timeout-watch",
    trigger=inngest.TriggerCron(cron="0 8 * * *"),  # daily 08:00 UTC
    retries=1,
)
async def onboarding_v2_esign_timeout_watch(
    ctx: inngest.Context,
) -> dict[str, Any]:
    """Alert HR when an LOIsigning envelope has been outstanding for >48 h.

    We deliberately do NOT auto-void — the candidate may still be in the
    process of signing. HR can decide whether to chase the candidate or void
    the envelope (POST /runs/{id}/cancel) and restart the signing step
    manually."""
    from datetime import timedelta

    from app.config import get_settings

    svc = get_service_client()
    now = datetime.now(UTC)
    stale_before = (now - timedelta(hours=48)).isoformat()

    res = svc.table("onboarding_runs").select(
        "id, org_id, candidate_name, role_title, "
        "loi_approved_for_signing_at, triggered_by_user_id"
    ).eq("status", "loi_pending_esign_signature").lt(
        "loi_approved_for_signing_at", stale_before
    ).execute()

    rows = res.data or []
    alerted = 0
    settings = get_settings()
    today = now.strftime("%Y-%m-%d")

    for r in rows:
        triggered_by = r.get("triggered_by_user_id")
        if not triggered_by:
            continue
        try:
            au = svc.auth.admin.get_user_by_id(triggered_by)
            hr_email = getattr(getattr(au, "user", None), "email", None)
        except Exception:
            hr_email = None
        if not hr_email:
            continue

        hours_elapsed = 0
        approved_at = r.get("loi_approved_for_signing_at") or ""
        if approved_at:
            try:
                sent_dt = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
                hours_elapsed = int((now - sent_dt).total_seconds() / 3600)
            except Exception:
                pass

        run_url = (
            f"{settings.app_url.rstrip('/')}/onboarding/{r['id']}"
            if settings.app_url
            else ""
        )

        try:
            await send_email_event(
                event_type="onboarding_esign_stalled",
                to=hr_email,
                user_id=triggered_by,
                org_id=r["org_id"],
                dedupe_key=f"esign-stalled-{r['id']}-{today}",
                data={
                    "candidate_name": r["candidate_name"],
                    "role_title": r.get("role_title") or "",
                    "hours_elapsed": hours_elapsed,
                    "run_url": run_url,
                },
            )
            alerted += 1
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "onboarding_v2.esign_stall_alert_failed run=%s err=%s",
                r["id"], exc,
            )

    return {"status": "ok", "alerted": alerted}


FUNCTIONS = [
    onboarding_v2_start,
    onboarding_v2_loi_signed,
    onboarding_v2_bgv_response,
    onboarding_v2_policy_ack,
    onboarding_v2_template_uploaded,
    onboarding_v2_esign_completed,
    onboarding_v2_esign_signer_turn,
    onboarding_v2_resume,
    onboarding_v2_collect_submitted,
    onboarding_v2_bgv_reminders,
    onboarding_v2_candidate_refs_reminders,
    onboarding_v2_esign_timeout_watch,
]
