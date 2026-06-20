"""Conversation auto-archive + auto-delete crons (V3 #104, production-grade).

Two cron functions:

  1. ``conversations-auto-archive`` — daily at 03:00 UTC.
     Calls the ``run_conversation_auto_archive`` Postgres RPC, which uses a
     single CTE to archive every eligible conversation across every org in
     one statement. Per-org thresholds live in ``organizations.metadata.archive``
     so there is no N+1 fan-out and no per-org Inngest step explosion.

  2. ``conversations-auto-delete`` — daily at 03:15 UTC.
     Honours the optional retention tier. Orgs that opt in by setting
     ``metadata.archive.delete_after_archive_days`` get archived rows beyond
     that age hard-deleted (messages cascade via the FK). Default = NULL =
     never delete, so this is a no-op for most orgs.

Both functions log structured ``archive.cron.ran`` events with org-level
counts so the admin dashboard / SQL queries can answer "did this run, when,
and how many rows did it touch?" without us standing up a separate
metrics pipeline.

The actual eligibility logic lives in Postgres (see migration 038). Keeping
it there means the cron is one round trip and the rules are visible to
anyone reading the migration — no scattered Python predicates.
"""
from __future__ import annotations

import asyncio
from typing import Any

import inngest

from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.observability import get_logger

log = get_logger(__name__)

_inngest_client = get_inngest_client()


# ── Daily archive cron ────────────────────────────────────────────────────


@_inngest_client.create_function(
    fn_id="conversations-auto-archive",
    # 03:00 UTC. Off-peak globally; leaves the 09:00 compliance slot clear so
    # the two crons never contend for the same DB write window.
    trigger=inngest.TriggerCron(cron="0 3 * * *"),
    retries=1,
    # Single-flight: the RPC is idempotent in the sense that a re-run finds
    # nothing new, but concurrent runs would race on the same rows and waste
    # connection budget.
    concurrency=[inngest.Concurrency(limit=1)],
)
async def conversations_auto_archive(ctx: inngest.Context) -> dict[str, Any]:
    """One SQL call archives every eligible row, returns per-org counts."""
    step = ctx.step
    results = await step.run("rpc-auto-archive", _run_auto_archive_rpc_async)
    total = sum(int(r.get("archived_count") or 0) for r in results)
    log.info(
        "archive.cron.ran",
        kind="auto_archive",
        orgs_touched=len(results),
        archived_total=total,
    )
    return {"orgs": len(results), "archived": total}


# ── Daily delete cron (retention tier) ────────────────────────────────────


@_inngest_client.create_function(
    fn_id="conversations-auto-delete",
    # 03:15 UTC. Shifted off the archive slot so a slow archive run can't push
    # the delete window into business-hour writes.
    trigger=inngest.TriggerCron(cron="15 3 * * *"),
    retries=1,
    concurrency=[inngest.Concurrency(limit=1)],
)
async def conversations_auto_delete(ctx: inngest.Context) -> dict[str, Any]:
    """Hard-delete archived conversations past per-org retention.

    A no-op for orgs that haven't opted in to retention deletion (the
    overwhelming majority — default is NULL/never).
    """
    step = ctx.step
    results = await step.run("rpc-auto-delete", _run_auto_delete_rpc_async)
    total = sum(int(r.get("deleted_count") or 0) for r in results)
    log.info(
        "archive.cron.ran",
        kind="auto_delete",
        orgs_touched=len(results),
        deleted_total=total,
    )
    return {"orgs": len(results), "deleted": total}


# ── RPC wrappers ──────────────────────────────────────────────────────────
# Inngest's ``step.run`` accepts an async callable. We wrap the synchronous
# PostgREST client in ``asyncio.to_thread`` so the event loop stays free
# while the RPC roundtrips.


def _run_auto_archive_rpc_sync() -> list[dict[str, Any]]:
    svc = get_service_client()
    res = svc.rpc("run_conversation_auto_archive", {}).execute()
    return list(res.data or [])


def _run_auto_delete_rpc_sync() -> list[dict[str, Any]]:
    svc = get_service_client()
    res = svc.rpc("run_conversation_auto_delete", {}).execute()
    return list(res.data or [])


async def _run_auto_archive_rpc_async() -> list[dict[str, Any]]:
    return await asyncio.to_thread(_run_auto_archive_rpc_sync)


async def _run_auto_delete_rpc_async() -> list[dict[str, Any]]:
    return await asyncio.to_thread(_run_auto_delete_rpc_sync)


FUNCTIONS = [conversations_auto_archive, conversations_auto_delete]
