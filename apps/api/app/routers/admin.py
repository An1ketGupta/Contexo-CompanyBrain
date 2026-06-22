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
from pydantic import BaseModel, Field, field_validator

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
    # We also fold in copy_count and time_saved_minutes here so the dashboard
    # gets V5 quality + ROI signals in one round-trip per period.
    intent_res = await asyncio.to_thread(
        lambda: svc.table("messages")
        .select("id, metadata, copy_count, time_saved_minutes, content, conversation_id")
        .eq("org_id", org_id)
        .eq("role", "assistant")
        .gte("created_at", cutoff.isoformat())
        .limit(50000)
        .execute()
    )
    assistant_rows = intent_res.data or []
    intent_counts: dict[str, int] = {}
    intent_copied: dict[str, int] = {}
    intent_total: dict[str, int] = {}
    total_assistant = 0
    total_copied = 0
    total_minutes_saved = 0
    for row in assistant_rows:
        meta = row.get("metadata") or {}
        intent = meta.get("intent")
        copied = (row.get("copy_count") or 0) > 0
        total_assistant += 1
        if copied:
            total_copied += 1
        total_minutes_saved += int(row.get("time_saved_minutes") or 0)
        if intent:
            intent_counts[intent] = intent_counts.get(intent, 0) + 1
            intent_total[intent] = intent_total.get(intent, 0) + 1
            if copied:
                intent_copied[intent] = intent_copied.get(intent, 0) + 1
    intent_breakdown = [
        {
            "intent": k,
            "count": v,
            "copy_rate": (
                round(intent_copied.get(k, 0) / intent_total[k] * 100, 1)
                if intent_total.get(k) else None
            ),
        }
        for k, v in sorted(intent_counts.items(), key=lambda kv: kv[1], reverse=True)
    ]

    # ── V5 #59 — Top copied messages ──────────────────────────────────────
    top_copied_rows = sorted(
        (r for r in assistant_rows if (r.get("copy_count") or 0) > 0),
        key=lambda r: r.get("copy_count") or 0,
        reverse=True,
    )[:5]
    top_copied: list[dict[str, Any]] = []
    if top_copied_rows:
        conv_ids_for_titles = list({r["conversation_id"] for r in top_copied_rows if r.get("conversation_id")})
        title_lookup: dict[str, str] = {}
        if conv_ids_for_titles:
            tres = await asyncio.to_thread(
                lambda: svc.table("conversations")
                .select("id, title")
                .in_("id", conv_ids_for_titles)
                .execute()
            )
            for trow in (tres.data or []):
                title_lookup[trow["id"]] = trow.get("title") or "Untitled"
        for r in top_copied_rows:
            preview = (r.get("content") or "").strip().replace("\n", " ")
            if len(preview) > 120:
                preview = preview[:120].rstrip() + "…"
            top_copied.append({
                "message_id": r["id"],
                "conversation_id": r.get("conversation_id"),
                "conversation_title": title_lookup.get(r.get("conversation_id") or "", "Untitled"),
                "copy_count": int(r.get("copy_count") or 0),
                "preview": preview,
                "intent": (r.get("metadata") or {}).get("intent"),
            })

    copy_rate = round(total_copied / total_assistant * 100, 1) if total_assistant else None

    return {
        "period": period,
        "stats": {
            "total_queries": total_queries,
            "active_users": active_users,
            "total_users": total_users,
            "feedback_score": feedback_score,
            "total_docs": total_docs,
            "docs_accessed": docs_accessed,
            # V5 additions — small enough that adding them here saves a second
            # network round-trip from the dashboard.
            "copy_rate": copy_rate,
            "total_minutes_saved": total_minutes_saved,
            "total_hours_saved": round(total_minutes_saved / 60, 1),
        },
        "daily_queries": daily_queries,
        "user_breakdown": user_breakdown,
        "top_documents": top_documents,
        "top_copied": top_copied,
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


# ── Confidence thresholds (Agent Day 3) ──────────────────────────────────────
#
# The chat confidence badge is computed from raw vector cosine on the cited
# chunks. The default 0.75/0.45 cutoffs work for most orgs, but two patterns
# motivate per-org tuning:
#   1) Heterogeneous corpora (PDFs + meeting notes + scraped wiki) produce
#      lower average cosines than uniform corpora. The default may show too
#      many "low" badges; admin lowers `medium` to widen the green zone.
#   2) Tight, deeply-redundant corpora (legal templates, SOPs) produce
#      uniformly high cosines. The default may render too many "high"; admin
#      raises `high` so the badge stays meaningful.


@router.get("/config/confidence-thresholds")
async def get_confidence_thresholds_endpoint(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id = await _require_admin(current_user)
    from app.services.org_config import (
        DEFAULT_CONFIDENCE_BLOCK,
        DEFAULT_CONFIDENCE_HIGH,
        DEFAULT_CONFIDENCE_MEDIUM,
        get_confidence_thresholds,
    )

    thresholds = await get_confidence_thresholds(org_id)
    return {
        "high": thresholds.high,
        "medium": thresholds.medium,
        "block": thresholds.block,
        "defaults": {
            "high": DEFAULT_CONFIDENCE_HIGH,
            "medium": DEFAULT_CONFIDENCE_MEDIUM,
            "block": DEFAULT_CONFIDENCE_BLOCK,
        },
    }


class ConfidenceThresholdsBody(BaseModel):
    # Cosine cutoffs in [0, 1]. The UI shows them as 0–10 sliders (cosine × 10)
    # and converts back, but on the wire we keep the raw cosine so the unit
    # is unambiguous.
    high: float = Field(..., ge=0.0, le=1.0)
    medium: float = Field(..., ge=0.0, le=1.0)
    # `block` is optional for backward compatibility — clients that only know
    # about the badge bands can still PUT without clobbering the gate config.
    # When present, must satisfy 0 <= block <= medium.
    block: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("high")
    @classmethod
    def _high_ge_medium(cls, v: float, info: Any) -> float:
        # Pydantic v2 — `info.data` carries already-validated fields. `medium`
        # is declared after `high`, so we can't validate ordering on `high`
        # directly. We attach the constraint to `medium` instead (below).
        return v

    @field_validator("medium")
    @classmethod
    def _medium_le_high(cls, v: float, info: Any) -> float:
        high = info.data.get("high")
        if high is not None and v > high:
            raise ValueError("medium threshold must be <= high threshold")
        return v

    @field_validator("block")
    @classmethod
    def _block_le_medium(cls, v: float | None, info: Any) -> float | None:
        if v is None:
            return v
        medium = info.data.get("medium")
        if medium is not None and v > medium:
            raise ValueError("block threshold must be <= medium threshold")
        return v


@router.put("/config/confidence-thresholds")
async def update_confidence_thresholds_endpoint(
    body: ConfidenceThresholdsBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id = await _require_admin(current_user)
    from app.services.org_config import update_confidence_thresholds

    saved = await update_confidence_thresholds(
        org_id=org_id, high=body.high, medium=body.medium, block=body.block
    )
    return {
        "high": saved.high,
        "medium": saved.medium,
        "block": saved.block,
        "updated": True,
    }


# ── Knowledge gaps (Agent Day 5) ─────────────────────────────────────────────


@router.get("/knowledge-gaps")
async def list_knowledge_gaps(
    days: int = Query(default=30, ge=1, le=180),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Aggregate gap rows by topic for the admin panel.

    Returns one row per distinct topic, with occurrence count + last-asked
    timestamp + whether an AI draft is available. We aggregate server-side
    (instead of the UI doing it) so the response stays small even on orgs
    with thousands of zero-result queries.
    """
    org_id = await _require_admin(current_user)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    svc = get_service_client()

    gaps_res = await asyncio.to_thread(
        lambda: svc.table("knowledge_gaps")
        .select("topic, query, created_at")
        .eq("org_id", org_id)
        .gte("created_at", cutoff)
        .order("created_at", desc=True)
        .limit(5000)
        .execute()
    )
    rows = gaps_res.data or []

    # Bucket by topic — simple, fast, and predictable. If/when a single org
    # blows past 5k rows in a window, we'll push this to a SQL view.
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        topic = (row.get("topic") or "").strip()
        if not topic:
            continue
        bucket = buckets.setdefault(
            topic,
            {"topic": topic, "count": 0, "last_asked": row["created_at"], "sample_queries": []},
        )
        bucket["count"] += 1
        if row["created_at"] > bucket["last_asked"]:
            bucket["last_asked"] = row["created_at"]
        if len(bucket["sample_queries"]) < 3 and row.get("query"):
            bucket["sample_queries"].append(row["query"])

    # Pull draft availability in one shot rather than N queries.
    draft_topics = await asyncio.to_thread(
        lambda: svc.table("document_drafts")
        .select("gap_topic, id, status")
        .eq("org_id", org_id)
        .eq("source", "knowledge_gap_autoflow")
        .execute()
    )
    drafts_by_topic: dict[str, dict[str, Any]] = {}
    for d in (draft_topics.data or []):
        t = d.get("gap_topic")
        if t and t not in drafts_by_topic:
            drafts_by_topic[t] = {"draft_id": d["id"], "draft_status": d.get("status")}

    items = []
    for bucket in buckets.values():
        draft = drafts_by_topic.get(bucket["topic"])
        items.append({**bucket, "draft": draft})

    items.sort(key=lambda i: (-i["count"], i["topic"]))
    return {"days": days, "items": items, "total_topics": len(items)}


@router.get("/document-drafts/{draft_id}")
async def get_document_draft(
    draft_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id = await _require_admin(current_user)
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("document_drafts")
        .select("*")
        .eq("id", draft_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        raise HTTPException(status_code=404, detail="draft_not_found")
    return row.data


class DraftDecisionBody(BaseModel):
    # Optional edits — if present, replaces the stub before ingest.
    title: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None, max_length=200_000)


@router.post("/document-drafts/{draft_id}/approve")
async def approve_document_draft(
    draft_id: str,
    body: DraftDecisionBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Approve a knowledge-gap stub: ingest it as a real document.

    The draft is persisted (status='approved' + reviewer) and a regular
    `doc/process-text` event is fired against the same pipeline that
    handles Notion/Drive ingest. Once it lands in `documents`, the next
    chat that asks about the topic will find it via hybrid_search.
    """
    org_id = await _require_admin(current_user)
    user_id = current_user["user_id"]
    svc = get_service_client()

    row = await asyncio.to_thread(
        lambda: svc.table("document_drafts")
        .select("id, title, content, status, gap_topic")
        .eq("id", draft_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        raise HTTPException(status_code=404, detail="draft_not_found")
    if row.data.get("status") not in (None, "pending_review"):
        raise HTTPException(status_code=409, detail="draft_already_resolved")

    title = (body.title or row.data["title"]).strip() or "Untitled knowledge stub"
    content = (body.content or row.data["content"]).strip()
    if not content:
        raise HTTPException(status_code=400, detail="empty_content")

    # Insert documents row in 'processing' state — same shape the rest of the
    # ingest pipeline expects.
    doc_row = await asyncio.to_thread(
        lambda: svc.table("documents")
        .insert({
            "org_id": org_id,
            "name": title,
            "file_type": "md",
            "status": "processing",
            "uploaded_by": user_id,
            "source": "knowledge_gap_stub",
        })
        .execute()
    )
    if not doc_row.data:
        raise HTTPException(status_code=500, detail="document_insert_failed")
    new_doc_id = doc_row.data[0]["id"]

    # Mark draft approved + link to the created document.
    await asyncio.to_thread(
        lambda: svc.table("document_drafts")
        .update({
            "status": "approved",
            "reviewed_by": user_id,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "ingested_document_id": new_doc_id,
            "title": title,
            "content": content,
        })
        .eq("id", draft_id)
        .execute()
    )

    # Queue the standard process-text pipeline (chunk + embed + ready flip).
    import inngest as _inngest_pkg

    from app.inngest.client import get_inngest_client

    client = get_inngest_client()
    await client.send(
        _inngest_pkg.Event(
            name="doc/process-text",
            data={"doc_id": new_doc_id, "org_id": org_id, "text": content},
            id=f"draft-{draft_id}-{new_doc_id}",
        )
    )

    return {"approved": True, "document_id": new_doc_id}


@router.post("/document-drafts/{draft_id}/reject")
async def reject_document_draft(
    draft_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id = await _require_admin(current_user)
    user_id = current_user["user_id"]
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("document_drafts")
        .update({
            "status": "rejected",
            "reviewed_by": user_id,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        })
        .eq("id", draft_id)
        .eq("org_id", org_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="draft_not_found")
    return {"rejected": True}


# ── Agent Roadmap Day 7: agent runs audit trail ─────────────────────────────


@router.get("/agent-runs")
async def list_agent_runs(
    agent_type: str | None = Query(default=None, max_length=64),
    status_filter: str | None = Query(default=None, alias="status", max_length=32),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=10_000),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """List agent runs for the admin audit trail.

    Returns a slimmed-down view (no `steps`, no `output`, no `input`) so
    the listing fetches stay cheap. The detail endpoint returns the full
    row including step-by-step execution log.
    """
    org_id = await _require_admin(current_user)
    svc = get_service_client()

    def _query():
        q = (
            svc.table("agent_runs")
            .select(
                "id, agent_type, triggered_by, triggered_by_user_id, status, "
                "llm_tokens_used, confidence_scores, error, created_at, "
                "started_at, completed_at, approval_id",
                count="exact",
            )
            .eq("org_id", org_id)
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
        )
        if agent_type:
            q = q.eq("agent_type", agent_type)
        if status_filter:
            q = q.eq("status", status_filter)
        return q.execute()

    res = await asyncio.to_thread(_query)
    rows = res.data or []

    # Hydrate display names for the triggering user (best-effort).
    user_ids = {r["triggered_by_user_id"] for r in rows if r.get("triggered_by_user_id")}
    name_map: dict[str, str | None] = {}
    if user_ids:
        users = await asyncio.to_thread(
            lambda: svc.table("users")
            .select("id, display_name")
            .in_("id", list(user_ids))
            .execute()
        )
        name_map = {u["id"]: u.get("display_name") for u in (users.data or [])}

    for r in rows:
        r["triggered_by_name"] = name_map.get(r.get("triggered_by_user_id") or "")
        # Compact summary the listing UI needs: step count + avg confidence.
        scores = r.get("confidence_scores") or []
        r["avg_confidence"] = (
            round(sum(scores) / len(scores), 1) if scores else None
        )

    return {
        "runs": rows,
        "total": res.count or len(rows),
        "limit": limit,
        "offset": offset,
    }


@router.get("/agent-runs/{run_id}")
async def get_agent_run(
    run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Full run detail including step-by-step execution log."""
    org_id = await _require_admin(current_user)
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("agent_runs")
        .select("*")
        .eq("id", run_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        raise HTTPException(status_code=404, detail="agent_run_not_found")

    data = row.data
    # Hydrate user name + approval status if linked.
    if data.get("triggered_by_user_id"):
        u = await asyncio.to_thread(
            lambda: svc.table("users")
            .select("display_name")
            .eq("id", data["triggered_by_user_id"])
            .maybe_single()
            .execute()
        )
        data["triggered_by_name"] = (u.data or {}).get("display_name") if u else None
    if data.get("approval_id"):
        ap = await asyncio.to_thread(
            lambda: svc.table("approvals")
            .select("id, status, resolved_at")
            .eq("id", data["approval_id"])
            .maybe_single()
            .execute()
        )
        data["approval"] = ap.data if ap else None
    return data


@router.get("/agent-runs/stats/summary")
async def agent_runs_summary(
    period: str = Query(default="30d", pattern="^(7d|30d|90d)$"),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Counts by agent_type + status for the period — drives the audit
    page's filter chips and a header strip showing volume at a glance."""
    org_id = await _require_admin(current_user)
    days = _PERIOD_DAYS[period]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    svc = get_service_client()

    rows = await asyncio.to_thread(
        lambda: svc.table("agent_runs")
        .select("agent_type, status")
        .eq("org_id", org_id)
        .gte("created_at", cutoff)
        .limit(10000)
        .execute()
    )
    items = rows.data or []
    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for r in items:
        by_type[r["agent_type"]] = by_type.get(r["agent_type"], 0) + 1
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    return {
        "period": period,
        "total_runs": len(items),
        "by_agent_type": by_type,
        "by_status": by_status,
    }


# ── Weekly digest (Day 11) ──────────────────────────────────────────────────


@router.get("/weekly-digest/preview")
async def preview_weekly_digest(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    """Render the same stats the email would contain, in JSON.

    Used by the Settings page so admins can see what a fresh digest looks
    like without sending an email. Stays in sync with the cron because both
    paths call `gather_weekly_stats` on the worker side.
    """
    org_id = await _require_admin(current_user)
    from app.services.email.worker import gather_weekly_stats

    stats = await gather_weekly_stats(org_id)
    return {"org_id": org_id, "stats": stats}


@router.post("/weekly-digest/send-now")
async def send_weekly_digest_now(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Fire a digest email to every admin in the org right now.

    Bypasses the iso-week dedupe gate so admins can test the email after
    tuning their data. Hands off to Inngest so the route returns quickly.
    """
    org_id = await _require_admin(current_user)
    from app.inngest.client import get_inngest_client
    import inngest as _inngest
    import uuid as _uuid

    client = get_inngest_client()
    await client.send(
        _inngest.Event(
            name="email/weekly-digest-now",
            data={"org_id": org_id, "triggered_by": current_user["user_id"]},
            id=f"weekly-digest-now-{org_id}-{_uuid.uuid4().hex[:8]}",
        )
    )
    return {"queued": True}


# ── Competitor mentions review ──────────────────────────────────────────────


class CompetitorMentionsDismissRequest(BaseModel):
    """Bulk-dismiss request. Either pass a list of mention ids OR a term to
    dismiss every open mention of that term. Exactly one of the two must be
    set so the caller can't accidentally close a wider set than intended."""

    ids: list[str] | None = None
    term: str | None = None

    @field_validator("ids")
    @classmethod
    def _cap_ids(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if len(v) > 500:
            raise ValueError("Too many ids (max 500).")
        return v


_MENTIONS_PAGE_MAX = 200


@router.get("/competitor-mentions")
async def list_competitor_mentions(
    status_filter: str = Query(default="open", pattern="^(open|dismissed|all)$", alias="status"),
    term: str | None = Query(default=None, max_length=200),
    user_id: str | None = Query(default=None),
    source: str | None = Query(default=None, pattern="^(chat|agent)$"),
    since: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=_MENTIONS_PAGE_MAX),
    offset: int = Query(default=0, ge=0),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Admin feed of competitor hits across the org.

    Returns rows newest first, plus a `summary.by_term` count of currently
    open mentions so the dashboard can show top-N offenders without a
    second round trip.
    """
    org_id = await _require_admin(current_user)
    svc = get_service_client()

    def _query() -> Any:
        q = (
            svc.table("competitor_mentions")
            .select(
                "id, source_kind, message_id, agent_run_id, conversation_id, "
                "user_id, matched_term, watchlist_source, snippet, match_count, "
                "status, dismissed_by, dismissed_at, created_at",
                count="exact",
            )
            .eq("org_id", org_id)
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
        )
        if status_filter != "all":
            q = q.eq("status", status_filter)
        if term:
            q = q.ilike("matched_term", term)
        if user_id:
            q = q.eq("user_id", user_id)
        if source:
            q = q.eq("source_kind", source)
        if since:
            q = q.gte("created_at", since)
        return q.execute()

    res = await asyncio.to_thread(_query)
    items = res.data or []
    total = res.count or 0

    summary_res = await asyncio.to_thread(
        lambda: svc.table("competitor_mentions")
        .select("matched_term")
        .eq("org_id", org_id)
        .eq("status", "open")
        .limit(5000)
        .execute()
    )
    by_term: dict[str, int] = {}
    for row in summary_res.data or []:
        t = row.get("matched_term")
        if not t:
            continue
        by_term[t] = by_term.get(t, 0) + 1
    top_terms = sorted(by_term.items(), key=lambda kv: kv[1], reverse=True)[:20]

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "summary": {
            "open_total": sum(by_term.values()),
            "by_term": [{"term": t, "count": c} for t, c in top_terms],
        },
    }


@router.post("/competitor-mentions/dismiss")
async def dismiss_competitor_mentions(
    body: CompetitorMentionsDismissRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Mark mentions as dismissed. Records who dismissed and when so the
    log can be audited later. Idempotent — already-dismissed rows are
    silently skipped by the `status='open'` filter."""
    org_id = await _require_admin(current_user)
    if (body.ids is None) == (body.term is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pass exactly one of `ids` or `term`.",
        )

    svc = get_service_client()
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "status": "dismissed",
        "dismissed_by": current_user["user_id"],
        "dismissed_at": now,
    }

    def _update() -> Any:
        q = (
            svc.table("competitor_mentions")
            .update(payload)
            .eq("org_id", org_id)
            .eq("status", "open")
        )
        if body.ids:
            q = q.in_("id", body.ids)
        else:
            q = q.eq("matched_term", body.term)
        return q.execute()

    res = await asyncio.to_thread(_update)
    affected = len(res.data or [])
    return {"dismissed": affected}


# ── Rate limits (V5 #78) ────────────────────────────────────────────────────


@router.get("/rate-limits")
async def get_admin_rate_limits(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Org-level quota + per-user breakdown for the admin dashboard.

    Reads the org's live monthly quota from the SAME Upstash key the limiter
    increments (`get_usage_snapshot`), so the meter never disagrees with the
    gate that actually 402s. Per-user counts come from `query_logs` over the
    last 30 days — the limiter doesn't tally per-user (it gates per-minute
    sliding window, not per-month) so this is the authoritative source for
    "which teammate consumed the quota".
    """
    from app.services.rate_limit import get_usage_snapshot

    org_id = await _require_admin(current_user)
    now = datetime.now(timezone.utc)
    thirty_ago = now - timedelta(days=30)
    one_day_ago = now - timedelta(days=1)
    svc = get_service_client()

    snap = await get_usage_snapshot(org_id)

    def _per_user() -> list[dict[str, Any]]:
        rows = (
            svc.table("query_logs")
            .select("user_id, created_at")
            .eq("org_id", org_id)
            .gte("created_at", thirty_ago.isoformat())
            .limit(20_000)
            .execute()
            .data
            or []
        )
        # Tally per-user in Python — small set sizes, simpler than PG GROUP BY
        # through PostgREST.
        agg: dict[str, dict[str, int]] = {}
        one_day_iso = one_day_ago.isoformat()
        for r in rows:
            uid = r.get("user_id") or ""
            if not uid:
                continue
            bucket = agg.setdefault(uid, {"queries_30d": 0, "queries_today": 0})
            bucket["queries_30d"] += 1
            if (r.get("created_at") or "") >= one_day_iso:
                bucket["queries_today"] += 1
        if not agg:
            return []

        # Resolve display names from `users`. The auth.users emails are also
        # joined in best-effort — failures are tolerated so a deleted user
        # never crashes the dashboard.
        users_res = (
            svc.table("users")
            .select("id, display_name")
            .in_("id", list(agg.keys()))
            .execute()
            .data
            or []
        )
        name_by_id = {u["id"]: (u.get("display_name") or "") for u in users_res}

        out: list[dict[str, Any]] = []
        for uid, counts in agg.items():
            email = None
            try:
                au = svc.auth.admin.get_user_by_id(uid)
                email = getattr(getattr(au, "user", None), "email", None)
            except Exception:
                email = None
            out.append(
                {
                    "user_id": uid,
                    "name": name_by_id.get(uid) or "Member",
                    "email": email,
                    "queries_30d": counts["queries_30d"],
                    "queries_today": counts["queries_today"],
                }
            )
        out.sort(key=lambda r: r["queries_30d"], reverse=True)
        return out

    per_user = await asyncio.to_thread(_per_user)

    # Linear projection: extrapolate this month's run-rate to month-end.
    # day_of_month==0 is impossible (UTC always has day≥1) but we guard anyway.
    day_of_month = now.day
    days_in_month = 30  # close enough for projection — not displayed
    projected = (
        int(snap.used / day_of_month * days_in_month) if day_of_month > 0 else snap.used
    )
    will_exceed = bool(snap.limit) and projected > (snap.limit or 0)

    reset_at = now + timedelta(seconds=snap.seconds_until_reset)
    pct_used: float | None = None
    if snap.limit:
        pct_used = round(snap.used / snap.limit * 100, 1)

    return {
        "org_quota": {
            "plan": snap.plan,
            "used": snap.used,
            "limit": snap.limit,  # null = unlimited
            "unlimited": snap.limit is None,
            "pct_used": pct_used,
            "reset_at": reset_at.isoformat(),
            "seconds_until_reset": snap.seconds_until_reset,
            "projected_month_end": projected,
            "will_exceed": will_exceed,
            "source": snap.source,
        },
        "per_user": per_user,
    }


# ── Embedding fine-tune (V5 #106) ───────────────────────────────────────────


@router.get("/embeddings/status")
async def get_embedding_status(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """State of the org's fine-tuning: training pair count, current model,
    last eval scores, any active job."""
    from app.config import get_settings
    from app.services.embedding_finetune import org_has_active_job

    org_id = await _require_admin(current_user)
    svc = get_service_client()
    settings = get_settings()

    def _query() -> dict[str, Any]:
        org = (
            svc.table("organizations")
            .select("id, name, plan, metadata")
            .eq("id", org_id)
            .maybe_single()
            .execute()
            .data
            or {}
        )
        pair_count = (
            svc.table("embedding_training_pairs")
            .select("id", count="exact", head=True)
            .eq("org_id", org_id)
            .execute()
            .count
            or 0
        )
        last_job = (
            svc.table("embedding_fine_tune_jobs")
            .select(
                "id, status, base_model, fine_tuned_model_id, training_pairs_count, "
                "eval_score_before, eval_score_after, error_message, started_at, "
                "completed_at, created_at"
            )
            .eq("org_id", org_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
            .data
            or []
        )
        return {
            "org": org,
            "pair_count": pair_count,
            "last_job": last_job[0] if last_job else None,
        }

    raw = await asyncio.to_thread(_query)
    org_meta = (raw["org"].get("metadata") or {}) if isinstance(raw["org"].get("metadata"), dict) else {}
    active = await asyncio.to_thread(lambda: org_has_active_job(org_id))

    eval_improvement: float | None = None
    last_job = raw["last_job"]
    if last_job and last_job.get("eval_score_after") is not None and last_job.get("eval_score_before") is not None:
        eval_improvement = round(
            float(last_job["eval_score_after"]) - float(last_job["eval_score_before"]),
            4,
        )

    return {
        "plan": raw["org"].get("plan") or "free",
        "is_eligible": (raw["org"].get("plan") or "") in {"business", "enterprise"},
        "backend_configured": bool(settings.modal_finetune_endpoint),
        "training_pairs": raw["pair_count"],
        "min_pairs": settings.embedding_finetune_min_pairs,
        "recommended_pairs": settings.embedding_finetune_recommended_pairs,
        "current_model": org_meta.get("embedding_model"),
        "fine_tuned_at": org_meta.get("embedding_fine_tuned_at"),
        "eval_improvement": eval_improvement,
        "last_job": last_job,
        "active_job": active,
    }


@router.post("/embeddings/fine-tune", status_code=status.HTTP_202_ACCEPTED)
async def trigger_embedding_fine_tune(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Start a fine-tune job. Enterprise / business plan only. Refuses if
    another job for this org is already in flight."""
    import inngest as _inngest_pkg

    from app.config import get_settings
    from app.inngest.client import get_inngest_client
    from app.services.embedding_finetune import org_has_active_job

    org_id = await _require_admin(current_user)
    settings = get_settings()
    svc = get_service_client()

    org = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select("id, plan")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    plan = ((org.data or {}) if org else {}).get("plan") or "free"
    if plan not in {"business", "enterprise"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fine-tuning requires the Business or Enterprise plan.",
        )

    if not settings.modal_finetune_endpoint:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Fine-tuning backend not configured. Set MODAL_FINETUNE_ENDPOINT.",
        )

    active = await asyncio.to_thread(lambda: org_has_active_job(org_id))
    if active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A fine-tune job is already in progress (status={active.get('status')}).",
        )

    pair_count = await asyncio.to_thread(
        lambda: svc.table("embedding_training_pairs")
        .select("id", count="exact", head=True)
        .eq("org_id", org_id)
        .execute()
        .count
    ) or 0
    if pair_count < settings.embedding_finetune_min_pairs:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Need at least {settings.embedding_finetune_min_pairs} training pairs "
                f"to fine-tune (have {pair_count})."
            ),
        )

    def _create_job() -> dict[str, Any]:
        res = (
            svc.table("embedding_fine_tune_jobs")
            .insert(
                {
                    "org_id": org_id,
                    "status": "pending",
                    "triggered_by": current_user.get("user_id"),
                    "training_pairs_count": pair_count,
                }
            )
            .execute()
            .data
            or []
        )
        return res[0] if res else {}

    job_row = await asyncio.to_thread(_create_job)
    if not job_row:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not create fine-tune job.",
        )

    client = get_inngest_client()
    await client.send(
        _inngest_pkg.Event(
            name="embeddings/fine-tune",
            data={
                "org_id": org_id,
                "job_id": job_row["id"],
                "base_model": job_row.get("base_model"),
            },
        )
    )
    return {"job_id": job_row["id"], "status": "started"}


# ── Day 15: actionable feedback-alert surface ───────────────────────────────
#
# The threshold-alert email points admins here. Rows are negative-feedback
# assistant messages with feedback_analysis already classified, joined with
# the parent user query so the admin sees what was asked, what answered,
# and why it was flagged — all in one row, deep-linkable to the chat.

_FEEDBACK_REASONS = {
    "wrong_tone",
    "missing_context",
    "outdated_policy",
    "hallucination",
    "wrong_format",
    "unknown",
}


@router.get("/feedback-flagged")
async def list_feedback_flagged(
    days: int = Query(default=7, ge=1, le=90),
    reason: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Recent negative-feedback messages with LLM-classified failure reason.

    Excludes messages still awaiting analysis (`feedback_analysis IS NULL`) —
    the threshold-alert flow only ever cares about classified rows, and the
    partial index in migration 035 makes the filtered query cheap.
    """
    org_id = await _require_admin(current_user)
    if reason and reason not in _FEEDBACK_REASONS:
        raise HTTPException(status_code=400, detail="unknown_reason")

    svc = get_service_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    def _query() -> Any:
        q = (
            svc.table("messages")
            .select(
                "id, conversation_id, content, sources, "
                "feedback_analysis, created_at"
            )
            .eq("org_id", org_id)
            .eq("feedback", "negative")
            .filter("feedback_analysis", "not.is", "null")
            .gte("created_at", cutoff)
        )
        if reason:
            q = q.filter(
                "feedback_analysis->>failure_reason", "eq", reason,
            )
        return q.order("created_at", desc=True).limit(limit).execute()

    res = await asyncio.to_thread(_query)
    rows = res.data or []

    # Per-row parent-query lookup. The N+1 is acceptable here — `limit` is
    # bounded to 200 and these are short bounded reads on an indexed
    # (conversation_id, role, created_at) path.
    items: list[dict[str, Any]] = []
    for r in rows:
        analysis = r.get("feedback_analysis") or {}
        parent_res = await asyncio.to_thread(
            lambda r=r: svc.table("messages")
            .select("content")
            .eq("conversation_id", r["conversation_id"])
            .eq("role", "user")
            .lt("created_at", r["created_at"])
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        query_text = ((parent_res.data or [{}])[0] or {}).get("content") or ""
        items.append({
            "message_id": r["id"],
            "conversation_id": r["conversation_id"],
            "snippet": _trim(r.get("content") or "", 320),
            "query": _trim(query_text, 240),
            "sources": (r.get("sources") or [])[:5],
            "failure_reason": analysis.get("failure_reason") or "unknown",
            "suggested_doc": analysis.get("suggested_doc"),
            "alerted_at": analysis.get("alerted_at"),
            "created_at": r["created_at"],
        })

    # Roll up by reason for the filter chips so the UI can show counts
    # without a second round trip.
    reasons_summary: dict[str, int] = {}
    for i in items:
        rsn = i["failure_reason"]
        reasons_summary[rsn] = reasons_summary.get(rsn, 0) + 1

    return {
        "days": days,
        "reason": reason,
        "items": items,
        "reasons": reasons_summary,
    }


def _trim(s: str, n: int) -> str:
    s = (s or "").strip().replace("\r", " ").replace("\n", " ")
    return s[: n - 1] + "…" if len(s) > n else s


class FeedbackDocFromSuggestion(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1, max_length=200_000)
    # Lineage: the messages whose feedback motivated this doc. Stored on
    # documents.metadata so we can later show "this doc resolved N flagged
    # messages" or unwind a wrong fix.
    source_message_ids: list[str] = Field(default_factory=list, max_length=20)
    failure_reason: str | None = Field(default=None, max_length=40)

    @field_validator("failure_reason")
    @classmethod
    def _validate_reason(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v not in _FEEDBACK_REASONS:
            raise ValueError("unknown failure_reason")
        return v


@router.post("/feedback-flagged/from-suggestion", status_code=status.HTTP_201_CREATED)
async def create_doc_from_suggestion(
    body: FeedbackDocFromSuggestion,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Create a real document from an admin-edited suggested-fix text.

    Mirrors the document-draft approve flow: insert a `documents` row in
    `processing`, queue `doc/process-text` against the same chunk+embed
    pipeline that Notion/Drive ingest uses. The doc becomes searchable
    once Inngest flips status to `ready`.
    """
    org_id = await _require_admin(current_user)
    user_id = current_user["user_id"]
    svc = get_service_client()

    title = body.title.strip() or "Feedback-driven guidance"
    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="empty_content")

    # `source='upload'` keeps us inside the documents_source_check CHECK
    # constraint (migration 036). Provenance lives in metadata.
    doc_row = await asyncio.to_thread(
        lambda: svc.table("documents")
        .insert({
            "org_id": org_id,
            "name": title[:255],
            "file_type": "md",
            "status": "processing",
            "uploaded_by": user_id,
            "source": "upload",
            "metadata": {
                "origin": "feedback_alert",
                "failure_reason": body.failure_reason,
                "source_message_ids": body.source_message_ids[:20],
            },
        })
        .execute()
    )
    if not doc_row.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="document_insert_failed",
        )
    new_doc_id = doc_row.data[0]["id"]

    import inngest as _inngest_pkg

    from app.inngest.client import get_inngest_client

    client = get_inngest_client()
    await client.send(
        _inngest_pkg.Event(
            name="doc/process-text",
            data={"doc_id": new_doc_id, "org_id": org_id, "text": content},
            id=f"feedback-doc-{new_doc_id}",
        )
    )

    return {"document_id": new_doc_id, "status": "processing"}
