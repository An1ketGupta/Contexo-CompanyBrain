"""Founder-only internal dashboards.

Routes under /internal/* are gated by FOUNDER_USER_IDS (a comma-separated list
of Supabase user UUIDs in .env). The check happens AFTER verify_jwt — so we
have a guaranteed-real authenticated user before we permit-or-deny.

The endpoints here read across ALL orgs via service-role client. Don't expose
any of this to org admins, even with their own data filtered out — the table
shapes leak which other customers exist + how much they're costing us.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import verify_jwt
from app.config import get_settings
from app.database import get_service_client
from app.observability import get_logger
from app.services.llm_cost import micros_to_usd

log = get_logger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])

_PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90}


async def require_founder(current_user: dict = Depends(verify_jwt)) -> dict:
    """Allow only Supabase auth users whose id is in FOUNDER_USER_IDS.

    Returns the same dict shape as verify_jwt so route handlers can keep using
    `current_user["user_id"]` / `current_user["org_id"]` without branching.
    """
    settings = get_settings()
    allowed = settings.founder_user_id_set
    user_id = current_user.get("user_id") or ""
    if not allowed:
        # Misconfigured deploy: refuse to even hint that the endpoint exists.
        log.warning("internal_route_refused_no_founders_configured", user_id=user_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if user_id not in allowed:
        # 404 (not 403) so the route is indistinguishable from "doesn't exist"
        # to non-founders. Defense in depth: we don't want to leak the list of
        # internal endpoints via brute-forced URL discovery.
        log.info("internal_route_denied", user_id=user_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return current_user


# ── LLM Cost Dashboard (#75) ────────────────────────────────────────────────


@router.get("/llm-costs")
async def get_llm_costs(
    period: str = Query(default="30d", pattern="^(7d|30d|90d)$"),
    _: dict = Depends(require_founder),
) -> dict[str, Any]:
    """Cost rollup across every org. Powers the founder-only cost dashboard.

    Aggregations are computed in Python (single pass over the raw rows) rather
    than via SQL GROUP BY because Supabase's PostgREST doesn't expose
    SUM/GROUP BY conveniently and an RPC would be overkill for the data volume
    we expect at pre-seed scale (~10k rows/month). When this gets slow we
    promote it to a `llm_cost_rollups` materialized view, not before.
    """
    days = _PERIOD_DAYS[period]
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    prev_cutoff = cutoff - timedelta(days=days)
    svc = get_service_client()

    def _load_rows() -> list[dict[str, Any]]:
        # `range(0, 49999)` because Supabase enforces a default 1000-row cap;
        # the `.limit(...)` here just bumps that. At pre-seed traffic this is
        # well under one batch. Promote to pagination if we ever cross 50k.
        return (
            svc.table("query_logs")
            .select(
                "org_id, model_used, input_tokens, output_tokens, cost_micros, created_at"
            )
            .gte("created_at", prev_cutoff.isoformat())
            .lt("created_at", now.isoformat())
            .limit(50_000)
            .execute()
            .data
            or []
        )

    rows = await asyncio.to_thread(_load_rows)

    # Partition by current vs. previous period for the delta card.
    cutoff_iso = cutoff.isoformat()
    current_rows = [r for r in rows if (r.get("created_at") or "") >= cutoff_iso]
    previous_rows = [r for r in rows if (r.get("created_at") or "") < cutoff_iso]

    total_micros = sum(int(r.get("cost_micros") or 0) for r in current_rows)
    prev_micros = sum(int(r.get("cost_micros") or 0) for r in previous_rows)
    total_tokens = sum(
        int(r.get("input_tokens") or 0) + int(r.get("output_tokens") or 0)
        for r in current_rows
    )
    total_queries = len(current_rows)

    # Per-org rollup — needs org names. One query for the orgs in the result
    # set; we tolerate orgs that have been deleted (their rows stay in
    # query_logs's FK NOT NULL via the org's pre-delete, but we defend anyway).
    org_ids = {r["org_id"] for r in current_rows if r.get("org_id")}
    per_org_meta: dict[str, dict[str, Any]] = {}
    if org_ids:
        def _load_orgs() -> list[dict[str, Any]]:
            return (
                svc.table("organizations")
                .select("id, name, plan")
                .in_("id", list(org_ids))
                .execute()
                .data
                or []
            )

        for o in await asyncio.to_thread(_load_orgs):
            per_org_meta[o["id"]] = o

    per_org: dict[str, dict[str, Any]] = {}
    for r in current_rows:
        oid = r.get("org_id")
        if not oid:
            continue
        bucket = per_org.setdefault(
            oid,
            {
                "org_id": oid,
                "name": (per_org_meta.get(oid) or {}).get("name", "(deleted)"),
                "plan": (per_org_meta.get(oid) or {}).get("plan", "unknown"),
                "cost_micros": 0,
                "tokens": 0,
                "queries": 0,
            },
        )
        bucket["cost_micros"] += int(r.get("cost_micros") or 0)
        bucket["tokens"] += int(r.get("input_tokens") or 0) + int(
            r.get("output_tokens") or 0
        )
        bucket["queries"] += 1

    per_org_list = sorted(per_org.values(), key=lambda x: x["cost_micros"], reverse=True)
    # Flag orgs costing > $X / period for the red banner row.
    UNPROFITABLE_CENTS_PER_PERIOD = 500  # $5 in a 30d window → flag in dashboard
    for row in per_org_list:
        row["cost_usd"] = round(micros_to_usd(row["cost_micros"]), 4)
        row["cost_per_query_usd"] = (
            round(row["cost_usd"] / row["queries"], 6) if row["queries"] else 0
        )
        row["over_threshold"] = (
            row["cost_micros"] >= UNPROFITABLE_CENTS_PER_PERIOD * 10_000
        )

    # Daily trend — bucket by date string (UTC). Skip the previous-period rows.
    daily: dict[str, int] = {}
    for r in current_rows:
        ts = r.get("created_at") or ""
        if len(ts) >= 10:
            daily[ts[:10]] = daily.get(ts[:10], 0) + int(r.get("cost_micros") or 0)
    daily_series = [
        {"date": d, "cost_usd": round(micros_to_usd(m), 4)}
        for d, m in sorted(daily.items())
    ]

    # By-model rollup
    by_model: dict[str, dict[str, Any]] = {}
    for r in current_rows:
        model = r.get("model_used") or "unknown"
        bucket = by_model.setdefault(
            model,
            {"model": model, "cost_micros": 0, "queries": 0, "tokens": 0},
        )
        bucket["cost_micros"] += int(r.get("cost_micros") or 0)
        bucket["queries"] += 1
        bucket["tokens"] += int(r.get("input_tokens") or 0) + int(
            r.get("output_tokens") or 0
        )
    by_model_list = sorted(
        by_model.values(), key=lambda x: x["cost_micros"], reverse=True
    )
    for row in by_model_list:
        row["cost_usd"] = round(micros_to_usd(row["cost_micros"]), 4)

    # Period-over-period delta (None when prev_micros == 0 to avoid /0 noise).
    delta_pct: float | None = None
    if prev_micros > 0:
        delta_pct = round((total_micros - prev_micros) / prev_micros * 100, 1)

    return {
        "period": period,
        "total_cost_usd": round(micros_to_usd(total_micros), 4),
        "previous_period_cost_usd": round(micros_to_usd(prev_micros), 4),
        "delta_pct": delta_pct,
        "total_tokens": total_tokens,
        "total_queries": total_queries,
        "avg_cost_per_query_usd": (
            round(micros_to_usd(total_micros) / total_queries, 6)
            if total_queries
            else 0
        ),
        "per_org": per_org_list,
        "daily_cost": daily_series,
        "by_model": by_model_list,
    }
