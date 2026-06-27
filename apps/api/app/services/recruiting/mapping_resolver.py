"""Fuzzy-match a recruiter's free-text location/department to ATS IDs.

The recruiter types `San Francisco / Engineering` in the new-requisition form.
Greenhouse needs office_ids (integers). Ashby needs locationIds + departmentId
(UUIDs). Lever takes free-text strings, so we still confirm a canonical spelling.

How:
    1. Pull the cached taxonomy from integrations.mapping_cache for that
       org+provider (populated on connect or via refresh-mapping).
    2. Fuzzy-score the recruiter's input against every taxonomy entry using
       `difflib.SequenceMatcher` after a light normalisation pass (lowercase,
       drop punctuation, collapse whitespace).
    3. Return the best match (above threshold) + up to 3 alternatives so the
       UI can offer an override dropdown.

Confidence tiers:
    - 'high'   ≥ 0.85   → auto-use the match.
    - 'medium' 0.65–0.85 → use, but surface as a confirmation prompt.
    - 'low'    < 0.65   → don't apply; recruiter must pick from the dropdown.

Falls back gracefully when:
    - The provider isn't connected (returns confidence='none', alternatives=[]).
    - The mapping cache is empty (e.g. connect happened pre-062 migration).
    - The input string is empty.
"""
from __future__ import annotations

import asyncio
import logging
import re
import string
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any, Literal

from app.database import get_service_client

log = logging.getLogger(__name__)

Confidence = Literal["high", "medium", "low", "none"]


@dataclass
class ResolvedField:
    """One resolved field (location, department, team, …)."""

    input: str
    matched_id: str | None = None
    matched_name: str | None = None
    confidence: Confidence = "none"
    score: float = 0.0
    alternatives: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "input": self.input,
            "matched_id": self.matched_id,
            "matched_name": self.matched_name,
            "confidence": self.confidence,
            "score": round(self.score, 3),
            "alternatives": self.alternatives,
        }


@dataclass
class ResolvedMapping:
    """Full per-publish mapping bundle the publish flow consumes."""

    ats_platform: str
    location: ResolvedField
    department: ResolvedField
    team: ResolvedField | None = None
    job_template: ResolvedField | None = None
    cache_age_seconds: int | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ats_platform": self.ats_platform,
            "location": self.location.to_dict(),
            "department": self.department.to_dict(),
            "cache_age_seconds": self.cache_age_seconds,
        }
        if self.team is not None:
            out["team"] = self.team.to_dict()
        if self.job_template is not None:
            out["job_template"] = self.job_template.to_dict()
        return out

    def to_ats_metadata(self) -> dict[str, Any]:
        """Translate the resolved fields into the provider-specific
        publish_job(metadata=...) shape. Drops fields we couldn't resolve so
        the ATS adapter falls back to its own defaults."""
        meta: dict[str, Any] = {}
        if self.ats_platform == "greenhouse":
            if self.location.matched_id:
                meta["office_ids"] = [int(self.location.matched_id)]
            if self.department.matched_id:
                meta["department_id"] = int(self.department.matched_id)
        elif self.ats_platform == "ashby":
            if self.location.matched_id:
                meta["locationIds"] = [self.location.matched_id]
            if self.department.matched_id:
                meta["departmentId"] = self.department.matched_id
            if self.team and self.team.matched_id:
                meta["teamId"] = self.team.matched_id
            if self.job_template and self.job_template.matched_id:
                meta["jobTemplateId"] = self.job_template.matched_id
        # Lever takes free-text — no IDs to inject. We let the adapter use
        # the raw strings the recruiter typed (or the canonical name if we
        # matched).
        return meta


# ── Normalisation + scoring ──────────────────────────────────────────────────

_PUNCT = re.compile(rf"[{re.escape(string.punctuation)}]")
_WS = re.compile(r"\s+")


def _normalise(s: str) -> str:
    return _WS.sub(" ", _PUNCT.sub(" ", (s or "").lower())).strip()


def _score(a: str, b: str) -> float:
    """Return [0,1] similarity using a token-aware sequence ratio.

    Plain SequenceMatcher penalises word reordering ("Engineering — SF" vs
    "SF Engineering") harder than we want; bumping the score with a token-set
    overlap term smooths that.
    """
    a_n, b_n = _normalise(a), _normalise(b)
    if not a_n or not b_n:
        return 0.0
    seq = SequenceMatcher(None, a_n, b_n).ratio()
    a_tokens = set(a_n.split())
    b_tokens = set(b_n.split())
    if not a_tokens or not b_tokens:
        return seq
    overlap = len(a_tokens & b_tokens) / max(len(a_tokens | b_tokens), 1)
    return max(seq, 0.6 * seq + 0.4 * overlap)


def _confidence_for(score: float) -> Confidence:
    if score >= 0.85:
        return "high"
    if score >= 0.65:
        return "medium"
    if score > 0:
        return "low"
    return "none"


def _resolve_one(
    input_text: str | None,
    candidates: list[dict[str, Any]],
    *,
    label_key: str = "name",
    top_alternatives: int = 3,
) -> ResolvedField:
    """Score every candidate, return best + alternatives."""
    raw = (input_text or "").strip()
    if not raw or not candidates:
        return ResolvedField(input=raw)

    scored: list[tuple[float, dict[str, Any]]] = []
    for c in candidates:
        label = c.get(label_key) or ""
        if not label:
            continue
        scored.append((_score(raw, label), c))
    if not scored:
        return ResolvedField(input=raw)

    scored.sort(key=lambda kv: kv[0], reverse=True)
    best_score, best = scored[0]
    confidence = _confidence_for(best_score)
    # Only attach an id when we actually want it applied. "low" confidence
    # still surfaces a suggestion but matched_id stays None so the publish
    # flow doesn't auto-use it.
    matched_id = (
        str(best.get("id")) if best.get("id") is not None and confidence in ("high", "medium") else None
    )
    matched_name = best.get(label_key) if matched_id else None

    alternatives = [
        {
            "id": str(c.get("id")) if c.get("id") is not None else None,
            "name": c.get(label_key),
            "score": round(s, 3),
        }
        for s, c in scored[: top_alternatives + 1]
        if c is not best
    ][:top_alternatives]

    return ResolvedField(
        input=raw,
        matched_id=matched_id,
        matched_name=matched_name,
        confidence=confidence,
        score=best_score,
        alternatives=alternatives,
    )


# ── Cache fetcher ────────────────────────────────────────────────────────────


async def _fetch_cache(org_id: str, provider: str) -> tuple[dict[str, Any], int | None]:
    """Returns (mapping_cache, cache_age_seconds) for the org's integration row."""
    svc = get_service_client()

    def _run() -> dict[str, Any] | None:
        res = (
            svc.table("integrations")
            .select("mapping_cache, mapping_cached_at")
            .eq("org_id", org_id)
            .eq("provider", provider)
            .is_("scope_user_id", None)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    row = await asyncio.to_thread(_run)
    if not row:
        return {}, None
    cache = row.get("mapping_cache") or {}
    cached_at = row.get("mapping_cached_at")
    age: int | None = None
    if cached_at:
        try:
            ts = datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
            age = int((datetime.now(UTC) - ts).total_seconds())
        except (ValueError, AttributeError):
            age = None
    return cache, age


# ── Public API ───────────────────────────────────────────────────────────────


async def resolve_mapping(
    *,
    org_id: str,
    ats_platform: str,
    location_text: str | None,
    department_text: str | None,
) -> ResolvedMapping:
    """Resolve a recruiter's free-text department to ATS IDs.

    Location is intentionally NOT resolved to an ATS ID — location naming
    varies too much across ATS taxonomies (offices, remote entries, etc.) and
    causes low-confidence noise on every publish. Location is sent as free-text
    by the ATS adapters; the recruiter finishes it inside the ATS.

    Provider-specific behaviour:
        greenhouse — department_id resolved; office_ids skipped
        ashby      — departmentId + teamId + jobTemplateId resolved; locationIds skipped
        lever      — free-text for both; we canonicalise department spelling

    Returns a ResolvedMapping. Empty inputs produce 'none' confidence fields.
    """
    cache, age = await _fetch_cache(org_id, ats_platform)

    departments = cache.get("departments") or []
    locations = cache.get("locations") or []
    teams = cache.get("teams") or []
    job_templates = cache.get("job_templates") or []

    if ats_platform == "greenhouse":
        loc = ResolvedField(input=location_text or "")
        dept = _resolve_one(department_text, departments)
        return ResolvedMapping(
            ats_platform=ats_platform,
            location=loc,
            department=dept,
            cache_age_seconds=age,
        )
    if ats_platform == "ashby":
        loc = ResolvedField(input=location_text or "")
        dept = _resolve_one(department_text, departments)
        team = _resolve_one(department_text, teams)
        tmpl = _resolve_one(department_text, job_templates) if job_templates else None
        return ResolvedMapping(
            ats_platform=ats_platform,
            location=loc,
            department=dept,
            team=team,
            job_template=tmpl,
            cache_age_seconds=age,
        )
    if ats_platform == "lever":
        loc = ResolvedField(input=location_text or "")
        dept = _resolve_one(department_text, teams or departments)
        return ResolvedMapping(
            ats_platform=ats_platform,
            location=loc,
            department=dept,
            cache_age_seconds=age,
        )
    raise ValueError(f"unknown_ats_platform: {ats_platform}")


async def refresh_cache(*, org_id: str, ats_platform: str) -> dict[str, Any]:
    """Pull the live taxonomy from the ATS and write it to integrations.mapping_cache.

    Called from the connect flow (warm the cache) and from the explicit
    `POST /integrations/ats/{provider}/refresh-mapping` endpoint.

    Returns the cache payload that was written.
    """
    from app.services.integrations.ats import ashby, greenhouse, lever

    # Pull the api_key from the integrations row directly so we don't depend
    # on the adapter's per-provider _get_credentials having the right shape.
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("integrations")
        .select("access_token")
        .eq("org_id", org_id)
        .eq("provider", ats_platform)
        .is_("scope_user_id", None)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        raise PermissionError(f"{ats_platform}_not_connected")
    api_key = row.data["access_token"]

    cache: dict[str, Any] = {}

    try:
        if ats_platform == "greenhouse":
            offices, departments = await asyncio.gather(
                greenhouse.list_offices(api_key=api_key),
                greenhouse.list_departments(api_key=api_key),
            )
            cache = {"offices": offices, "departments": departments}
        elif ats_platform == "ashby":
            locations, departments, teams, templates = await asyncio.gather(
                ashby.list_locations(api_key=api_key),
                ashby.list_departments(api_key=api_key),
                ashby.list_teams(api_key=api_key),
                ashby.list_job_templates(api_key=api_key),
            )
            cache = {
                "locations": locations,
                "departments": departments,
                "teams": teams,
                "job_templates": templates,
            }
        elif ats_platform == "lever":
            locations, teams = await asyncio.gather(
                lever.list_locations(api_key=api_key),
                lever.list_teams(api_key=api_key),
            )
            cache = {"locations": locations, "teams": teams}
        else:
            raise ValueError(f"unknown_ats_platform: {ats_platform}")
    except Exception as exc:
        # Don't blow up on a partial mapping fetch — log and write whatever
        # we got so connect doesn't fail entirely on a transient 5xx.
        log.warning(
            "recruiting.mapping.refresh_partial provider=%s err=%s",
            ats_platform,
            exc,
        )

    await asyncio.to_thread(
        lambda: svc.table("integrations")
        .update(
            {
                "mapping_cache": cache,
                "mapping_cached_at": datetime.now(UTC).isoformat(),
            }
        )
        .eq("org_id", org_id)
        .eq("provider", ats_platform)
        .is_("scope_user_id", None)
        .execute()
    )
    return cache
