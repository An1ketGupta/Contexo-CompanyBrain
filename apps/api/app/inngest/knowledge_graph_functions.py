"""Inngest functions for Feature 2.1 — Knowledge Graph.

Two functions:
  1. knowledge-graph-nightly  (cron: 02:30 UTC daily)
       Fan one event per org with at least N ready documents.
  2. knowledge-graph-refresh  (event: knowledge-graph/refresh)
       Compute one snapshot for one org. Triggered by both the nightly fan
       AND the admin "Refresh" button.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

import inngest

from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.services.knowledge_graph import refresh_org_snapshot

log = logging.getLogger(__name__)

_inngest_client = get_inngest_client()

# Threshold for nightly fan — below this an org's snapshot is essentially
# the trivial "All docs" cluster anyway, no need to spend compute nightly.
_MIN_DOCS_FOR_NIGHTLY = 12


@_inngest_client.create_function(
    fn_id="knowledge-graph-nightly",
    # 02:30 UTC daily — late enough to come after embedding backfills and
    # health scoring, early enough that admins logging in for the day see
    # a fresh map.
    trigger=inngest.TriggerCron(cron="30 2 * * *"),
    concurrency=[inngest.Concurrency(limit=1)],
    retries=1,
)
async def knowledge_graph_nightly(ctx: inngest.Context) -> dict[str, Any]:
    """Fan one `knowledge-graph/refresh` event per org with enough docs."""

    async def _load_orgs() -> list[str]:
        svc = get_service_client()

        def _query() -> list[str]:
            # Count docs per org via a server-side aggregation would be ideal
            # but supabase-py doesn't expose GROUP BY ergonomically. Two-step
            # plan is fine at any plausible scale (≤ N orgs).
            orgs_res = svc.table("organizations").select("id").execute()
            org_ids = [r["id"] for r in (orgs_res.data or [])]
            keep: list[str] = []
            for oid in org_ids:
                cnt = (
                    svc.table("documents")
                    .select("id", count="exact", head=True)
                    .eq("org_id", oid)
                    .eq("status", "ready")
                    .not_.is_("summary_embedding", "null")
                    .execute()
                )
                if (cnt.count or 0) >= _MIN_DOCS_FOR_NIGHTLY:
                    keep.append(oid)
            return keep

        return await asyncio.to_thread(_query)

    org_ids = await ctx.step.run("load-orgs", _load_orgs)
    if not org_ids:
        return {"fanned": 0}

    client = get_inngest_client()
    for oid in org_ids:
        await ctx.step.run(
            f"enqueue-{oid}",
            lambda oid=oid: client.send(
                inngest.Event(
                    name="knowledge-graph/refresh",
                    data={"org_id": oid, "trigger": "nightly_cron"},
                )
            ),
        )
    return {"fanned": len(org_ids)}


@_inngest_client.create_function(
    fn_id="knowledge-graph-refresh",
    trigger=inngest.TriggerEvent(event="knowledge-graph/refresh"),
    # Per-org cap — a manual "Refresh" while the nightly run is mid-flight
    # should queue, not double-write.
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.org_id", scope="fn"),
    ],
    retries=2,
    debounce=inngest.Debounce(
        period=timedelta(minutes=2),
        key="event.data.org_id",
    ),
)
async def knowledge_graph_refresh(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    org_id: str = data["org_id"]
    result = await ctx.step.run(
        f"build-{org_id}",
        lambda: refresh_org_snapshot(org_id=org_id),
    )
    return {"org_id": org_id, "doc_count": (result or {}).get("doc_count", 0)}


FUNCTIONS = [knowledge_graph_nightly, knowledge_graph_refresh]
