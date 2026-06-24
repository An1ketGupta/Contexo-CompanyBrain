"""Autoflow engine — matching, execution, scheduled cron, approval gating.

The engine has three call paths:

  1. **Event-driven** — every Inngest event that should be able to fire an
     autoflow (document.ready, knowledge_gap.detected, …) sends an
     ``autoflow/trigger.fired`` event with ``{trigger_type, org_id, payload}``.
     ``dispatch_trigger`` (in ``app/inngest/autoflow_functions.py``) calls
     :func:`get_matching_autoflows`, then :func:`execute_autoflow_run` for
     each match.

  2. **Scheduled** — once a minute a single Inngest cron walks every active
     ``scheduled`` autoflow, calls :func:`scheduled_autoflows_due_now`, and
     fires the ones whose cron string matches. ``last_fired_at`` prevents a
     dispatcher restart from double-firing inside the same minute.

  3. **Resume after approval** — when an approval row with
     ``execution_action.channel == 'autoflow'`` is approved or rejected, the
     existing approvals worker sends ``autoflow/resume`` and we pick the
     ``autoflow_run`` back up at the next action.

Concurrency model:
    The Inngest function takes ``concurrency=[Concurrency(limit=1, key=...)]``
    on ``autoflow_id`` so a hot trigger (e.g. 50 document_ready events in a
    burst) doesn't fan out 50 simultaneous runs of the same autoflow against
    the same external APIs.

Why service-role for everything in this module:
    Autoflow execution is a background concern. There's no caller JWT to
    scope RLS off. Every query in here passes ``org_id`` explicitly, which
    is the only piece of multi-tenancy that matters in a worker context.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from datetime import UTC, datetime
from typing import Any

from app.database import get_service_client
from app.models.autoflow import (
    AutoflowAction,
    AutoflowActionType,
    AutoflowRunStatus,
    AutoflowTriggerType,
)
from app.observability import get_logger
from app.services.autoflow_actions import (
    ActionContext,
    ActionExecutionError,
    ActionHeldForApproval,
    get_handler,
)

log = get_logger(__name__)


# Length of the approval magic-link token. 32 random bytes hex-encoded is the
# pattern used by migration 028 — match it so the existing approvals UI can
# verify autoflow approvals with no special casing.
_APPROVAL_TOKEN_BYTES = 32
# Approval magic-links for autoflows expire after this many seconds.
_APPROVAL_TOKEN_TTL_SECONDS = 7 * 24 * 3600


# ── Matching: pick which autoflows fire for this trigger ──────────────────


async def get_matching_autoflows(
    *,
    org_id: str,
    trigger_type: AutoflowTriggerType | str,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """Active autoflows in this org with this trigger_type, post-filter applied.

    Filters live in ``trigger_config.filters`` and are checked against the
    payload after the SQL query. Keeping filters out of SQL avoids per-org
    index proliferation — the active count per (org, trigger) is small.
    """
    trigger_value = trigger_type.value if isinstance(trigger_type, AutoflowTriggerType) else trigger_type
    svc = get_service_client()

    def _run() -> list[dict[str, Any]]:
        res = (
            svc.table("autoflows")
            .select("*")
            .eq("org_id", org_id)
            .eq("trigger_type", trigger_value)
            .eq("is_active", True)
            .execute()
        )
        return res.data or []

    rows = await asyncio.to_thread(_run)

    matched: list[dict[str, Any]] = []
    for row in rows:
        cfg = row.get("trigger_config") or {}
        if _payload_matches_filters(payload, cfg.get("filters") or {}):
            matched.append(row)
    return matched


def _payload_matches_filters(payload: dict[str, Any], filters: dict[str, Any]) -> bool:
    """Right now: each filter key must equal payload[key]. List filter values
    are treated as 'in' membership (any value matches). Unknown payload keys
    cause the filter to fail-closed — better to skip a flow than fire one
    that targeted "HR uploads" against an engineering doc.
    """
    if not filters:
        return True
    for key, expected in filters.items():
        actual = payload.get(key)
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


# ── Run lifecycle ────────────────────────────────────────────────────────


async def open_autoflow_run(
    *,
    autoflow_id: str,
    org_id: str,
    trigger_payload: dict[str, Any],
    total_steps: int,
) -> str:
    """Insert a fresh autoflow_runs row in 'pending' state, return its id."""
    svc = get_service_client()

    def _run() -> str:
        res = (
            svc.table("autoflow_runs")
            .insert(
                {
                    "autoflow_id": autoflow_id,
                    "org_id": org_id,
                    "trigger_payload": trigger_payload,
                    "status": AutoflowRunStatus.PENDING.value,
                    "total_steps": total_steps,
                    "steps": [],
                }
            )
            .execute()
        )
        rows = res.data or []
        if not rows:
            raise RuntimeError("autoflow_run insert returned no rows")
        return rows[0]["id"]

    return await asyncio.to_thread(_run)


async def _update_run(run_id: str, fields: dict[str, Any]) -> None:
    svc = get_service_client()

    def _run() -> None:
        svc.table("autoflow_runs").update(fields).eq("id", run_id).execute()

    await asyncio.to_thread(_run)


async def _set_step(
    run_id: str,
    *,
    index: int,
    type: str,
    status: str,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    error: str | None = None,
    output: dict[str, Any] | None = None,
) -> None:
    """Idempotent step upsert into autoflow_runs.steps JSONB.

    We re-read the row to merge — would be a race if two steps could write
    concurrently, but the executor walks actions strictly in order, so this
    is single-writer per run.
    """
    svc = get_service_client()

    def _run() -> None:
        cur = svc.table("autoflow_runs").select("steps, steps_completed").eq("id", run_id).single().execute()
        existing = (cur.data or {}).get("steps") or []
        existing = list(existing)
        # Pad if a step was skipped.
        while len(existing) <= index:
            existing.append({"index": len(existing), "type": "", "status": "pending"})
        entry: dict[str, Any] = {
            "index": index,
            "type": type,
            "status": status,
        }
        if started_at:
            entry["started_at"] = started_at.isoformat()
        if completed_at:
            entry["completed_at"] = completed_at.isoformat()
        if error:
            entry["error"] = error
        if output is not None:
            entry["output"] = output
        existing[index] = entry
        # steps_completed only counts terminal-success steps.
        completed = sum(1 for s in existing if s.get("status") == "completed")
        svc.table("autoflow_runs").update(
            {"steps": existing, "steps_completed": completed}
        ).eq("id", run_id).execute()

    await asyncio.to_thread(_run)


async def _touch_last_fired(autoflow_id: str) -> None:
    svc = get_service_client()

    def _run() -> None:
        svc.table("autoflows").update({"last_fired_at": datetime.now(UTC).isoformat()}).eq(
            "id", autoflow_id
        ).execute()

    await asyncio.to_thread(_run)


# ── Execution ────────────────────────────────────────────────────────────


async def execute_autoflow_run(
    *,
    autoflow: dict[str, Any],
    trigger_payload: dict[str, Any],
    run_id: str | None = None,
    resume_from_index: int = 0,
    resume_prior_outputs: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run actions in order. Returns the final state of the run.

    Failure semantics:
        * Any ``ActionExecutionError`` marks that step failed AND fails the
          whole run. We don't have per-step retry config yet — Inngest's
          function-level retry covers transient infra failures. Authoring
          retry logic per action would invite a config explosion before we
          know what real users need.
        * ``ActionHeldForApproval`` is *not* a failure — it stops the loop,
          marks the run ``held_for_approval``, and the resume path picks up
          at the next action when the approval is decided.
    """
    autoflow_id = autoflow["id"]
    org_id = autoflow["org_id"]
    raw_actions = autoflow.get("actions") or []
    confidence_threshold = autoflow.get("confidence_threshold")

    # Parse + sort actions. Bad shapes fail the run immediately rather than
    # silently dropping a step.
    try:
        parsed = [AutoflowAction(**a) for a in raw_actions]
    except Exception as exc:
        log.exception("autoflow.actions.parse_failed", autoflow_id=autoflow_id)
        if run_id is None:
            run_id = await open_autoflow_run(
                autoflow_id=autoflow_id, org_id=org_id, trigger_payload=trigger_payload, total_steps=0
            )
        await _update_run(
            run_id,
            {
                "status": AutoflowRunStatus.FAILED.value,
                "error_message": f"actions_parse_failed: {exc}",
                "completed_at": datetime.now(UTC).isoformat(),
            },
        )
        return {"run_id": run_id, "status": "failed"}

    actions_sorted = sorted(parsed, key=lambda a: a.order)
    total_steps = len(actions_sorted)

    if run_id is None:
        run_id = await open_autoflow_run(
            autoflow_id=autoflow_id,
            org_id=org_id,
            trigger_payload=trigger_payload,
            total_steps=total_steps,
        )

    await _update_run(run_id, {"status": AutoflowRunStatus.RUNNING.value})
    await _touch_last_fired(autoflow_id)

    prior_outputs: dict[str, dict[str, Any]] = dict(resume_prior_outputs or {})

    for idx, action in enumerate(actions_sorted):
        if idx < resume_from_index:
            continue  # already executed in a previous run cycle

        started = datetime.now(UTC)
        await _set_step(
            run_id, index=idx, type=action.type.value, status="running", started_at=started
        )

        ctx = ActionContext(
            org_id=org_id,
            autoflow_id=autoflow_id,
            run_id=run_id,
            action_index=idx,
            action=action,
            trigger_payload=trigger_payload,
            prior_outputs=prior_outputs,
            confidence_threshold=confidence_threshold,
        )

        try:
            handler = get_handler(action.type)
            output = await handler(ctx)
        except ActionHeldForApproval as hold:
            await _set_step(
                run_id,
                index=idx,
                type=action.type.value,
                status="held_for_approval",
                started_at=started,
                completed_at=datetime.now(UTC),
                output={
                    "approval_id": hold.approval_id,
                    "preview_text": hold.preview_text,
                },
            )
            await _update_run(
                run_id,
                {
                    "status": AutoflowRunStatus.HELD_FOR_APPROVAL.value,
                    "blocking_approval_id": hold.approval_id,
                },
            )
            log.info(
                "autoflow.run.held",
                run_id=run_id,
                autoflow_id=autoflow_id,
                action_index=idx,
                approval_id=hold.approval_id,
            )
            return {"run_id": run_id, "status": "held_for_approval", "approval_id": hold.approval_id}
        except ActionExecutionError as exc:
            await _set_step(
                run_id,
                index=idx,
                type=action.type.value,
                status="failed",
                started_at=started,
                completed_at=datetime.now(UTC),
                error=str(exc),
            )
            await _update_run(
                run_id,
                {
                    "status": AutoflowRunStatus.FAILED.value,
                    "error_message": f"action_{idx}: {exc}",
                    "completed_at": datetime.now(UTC).isoformat(),
                },
            )
            log.warning(
                "autoflow.run.failed",
                run_id=run_id,
                autoflow_id=autoflow_id,
                action_index=idx,
                error=str(exc),
            )
            return {"run_id": run_id, "status": "failed", "action_index": idx}
        except Exception as exc:  # noqa: BLE001 - last-resort guard
            log.exception(
                "autoflow.run.crashed",
                run_id=run_id,
                autoflow_id=autoflow_id,
                action_index=idx,
            )
            await _set_step(
                run_id,
                index=idx,
                type=action.type.value,
                status="failed",
                started_at=started,
                completed_at=datetime.now(UTC),
                error=f"unhandled: {exc}",
            )
            await _update_run(
                run_id,
                {
                    "status": AutoflowRunStatus.FAILED.value,
                    "error_message": f"action_{idx}: unhandled exception",
                    "completed_at": datetime.now(UTC).isoformat(),
                },
            )
            return {"run_id": run_id, "status": "failed", "action_index": idx}

        prior_outputs[str(idx)] = output or {}
        await _set_step(
            run_id,
            index=idx,
            type=action.type.value,
            status="completed",
            started_at=started,
            completed_at=datetime.now(UTC),
            output=output,
        )

    await _update_run(
        run_id,
        {
            "status": AutoflowRunStatus.COMPLETED.value,
            "completed_at": datetime.now(UTC).isoformat(),
        },
    )
    log.info(
        "autoflow.run.completed",
        run_id=run_id,
        autoflow_id=autoflow_id,
        steps=total_steps,
    )
    return {"run_id": run_id, "status": "completed"}


# ── Scheduled cron evaluation ────────────────────────────────────────────


async def scheduled_autoflows_due_now(*, now: datetime | None = None) -> list[dict[str, Any]]:
    """Active scheduled autoflows whose cron matches the current minute.

    Dedupe: skip rows whose ``last_fired_at`` is within 55 seconds of *now*.
    A bit looser than 60s to absorb clock skew between the Inngest scheduler
    and our worker without ever double-firing.
    """
    from croniter import croniter  # local import — croniter is optional dep, see pyproject

    svc = get_service_client()
    now = now or datetime.now(UTC)

    def _run() -> list[dict[str, Any]]:
        res = (
            svc.table("autoflows")
            .select("*")
            .eq("trigger_type", AutoflowTriggerType.SCHEDULED.value)
            .eq("is_active", True)
            .execute()
        )
        return res.data or []

    rows = await asyncio.to_thread(_run)
    due: list[dict[str, Any]] = []
    for row in rows:
        cfg = row.get("trigger_config") or {}
        cron_expr = cfg.get("cron")
        if not cron_expr:
            continue
        try:
            # Anchor on (now - 1min) so croniter.get_next gives us the slot
            # that "owns" this minute. If that next-slot is within the
            # current minute, the autoflow is due.
            anchor = now.replace(second=0, microsecond=0)
            it = croniter(cron_expr, anchor.replace(second=0) - _timedelta(minutes=1))
            next_fire = it.get_next(datetime)
        except Exception as exc:
            log.warning(
                "autoflow.scheduled.bad_cron",
                autoflow_id=row.get("id"),
                cron=cron_expr,
                error=str(exc),
            )
            continue
        if not (anchor <= next_fire < anchor + _timedelta(minutes=1)):
            continue
        # Dedupe by last_fired_at
        last = row.get("last_fired_at")
        if last:
            try:
                last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
                if (now - last_dt).total_seconds() < 55:
                    continue
            except ValueError:
                pass
        due.append(row)
    return due


def _timedelta(**kwargs):  # tiny shim so the import stays at module top
    from datetime import timedelta
    return timedelta(**kwargs)


# ── Approval gating ──────────────────────────────────────────────────────


async def open_run_approval(
    *,
    org_id: str,
    autoflow_run_id: str,
    execution_action: dict[str, Any],
    preview_text: str | None = None,
    note: str | None = None,
    approver_user_id: str | None = None,
) -> str:
    """Open an ``approvals`` row gating this autoflow_run.

    Approver selection: if ``approver_user_id`` is given, use it. Otherwise
    pick the org's most recently active admin (admins are the catch-all
    approvers in the existing approvals UI; mirrors what the message-approval
    flow does when no explicit reviewer is set).
    """
    from app.config import get_settings

    settings = get_settings()
    svc = get_service_client()

    # Requester = the autoflow's creator (the user who set up this rule). The
    # CHECK constraint on `approvals` rejects requester == approver, so we
    # have to pick an admin who isn't the creator.
    def _fetch_autoflow_for_run() -> dict[str, Any]:
        run_row = (
            svc.table("autoflow_runs").select("autoflow_id").eq("id", autoflow_run_id).single().execute()
        )
        autoflow_id = (run_row.data or {}).get("autoflow_id")
        if not autoflow_id:
            raise RuntimeError(f"autoflow_run_not_found: {autoflow_run_id}")
        af_row = svc.table("autoflows").select("created_by").eq("id", autoflow_id).single().execute()
        creator = (af_row.data or {}).get("created_by")
        if not creator:
            raise RuntimeError(f"autoflow_not_found: {autoflow_id}")
        return {"autoflow_id": autoflow_id, "created_by": creator}

    af_meta = await asyncio.to_thread(_fetch_autoflow_for_run)
    requester_id = af_meta["created_by"]

    if not approver_user_id:
        def _pick_admin(exclude_id: str) -> str | None:
            res = (
                svc.table("users")
                .select("id")
                .eq("org_id", org_id)
                .eq("role", "admin")
                .neq("id", exclude_id)
                .order("created_at", desc=False)
                .limit(1)
                .execute()
            )
            rows = res.data or []
            return rows[0]["id"] if rows else None

        approver_user_id = await asyncio.to_thread(_pick_admin, requester_id)
        if not approver_user_id:
            raise RuntimeError(
                "no_admin_to_approve: cannot hold autoflow run for approval without an admin in the org"
            )
    elif approver_user_id == requester_id:
        raise RuntimeError(
            "self_approval_forbidden: approver_user_id must differ from the autoflow's creator"
        )

    # Magic-link token (same shape as migration 028).
    raw_token = secrets.token_hex(_APPROVAL_TOKEN_BYTES)
    token_hash = hashlib.sha256(
        (settings.internal_email_secret + raw_token).encode("utf-8")
    ).hexdigest()
    expires_at = (datetime.now(UTC) + _timedelta(seconds=_APPROVAL_TOKEN_TTL_SECONDS)).isoformat()

    def _insert() -> str:
        res = (
            svc.table("approvals")
            .insert(
                {
                    "org_id": org_id,
                    "subject_type": "autoflow_run",
                    "subject_id": autoflow_run_id,
                    "message_id": None,
                    "requested_by": requester_id,
                    "approver_id": approver_user_id,
                    "status": "pending",
                    "note": note,
                    "preview_text": preview_text,
                    "execution_action": execution_action,
                    "token_hash": token_hash,
                    "token_expires_at": expires_at,
                }
            )
            .execute()
        )
        rows = res.data or []
        if not rows:
            raise RuntimeError("approvals insert returned no rows")
        return rows[0]["id"]

    approval_id = await asyncio.to_thread(_insert)

    # Emit the same approval.requested webhook the message-approval flow uses.
    from app.services.webhooks import trigger_event

    await trigger_event(
        org_id=org_id,
        event="approval.requested",
        payload={
            "approval_id": approval_id,
            "subject_type": "autoflow_run",
            "subject_id": autoflow_run_id,
            "preview_text": preview_text,
        },
    )

    return approval_id


# ── Resume after approval ───────────────────────────────────────────────


async def resume_run_after_approval(
    *,
    approval_id: str,
    approved: bool,
) -> dict[str, Any]:
    """Continue (or fail) an autoflow_run whose approval was just decided.

    Called from the approvals webhook handler. Looks up the gated run via
    ``autoflow_runs.blocking_approval_id``, then either continues from the
    next action (approved) or fails the run (rejected).
    """
    svc = get_service_client()

    def _fetch_run() -> dict[str, Any] | None:
        res = (
            svc.table("autoflow_runs")
            .select("*")
            .eq("blocking_approval_id", approval_id)
            .single()
            .execute()
        )
        return res.data

    run = await asyncio.to_thread(_fetch_run)
    if not run:
        return {"status": "not_found"}

    if not approved:
        await _update_run(
            run["id"],
            {
                "status": AutoflowRunStatus.CANCELLED.value,
                "error_message": "rejected_at_approval",
                "completed_at": datetime.now(UTC).isoformat(),
                "blocking_approval_id": None,
            },
        )
        return {"status": "cancelled", "run_id": run["id"]}

    # Fetch the parent autoflow for action list + threshold.
    def _fetch_autoflow() -> dict[str, Any] | None:
        res = (
            svc.table("autoflows")
            .select("*")
            .eq("id", run["autoflow_id"])
            .single()
            .execute()
        )
        return res.data

    autoflow = await asyncio.to_thread(_fetch_autoflow)
    if not autoflow:
        await _update_run(
            run["id"],
            {
                "status": AutoflowRunStatus.FAILED.value,
                "error_message": "parent_autoflow_missing",
                "blocking_approval_id": None,
                "completed_at": datetime.now(UTC).isoformat(),
            },
        )
        return {"status": "failed", "run_id": run["id"]}

    # Rebuild prior_outputs from steps; resume from the held step + 1.
    steps = run.get("steps") or []
    held_index = max(
        (s.get("index", 0) for s in steps if s.get("status") == "held_for_approval"),
        default=-1,
    )
    prior_outputs: dict[str, dict[str, Any]] = {}
    for s in steps:
        if s.get("status") == "completed" and isinstance(s.get("output"), dict):
            prior_outputs[str(s["index"])] = s["output"]

    # Clear the blocking pointer so the run is no longer "held".
    await _update_run(run["id"], {"blocking_approval_id": None})

    return await execute_autoflow_run(
        autoflow=autoflow,
        trigger_payload=run.get("trigger_payload") or {},
        run_id=run["id"],
        resume_from_index=held_index + 1,
        resume_prior_outputs=prior_outputs,
    )


# ── Convenience: emit a trigger event from anywhere in the app ──────────


async def emit_trigger(
    *,
    org_id: str,
    trigger_type: AutoflowTriggerType | str,
    payload: dict[str, Any],
) -> None:
    """Send an ``autoflow/trigger.fired`` Inngest event.

    The actual dispatch happens in the Inngest worker
    (app/inngest/autoflow_functions.py). Call sites are expected to be cheap
    and best-effort — a failed enqueue logs and moves on rather than blocking
    whatever side-effect the caller was actually doing.
    """
    import inngest

    from app.inngest.client import get_inngest_client

    client = get_inngest_client()
    trigger_value = (
        trigger_type.value if isinstance(trigger_type, AutoflowTriggerType) else trigger_type
    )
    try:
        await client.send(
            inngest.Event(
                name="autoflow/trigger.fired",
                data={
                    "trigger_type": trigger_value,
                    "org_id": org_id,
                    "payload": payload,
                },
            )
        )
    except Exception as exc:
        log.warning(
            "autoflow.trigger.enqueue_failed",
            org_id=org_id,
            trigger_type=trigger_value,
            error=str(exc),
        )
