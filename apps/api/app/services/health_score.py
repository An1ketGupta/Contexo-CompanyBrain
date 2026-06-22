"""Document health scoring (V4 #34).

`compute_health_score` is pure and unit-testable. `recompute_org_health`
loops over an org's `ready` documents and writes back the new score/label.
Triggered nightly by the Inngest cron in `app/inngest/functions.py`.

Scoring intent:
  * Health collapses three signals into one number teams can sort by:
      40% recency of access     (was this looked at recently?)
      40% access frequency      (how often *over time*, not just last week)
      20% gap-flag penalty      (knowledge-gap detector tagged it)
  * A doc that's brand-new but never accessed gets `unused`, not `at_risk` —
    a never-accessed score is 0.0 (no signal at all). At-risk implies the
    doc has been around long enough to expect activity.
  * Score 0.65+ healthy, 0.35–0.65 stale, 0.0–0.35 at_risk, 0.0 unused.

We measure access via `chunk_citations.cited_at` (citations are the real
"this doc influenced an answer" signal — not a user visit). Frequency uses
the last 90 days normalised to citations-per-week.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.database import get_service_client

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class HealthFactors:
    age_days: int
    days_since_accessed: int | None  # None = never
    access_frequency: float          # citations / week, last 90d
    gap_flag_count: int


@dataclass(frozen=True)
class HealthResult:
    score: float                     # 0.0 .. 1.0
    label: str                       # 'healthy' | 'stale' | 'at_risk' | 'unused'


def compute_health_score(f: HealthFactors) -> HealthResult:
    """Pure function — same inputs always produce same outputs."""
    # Recency score (0..1) — step function so the scoring story is explainable
    # to a customer without a formula.
    if f.days_since_accessed is None:
        recency = 0.0
    elif f.days_since_accessed <= 7:
        recency = 1.0
    elif f.days_since_accessed <= 30:
        recency = 0.75
    elif f.days_since_accessed <= 60:
        recency = 0.4
    elif f.days_since_accessed <= 90:
        recency = 0.2
    else:
        recency = 0.0

    # Frequency (0..1). 5 cites/week saturates — beyond that we don't reward
    # further; we just don't want to penalise heavy use.
    frequency = min(f.access_frequency / 5.0, 1.0) if f.access_frequency > 0 else 0.0

    # Gap penalty subtracts up to 20%. 10+ flags ⇒ max penalty.
    gap_penalty = min(f.gap_flag_count / 10.0, 1.0) * 0.20

    raw = (recency * 0.40) + (frequency * 0.40) - gap_penalty
    score = max(0.0, min(1.0, raw))

    # Label thresholds. Note "unused" is reserved for genuinely-never-touched
    # docs that ALSO have no other signal — otherwise a brand new doc would
    # always be "unused" which reads wrong on day one.
    if f.days_since_accessed is None and f.access_frequency == 0.0:
        label = "unused"
    elif score >= 0.65:
        label = "healthy"
    elif score >= 0.35:
        label = "stale"
    else:
        label = "at_risk"

    return HealthResult(score=round(score, 3), label=label)


async def recompute_org_health(org_id: str) -> int:
    """Recompute and persist health for every ready doc in an org.

    Returns the number of documents updated. Service-role client; intended
    to be called from the nightly Inngest cron only.
    """
    svc = get_service_client()
    now = datetime.now(UTC)
    cutoff_90d = now - timedelta(days=90)

    docs_res = await asyncio.to_thread(
        lambda: svc.table("documents")
        .select("id, created_at, last_accessed_at, gap_flag_count")
        .eq("org_id", org_id)
        .eq("status", "ready")
        .execute()
    )
    docs = docs_res.data or []
    if not docs:
        return 0

    updated = 0
    for doc in docs:
        doc_id = doc["id"]
        # Count citations in the last 90 days for this document.
        try:
            cite_res = await asyncio.to_thread(
                lambda did=doc_id: svc.table("chunk_citations")
                .select("id", count="exact")
                .eq("org_id", org_id)
                .eq("document_id", did)
                .gte("cited_at", cutoff_90d.isoformat())
                .limit(1)
                .execute()
            )
            citation_count = cite_res.count or 0
        except Exception as exc:
            log.warning("citation_count read failed doc=%s err=%s", doc_id, exc)
            citation_count = 0

        created = _parse_ts(doc.get("created_at")) or now
        last = _parse_ts(doc.get("last_accessed_at"))
        age_days = max(0, (now - created).days)
        days_since = (now - last).days if last else None
        # 90 days / 7 days/week = ~12.857 weeks. Use 13 for a clean denominator.
        frequency = citation_count / 13.0

        result = compute_health_score(HealthFactors(
            age_days=age_days,
            days_since_accessed=days_since,
            access_frequency=frequency,
            gap_flag_count=int(doc.get("gap_flag_count") or 0),
        ))

        try:
            await asyncio.to_thread(
                lambda did=doc_id, r=result: svc.table("documents")
                .update({
                    "health_score": r.score,
                    "health_label": r.label,
                    "health_computed_at": now.isoformat(),
                })
                .eq("id", did)
                .execute()
            )
            updated += 1
        except Exception as exc:
            log.warning("health update failed doc=%s err=%s", doc_id, exc)

    return updated


async def touch_last_accessed(document_ids: Iterable[str]) -> None:
    """Bump last_accessed_at to now() for the given documents.

    Called from the citation tracker after a turn cites these docs. Wrapped
    in try/except: this is telemetry-adjacent and must not break the chat
    flow."""
    ids = [d for d in dict.fromkeys(document_ids) if d]
    if not ids:
        return
    try:
        svc = get_service_client()
        now_iso = datetime.now(UTC).isoformat()
        await asyncio.to_thread(
            lambda: svc.table("documents")
            .update({"last_accessed_at": now_iso})
            .in_("id", ids)
            .execute()
        )
    except Exception as exc:
        log.warning("touch_last_accessed failed: %s", exc)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    # Supabase returns ISO 8601 strings, sometimes with trailing 'Z'.
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, TypeError):
        return None
