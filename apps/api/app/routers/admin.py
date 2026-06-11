"""Admin-only dashboards (V4 Day 1+2+4):

  GET /admin/analytics?period=7d|30d|90d   — full usage analytics dashboard
  GET /admin/knowledge-health              — KB health overview
  GET /admin/moderation?result=blocked|flagged|all  — moderation logs
  GET /admin/coverage-score?refresh=true   — KB coverage vs canonical categories

Admin gating uses the same pattern as `/usage/knowledge-intelligence`: hit
the `users` table with the user-scoped client and check `role = 'admin'`.
RLS on `users` already restricts the row to the caller, so the read is safe.

Aggregations use the service-role client because we want to read across the
whole org without RLS getting in the way on tables like `messages` (which
filters by conversation membership) and `chunk_citations` (read-only for
members but cleaner with one privileged path).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

# Period token → days. Anything else is rejected at the boundary.
_PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90}


async def _require_admin(current_user: dict) -> str:
    """Returns the caller's org_id; raises 403 if not admin, 404 if no org."""
    org_id = current_user.get("org_id")
    if not org_id:
        raise NoOrganization("No organization found. Please sign out and sign back in.")

    user_client = get_user_client(current_user["token"])
    me = await asyncio.to_thread(
        lambda: user_client.table("users")
        .select("role")
        .eq("id", current_user["user_id"])
        .maybe_single()
        .execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin only.",
        )
    return org_id


# ── Analytics ────────────────────────────────────────────────────────────────

@router.get("/analytics")
async def get_admin_analytics(
    period: str = Query(default="30d", pattern="^(7d|30d|90d)$"),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id = await _require_admin(current_user)
    days = _PERIOD_DAYS[period]
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    week_cutoff = now - timedelta(days=7)
    svc = get_service_client()

    # ── Stat cards ────────────────────────────────────────────────────────
    # Total queries = count of user-role messages in the period. We use
    # messages instead of `analytics_events.chat_sent` because chat history
    # is the durable source of truth; analytics_events can race.
    total_queries_res = await asyncio.to_thread(
        lambda: svc.table("messages")
        .select("id", count="exact")
        .eq("org_id", org_id)
        .eq("role", "user")
        .gte("created_at", cutoff.isoformat())
        .limit(1)
        .execute()
    )
    total_queries = total_queries_res.count or 0

    # Active users (last 7 days). We pull conversation user_ids in the window
    # — Supabase doesn't expose count(distinct), so dedupe client-side.
    active_convs_res = await asyncio.to_thread(
        lambda: svc.table("conversations")
        .select("user_id, updated_at")
        .eq("org_id", org_id)
        .gte("updated_at", week_cutoff.isoformat())
        .limit(10000)
        .execute()
    )
    active_users = len({row["user_id"] for row in (active_convs_res.data or []) if row.get("user_id")})

    total_users_res = await asyncio.to_thread(
        lambda: svc.table("users")
        .select("id", count="exact")
        .eq("org_id", org_id)
        .limit(1)
        .execute()
    )
    total_users = total_users_res.count or 0

    # Feedback ratio over the period — pulled from analytics_events for
    # granularity (the messages.feedback column is current-state-only, no
    # timestamp on the flip).
    feedback_res = await asyncio.to_thread(
        lambda: svc.table("analytics_events")
        .select("metadata")
        .eq("org_id", org_id)
        .eq("event_type", "feedback_given")
        .gte("created_at", cutoff.isoformat())
        .limit(10000)
        .execute()
    )
    positive = sum(
        1 for row in (feedback_res.data or [])
        if (row.get("metadata") or {}).get("feedback") == "positive"
    )
    negative = sum(
        1 for row in (feedback_res.data or [])
        if (row.get("metadata") or {}).get("feedback") == "negative"
    )
    total_feedback = positive + negative
    feedback_score = (
        round(positive / total_feedback * 100) if total_feedback else None
    )

    # Documents: ready count + how many got cited in window.
    ready_docs_res = await asyncio.to_thread(
        lambda: svc.table("documents")
        .select("id", count="exact")
        .eq("org_id", org_id)
        .eq("status", "ready")
        .limit(1)
        .execute()
    )
    total_docs = ready_docs_res.count or 0

    cited_docs_res = await asyncio.to_thread(
        lambda: svc.table("chunk_citations")
        .select("document_id")
        .eq("org_id", org_id)
        .gte("cited_at", cutoff.isoformat())
        .limit(10000)
        .execute()
    )
    docs_accessed = len({row["document_id"] for row in (cited_docs_res.data or []) if row.get("document_id")})

    # ── Time series: queries per day ──────────────────────────────────────
    # Bucket messages.created_at into days client-side. SQL date_trunc would
    # be cleaner but we'd need a custom RPC; for ≤90 days the in-memory
    # bucket is fast enough.
    daily_msgs_res = await asyncio.to_thread(
        lambda: svc.table("messages")
        .select("created_at")
        .eq("org_id", org_id)
        .eq("role", "user")
        .gte("created_at", cutoff.isoformat())
        .order("created_at")
        .limit(50000)
        .execute()
    )
    bucket: dict[str, int] = {}
    for row in (daily_msgs_res.data or []):
        day = (row.get("created_at") or "")[:10]
        if day:
            bucket[day] = bucket.get(day, 0) + 1

    # Fill in zero-count days so the area chart has no gaps.
    daily_queries: list[dict[str, Any]] = []
    for i in range(days):
        d = (cutoff + timedelta(days=i)).date().isoformat()
        daily_queries.append({"day": d, "count": bucket.get(d, 0)})

    # ── Per-user breakdown ────────────────────────────────────────────────
    # Pull user-role messages in window with conversation→user join, bucket
    # client-side. Cap at most-active 50 users; an org with more is rare on
    # any plan we ship.
    user_msgs_res = await asyncio.to_thread(
        lambda: svc.table("messages")
        .select("created_at, conversation_id")
        .eq("org_id", org_id)
        .eq("role", "user")
        .gte("created_at", cutoff.isoformat())
        .limit(50000)
        .execute()
    )
    conv_ids = list({row["conversation_id"] for row in (user_msgs_res.data or []) if row.get("conversation_id")})
    conv_to_user: dict[str, str] = {}
    if conv_ids:
        # Supabase has a 1000-id `in_` cap; chunk just in case.
        for start in range(0, len(conv_ids), 800):
            chunk_ids = conv_ids[start:start + 800]
            conv_res = await asyncio.to_thread(
                lambda c=chunk_ids: svc.table("conversations")
                .select("id, user_id")
                .in_("id", c)
                .execute()
            )
            for row in (conv_res.data or []):
                if row.get("user_id"):
                    conv_to_user[row["id"]] = row["user_id"]

    per_user: dict[str, dict[str, Any]] = {}
    for row in (user_msgs_res.data or []):
        uid = conv_to_user.get(row.get("conversation_id"))
        if not uid:
            continue
        u = per_user.setdefault(uid, {"queries": 0, "last_active": row["created_at"]})
        u["queries"] += 1
        if row["created_at"] > u["last_active"]:
            u["last_active"] = row["created_at"]

    user_breakdown: list[dict[str, Any]] = []
    if per_user:
        # Trim to the top 50 by query volume before doing the per-user auth
        # lookups — emails come from auth.users (one call per id) so we don't
        # want to pay that cost for users we're about to drop anyway.
        ranked = sorted(per_user.items(), key=lambda kv: kv[1]["queries"], reverse=True)[:50]
        top_ids = [uid for uid, _ in ranked]

        user_rows = await asyncio.to_thread(
            lambda: svc.table("users")
            .select("id, display_name")
            .in_("id", top_ids)
            .execute()
        )
        users_by_id = {row["id"]: row for row in (user_rows.data or [])}

        async def _email_for(uid: str) -> str | None:
            try:
                au = await asyncio.to_thread(lambda u=uid: svc.auth.admin.get_user_by_id(u))
                return getattr(getattr(au, "user", None), "email", None)
            except Exception as exc:
                log.warning("admin_analytics_email_lookup_failed", extra={"user_id": uid, "error": str(exc)})
                return None

        emails = await asyncio.gather(*(_email_for(uid) for uid in top_ids))
        emails_by_id = dict(zip(top_ids, emails))

        for uid, agg in ranked:
            u = users_by_id.get(uid, {})
            email = emails_by_id.get(uid)
            user_breakdown.append({
                "user_id": uid,
                "name": u.get("display_name") or (email or "Unknown").split("@")[0],
                "email": email,
                "queries": agg["queries"],
                "last_active": agg["last_active"],
            })

    # ── Most-cited documents ──────────────────────────────────────────────
    citations_res = await asyncio.to_thread(
        lambda: svc.table("chunk_citations")
        .select("document_id")
        .eq("org_id", org_id)
        .gte("cited_at", cutoff.isoformat())
        .limit(20000)
        .execute()
    )
    doc_cites: dict[str, int] = {}
    for row in (citations_res.data or []):
        d = row.get("document_id")
        if d:
            doc_cites[d] = doc_cites.get(d, 0) + 1
    top_doc_ids = sorted(doc_cites.items(), key=lambda kv: kv[1], reverse=True)[:5]
    top_documents: list[dict[str, Any]] = []
    if top_doc_ids:
        doc_rows = await asyncio.to_thread(
            lambda: svc.table("documents")
            .select("id, name")
            .in_("id", [d for d, _ in top_doc_ids])
            .execute()
        )
        doc_names = {row["id"]: row["name"] for row in (doc_rows.data or [])}
        for did, cites in top_doc_ids:
            if did in doc_names:
                top_documents.append({"name": doc_names[did], "citations": cites})

    # ── Intent breakdown — read from messages.metadata->'intent' ──────────
    intent_res = await asyncio.to_thread(
        lambda: svc.table("messages")
        .select("metadata")
        .eq("org_id", org_id)
        .eq("role", "assistant")
        .gte("created_at", cutoff.isoformat())
        .limit(50000)
        .execute()
    )
    intent_counts: dict[str, int] = {}
    for row in (intent_res.data or []):
        intent = (row.get("metadata") or {}).get("intent")
        if intent:
            intent_counts[intent] = intent_counts.get(intent, 0) + 1
    intent_breakdown = [
        {"intent": k, "count": v}
        for k, v in sorted(intent_counts.items(), key=lambda kv: kv[1], reverse=True)
    ]

    return {
        "period": period,
        "stats": {
            "total_queries": total_queries,
            "active_users": active_users,
            "total_users": total_users,
            "feedback_score": feedback_score,
            "total_docs": total_docs,
            "docs_accessed": docs_accessed,
        },
        "daily_queries": daily_queries,
        "user_breakdown": user_breakdown,
        "top_documents": top_documents,
        "intent_breakdown": intent_breakdown,
    }


# ── Knowledge Base Health ────────────────────────────────────────────────────

@router.get("/knowledge-health")
async def get_knowledge_health(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id = await _require_admin(current_user)
    svc = get_service_client()

    docs_res = await asyncio.to_thread(
        lambda: svc.table("documents")
        .select("id, name, file_type, health_score, health_label, last_accessed_at, created_at, citation_count, gap_flag_count, health_computed_at")
        .eq("org_id", org_id)
        .eq("status", "ready")
        .limit(5000)
        .execute()
    )
    docs = docs_res.data or []

    counts = {"healthy": 0, "stale": 0, "at_risk": 0, "unused": 0, "unscored": 0}
    at_risk: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    for d in docs:
        label = d.get("health_label") or "unscored"
        if label in counts:
            counts[label] += 1
        else:
            counts["unscored"] += 1

        if label in ("at_risk", "unused"):
            created = d.get("created_at")
            try:
                created_dt = datetime.fromisoformat((created or "").replace("Z", "+00:00"))
                age_days = max(0, (now - created_dt).days)
            except (ValueError, AttributeError):
                age_days = 0
            at_risk.append({
                "id": d["id"],
                "name": d["name"],
                "file_type": d.get("file_type"),
                "health_score": d.get("health_score") or 0,
                "health_label": label,
                "last_accessed_at": d.get("last_accessed_at"),
                "age_days": age_days,
                "citation_count": d.get("citation_count") or 0,
                "gap_flag_count": d.get("gap_flag_count") or 0,
            })

    at_risk.sort(key=lambda r: r["health_score"])

    return {
        "counts": counts,
        "total": len(docs),
        "at_risk_docs": at_risk[:20],
    }


# ── Moderation logs (Day 2) ──────────────────────────────────────────────────

@router.get("/moderation")
async def get_moderation_logs(
    result: str = Query(default="all", pattern="^(all|blocked|flagged)$"),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: dict = Depends(verify_jwt),
) -> list[dict[str, Any]]:
    org_id = await _require_admin(current_user)
    svc = get_service_client()

    q = (
        svc.table("moderation_logs")
        .select("id, query_text, result, reason, action_taken, created_at, user_id")
        .eq("org_id", org_id)
        .order("created_at", desc=True)
        .limit(limit)
    )
    if result != "all":
        q = q.eq("result", result)
    res = await asyncio.to_thread(lambda: q.execute())
    return res.data or []


# ── Coverage Score (Day 4) ───────────────────────────────────────────────────

@router.get("/coverage-score")
async def get_coverage_score(
    refresh: bool = Query(default=False),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Return the KB coverage payload, recomputing if stale or `refresh=true`.

    Compute is bounded (≤8 embedding calls + 8 RPCs) but not free; the
    service caches on a 1h TTL with eager invalidation on doc-ready.
    """
    from app.services.coverage import get_or_compute_coverage

    org_id = await _require_admin(current_user)
    return await get_or_compute_coverage(org_id, force_refresh=refresh)
