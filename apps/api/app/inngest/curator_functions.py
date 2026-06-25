"""Inngest functions for the Knowledge Curator Agent (Agent2 Day 3 #24).

Three functions:

  1. curator-weekly-scan  (cron: Mon 08:00 UTC)
       Enumerates orgs with curator_enabled=true, fans out one
       `knowledge/curator.scan` event per org. Inngest's per-fn
       concurrency cap on the runner keeps fan-out humane.

  2. curator-run-scan     (event: knowledge/curator.scan)
       Single-org scan. Calls knowledge_curator.run_curator_scan which
       writes a knowledge_health_reports row, notifies admins on
       findings, drafts gap stubs into document_drafts.

  3. (No third function — manual "run now" from the admin UI emits the
       same `knowledge/curator.scan` event the cron uses, so the path
       is unified.)

Why a single-step orchestration vs. multi-step Inngest fan-out:
    The scan is bounded — a few RPCs, ≤200 HEAD probes, ≤5 LLM calls.
    Total time is comfortably under one Inngest step ceiling for any
    realistic org. Splitting the scan into per-finding sub-steps would
    mean inventing cross-step state coordination for no resilience win
    (the whole scan is idempotent — retry just produces another
    knowledge_health_reports row, and the dashboard only shows the
    latest).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

import inngest

from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.services.knowledge_curator import run_curator_scan

log = logging.getLogger(__name__)

_inngest_client = get_inngest_client()


# ── 1. Weekly cron + fan-out ─────────────────────────────────────────────


@_inngest_client.create_function(
    fn_id="curator-weekly-scan",
    # Monday 08:00 UTC — early morning for US East, lunch for EU, late
    # afternoon for APAC. Admins find the report waiting when they sit
    # down to plan the week.
    trigger=inngest.TriggerCron(cron="0 8 * * 1"),
    # Single-instance fan-out — we don't want two cron fires producing
    # two reports per org if Inngest retries.
    concurrency=[inngest.Concurrency(limit=1)],
    retries=1,
)
async def curator_weekly_scan(ctx: inngest.Context) -> dict[str, Any]:
    """Find orgs with curator_enabled=true and fan one event each."""

    async def _load_active_orgs() -> list[str]:
        svc = get_service_client()

        def _query() -> list[str]:
            res = (
                svc.table("organizations")
                .select("id")
                .eq("curator_enabled", True)
                .execute()
            )
            return [r["id"] for r in (res.data or [])]

        return await asyncio.to_thread(_query)

    org_ids = await ctx.step.run("load-active-orgs", _load_active_orgs)
    if not org_ids:
        return {"fanned": 0}

    client = get_inngest_client()
    for oid in org_ids:
        await ctx.step.run(
            f"enqueue-{oid}",
            lambda oid=oid: client.send(
                inngest.Event(
                    name="knowledge/curator.scan",
                    data={"org_id": oid, "trigger": "weekly_cron"},
                )
            ),
        )

    return {"fanned": len(org_ids)}


# ── 2. Per-org scan worker ───────────────────────────────────────────────


@_inngest_client.create_function(
    fn_id="curator-run-scan",
    trigger=inngest.TriggerEvent(event="knowledge/curator.scan"),
    # Per-org concurrency cap — a manual "Run Now" while the weekly scan
    # is mid-flight should queue, not fan out two concurrent reports.
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.org_id", scope="fn"),
    ],
    retries=2,
    # Manual triggers and the cron can both fire — debounce by org to
    # absorb double-clicks on the dashboard's "Run Now" button without
    # producing two reports in a row.
    debounce=inngest.Debounce(
        period=timedelta(minutes=5),
        key="event.data.org_id",
    ),
)
async def curator_run_scan(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    org_id: str = data["org_id"]
    triggered_by: str | None = data.get("triggered_by")

    result = await ctx.step.run(
        f"scan-{org_id}",
        lambda: run_curator_scan(org_id=org_id, triggered_by=triggered_by),
    )
    return {"org_id": org_id, **(result or {})}


FUNCTIONS = [curator_weekly_scan, curator_run_scan]
