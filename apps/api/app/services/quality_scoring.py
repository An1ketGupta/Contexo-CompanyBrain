"""Output quality scoring + benchmarking (#52, Agent2 Day 6).

The score is a single 0-10 number per assistant message, computed from
four signals:

    +3   copy_count > 0          (the user used it)
    ±3   feedback                (positive: +3, negative: -3, none: 0)
    0-3  confidence_score        (rescaled from messages.metadata.confidence.score)
    +1   sources_count > 0       (had retrieval grounding)

Normalised to 0-10 via simple offset so the worst observable case (copy=0,
neg feedback, confidence=0, no sources) sits at 0 and the best (copy>0,
pos feedback, full confidence, sources>0) sits at 10. The mapping is
intentionally not calibrated against any "ground truth" — this is an
internal trend signal, not a benchmark.

Where the components come from:
    - messages.copy_count                  (migration 025)
    - messages.feedback                    ('positive'|'negative'|NULL)
    - messages.metadata.confidence.score   (0-10, migration 014)
    - messages.sources (JSONB)             (length)
    - intent (optional)                    (analysis vs writing — categorises)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.database import get_service_client
from app.services.notifications import create_notification

log = logging.getLogger(__name__)


# ── Score computation ──────────────────────────────────────────────────────


def compute_score(
    *,
    copy_count: int,
    feedback: str | None,
    confidence_score_0_10: float | None,
    sources_count: int,
) -> float:
    """Returns a 0-10 score.

    Bounds:
        score_raw ∈ [-3, 10]:
            +3 (copy>0) + ±3 (feedback) + 0..3 (confidence) + 1 (sources>0)
        normalize: add 3, divide by 13/10. Worst → 0, best → 10.
    """
    copy_pts = 3 if copy_count and copy_count > 0 else 0
    if feedback == "positive":
        fb_pts = 3
    elif feedback == "negative":
        fb_pts = -3
    else:
        fb_pts = 0
    confidence_pts = (
        max(0.0, min(3.0, (confidence_score_0_10 or 0.0) * 0.3))
    )
    sources_pts = 1 if sources_count > 0 else 0

    raw = copy_pts + fb_pts + confidence_pts + sources_pts
    normalized = (raw + 3) * (10.0 / 13.0)
    return round(max(0.0, min(10.0, normalized)), 2)


async def score_message(*, message_id: str) -> dict[str, Any] | None:
    """Compute the score for one message, upsert into message_quality_scores.
    Returns the upserted row, or None if the message doesn't exist."""
    svc = get_service_client()

    def _fetch_msg() -> dict[str, Any] | None:
        res = (
            svc.table("messages")
            .select("id, org_id, role, copy_count, feedback, sources, metadata, conversation_id")
            .eq("id", message_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    msg = await asyncio.to_thread(_fetch_msg)
    if not msg:
        return None
    if msg.get("role") != "assistant":
        # We only score the bot's outputs — user turns don't have a quality
        # dimension in this scheme.
        return None

    sources = msg.get("sources") or []
    sources_count = len(sources) if isinstance(sources, list) else 0
    metadata = msg.get("metadata") or {}
    confidence = (metadata.get("confidence") or {}).get("score")

    score = compute_score(
        copy_count=int(msg.get("copy_count") or 0),
        feedback=msg.get("feedback"),
        confidence_score_0_10=(
            float(confidence) if isinstance(confidence, (int, float)) else None
        ),
        sources_count=sources_count,
    )

    # Category: read off intent if present in metadata. We don't compute it
    # here — task_chain stamps `intent` ('writing' | 'analysis' | etc.) into
    # the assistant message's metadata. Bucket by that.
    category = (metadata.get("intent") or {}).get("mode") if isinstance(metadata.get("intent"), dict) else metadata.get("intent")
    if isinstance(category, str):
        category = category[:40]
    else:
        category = None

    row = {
        "message_id": message_id,
        "org_id": msg["org_id"],
        "score": score,
        "copy_count": int(msg.get("copy_count") or 0),
        "feedback": msg.get("feedback"),
        "confidence_score": confidence,
        "sources_count": sources_count,
        "category": category,
        "computed_at": datetime.now(UTC).isoformat(),
    }

    def _upsert() -> dict[str, Any] | None:
        res = (
            svc.table("message_quality_scores")
            .upsert(row, on_conflict="message_id")
            .execute()
        )
        return (res.data or [None])[0]

    return await asyncio.to_thread(_upsert)


# ── Trend + alerts ──────────────────────────────────────────────────────────


async def get_quality_trend(*, org_id: str, weeks: int = 8) -> dict[str, Any]:
    """Calls the SQL RPC migration 053 exposes."""
    svc = get_service_client()

    def _trend() -> list[dict[str, Any]]:
        res = svc.rpc(
            "get_org_quality_trend",
            {"p_org_id": org_id, "p_weeks": weeks},
        ).execute()
        return res.data or []

    def _by_cat() -> list[dict[str, Any]]:
        res = svc.rpc(
            "get_org_quality_by_category",
            {"p_org_id": org_id, "p_weeks": min(weeks, 8)},
        ).execute()
        return res.data or []

    trend_rows, category_rows = await asyncio.gather(
        asyncio.to_thread(_trend), asyncio.to_thread(_by_cat)
    )
    return {"trend": trend_rows, "by_category": category_rows}


async def check_threshold_alerts() -> dict[str, Any]:
    """Daily cron entrypoint. For every org with a quality_alert_threshold,
    check this week's average. If below threshold, drop a notification to
    every admin. Idempotent via dedupe_key=ISO-week."""
    svc = get_service_client()

    def _orgs() -> list[dict[str, Any]]:
        res = (
            svc.table("organizations")
            .select("id, quality_alert_threshold, name")
            .not_.is_("quality_alert_threshold", None)
            .execute()
        )
        return res.data or []

    orgs = await asyncio.to_thread(_orgs)
    alerted = 0
    iso_week = datetime.now(UTC).strftime("%G-W%V")

    for org in orgs:
        threshold = org.get("quality_alert_threshold")
        if threshold is None:
            continue
        org_id = org["id"]
        trend = await get_quality_trend(org_id=org_id, weeks=1)
        rows = trend.get("trend") or []
        if not rows:
            continue
        latest = rows[-1]
        avg = latest.get("avg_score") or 0
        msg_count = latest.get("message_count") or 0
        if msg_count < 5:
            continue  # too few messages — noisy
        if avg < threshold:
            # Notify every admin in the org.
            def _admins(org_id=org_id) -> list[dict[str, Any]]:
                res = (
                    svc.table("users")
                    .select("id")
                    .eq("org_id", org_id)
                    .eq("role", "admin")
                    .execute()
                )
                return res.data or []

            admins = await asyncio.to_thread(_admins)
            for admin in admins:
                await create_notification(
                    org_id=org_id,
                    user_id=admin["id"],
                    type="quality_threshold_alert",
                    title="Quality score below threshold",
                    body=(
                        f"This week's average quality is {avg:.1f} (threshold {threshold}). "
                        f"Based on {msg_count} messages."
                    ),
                    link_url="/admin/quality",
                    dedupe_key=f"quality-{iso_week}-{org_id}",
                )
            alerted += 1
    return {"orgs_alerted": alerted, "orgs_checked": len(orgs)}


# ── Backfill helper (used once after deploy, callable from admin endpoint) ──


async def backfill_org_scores(*, org_id: str, days: int = 30) -> dict[str, Any]:
    """Compute scores for every assistant message in the last N days. Useful
    after migration 053 so the trend chart isn't empty on day 1."""
    svc = get_service_client()
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()

    def _fetch_ids() -> list[str]:
        res = (
            svc.table("messages")
            .select("id")
            .eq("org_id", org_id)
            .eq("role", "assistant")
            .gte("created_at", cutoff)
            .limit(5000)
            .execute()
        )
        return [r["id"] for r in (res.data or [])]

    ids = await asyncio.to_thread(_fetch_ids)
    scored = 0
    # Process in serial — we don't want a backfill to thunder against the DB
    # while live traffic is hitting it.
    for mid in ids:
        try:
            await score_message(message_id=mid)
            scored += 1
        except Exception as exc:
            log.warning("quality.backfill.failed msg=%s err=%s", mid, exc)
    return {"messages_scanned": len(ids), "scored": scored}
