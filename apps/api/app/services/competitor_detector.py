"""Competitor watchlist detection — fires on assistant output.

Two watchlists feed the detector:
  * org-level (organizations.competitor_names) — admin-managed, applied to
    every output produced in the workspace.
  * user-level (users.competitor_names) — per-user personal list, layered
    on top of the org list. Only the user's own outputs are scanned
    against it.

Detection is deterministic regex matching, no LLM call:
  * Case-insensitive.
  * Whole-word — `Acme` matches "Acme launched" but not "acmecorp".
  * Multi-word terms are escaped and joined; a term with internal spaces
    matches the same sequence of words ignoring case.
  * One compiled pattern per (org_terms, user_terms) lookup, cached at the
    org/user grain so a chatty user doesn't recompile per turn.

Persistence is at term grain (one row per output × matched term × list)
so the admin review page can group, filter, and bulk-dismiss by term
without app-side aggregation.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Literal

from app.database import get_service_client
from app.observability import get_logger

log = get_logger(__name__)

# Whole-word boundary — picks up "Acme." and "Acme!" but not "acmecorp".
# Custom rather than \b because \b doesn't handle terms ending in non-word
# characters (e.g. "C++"), and lookarounds let us be explicit.
_BOUNDARY_LEFT = r"(?:(?<=^)|(?<=[^A-Za-z0-9]))"
_BOUNDARY_RIGHT = r"(?=$|[^A-Za-z0-9])"

# How much context to capture either side of the first hit. Stored on the
# DB row (capped at 600 chars by the schema) so the admin review page can
# show what was said without re-loading the source message.
_SNIPPET_RADIUS = 100

# In-process cache of (org_id, user_id|None) → CompiledLists. Short TTL —
# we'd rather hit the DB occasionally than serve a stale watchlist for
# long. Lock guards thundering-herd on a cold (org, user) pair.
_CACHE_TTL_SECONDS = 30.0
_cache: dict[tuple[str, str | None], "_CachedTerms"] = {}
_cache_lock = asyncio.Lock()


WatchlistSource = Literal["org", "user"]


@dataclass(frozen=True)
class CompetitorMatch:
    """One detection — a term + which list found it + a context snippet."""

    term: str
    source: WatchlistSource
    count: int
    snippet: str


@dataclass(frozen=True)
class CompetitorTerms:
    """The merged org + user list for a single request."""

    org_terms: tuple[str, ...]
    user_terms: tuple[str, ...]

    @property
    def has_any(self) -> bool:
        return bool(self.org_terms) or bool(self.user_terms)


@dataclass(frozen=True)
class _CachedTerms:
    terms: CompetitorTerms
    fetched_at: float


def _clean_terms(raw: list[str] | None) -> tuple[str, ...]:
    """Dedupe, strip, drop empties. Order preserved on first-seen so the
    admin's list ordering survives a round trip."""
    if not raw:
        return ()
    seen: set[str] = set()
    out: list[str] = []
    for t in raw:
        if not isinstance(t, str):
            continue
        cleaned = " ".join(t.split()).strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return tuple(out)


def _compile_pattern(terms: tuple[str, ...]) -> re.Pattern[str] | None:
    """One regex for many terms — alternation with whole-word boundaries.

    Returns None for an empty list so callers can short-circuit.
    """
    if not terms:
        return None
    # Sort longest-first so a list with "Acme Cloud" and "Acme" prefers the
    # longer match (regex alternation is greedy left-to-right).
    by_length = sorted(terms, key=len, reverse=True)
    alternation = "|".join(re.escape(t) for t in by_length)
    return re.compile(
        f"{_BOUNDARY_LEFT}({alternation}){_BOUNDARY_RIGHT}",
        re.IGNORECASE,
    )


def _snippet_for(text: str, start: int, end: int) -> str:
    """Capture ±_SNIPPET_RADIUS chars around the hit, collapse whitespace,
    and add ellipses so the UI can render a single-line preview."""
    lo = max(0, start - _SNIPPET_RADIUS)
    hi = min(len(text), end + _SNIPPET_RADIUS)
    chunk = text[lo:hi]
    chunk = " ".join(chunk.split())
    prefix = "…" if lo > 0 else ""
    suffix = "…" if hi < len(text) else ""
    out = f"{prefix}{chunk}{suffix}"
    return out[:580]  # well under the DB cap


def detect_competitors(
    text: str,
    *,
    org_terms: tuple[str, ...] | list[str],
    user_terms: tuple[str, ...] | list[str] = (),
) -> list[CompetitorMatch]:
    """Scan `text` for any term in either watchlist.

    Returns one CompetitorMatch per (term, source) hit. A term that lives
    in BOTH lists is reported once with `source='org'` — the org list
    takes precedence because it represents workspace policy.
    """
    if not text:
        return []

    org_tuple = _clean_terms(list(org_terms))
    user_tuple = _clean_terms(list(user_terms))
    # Strip duplicates that already belong to org. User watchlist is a
    # personal overlay, not a duplicate of the org policy.
    org_lower = {t.lower() for t in org_tuple}
    user_tuple = tuple(t for t in user_tuple if t.lower() not in org_lower)

    matches: list[CompetitorMatch] = []
    for src, terms in (("org", org_tuple), ("user", user_tuple)):
        pattern = _compile_pattern(terms)
        if not pattern:
            continue
        # Map lowercased term → canonical form (the version from the watchlist)
        # so a hit "ACME" reports back as "Acme" (the way the admin typed it).
        canonical_by_lower = {t.lower(): t for t in terms}
        # term → (count, first_match_span)
        per_term: dict[str, tuple[int, tuple[int, int]]] = {}
        for m in pattern.finditer(text):
            hit_key = m.group(1).lower()
            canonical = canonical_by_lower.get(hit_key, m.group(1))
            existing = per_term.get(canonical)
            if existing is None:
                per_term[canonical] = (1, m.span(1))
            else:
                per_term[canonical] = (existing[0] + 1, existing[1])
        for term, (count, (s, e)) in per_term.items():
            matches.append(
                CompetitorMatch(
                    term=term,
                    source=src,  # type: ignore[arg-type]
                    count=count,
                    snippet=_snippet_for(text, s, e),
                )
            )
    return matches


async def get_competitor_terms(
    *, org_id: str, user_id: str | None = None
) -> CompetitorTerms:
    """Return the merged org + user watchlist for one request.

    Cached at the (org, user) grain for `_CACHE_TTL_SECONDS`. A miss
    fans into one or two PostgREST reads — both small, both indexed.
    Failure is non-fatal: we return empty lists so chat keeps working
    even if the watchlist read blew up.
    """
    if not org_id:
        return CompetitorTerms(org_terms=(), user_terms=())

    key = (org_id, user_id)
    now = time.monotonic()
    cached = _cache.get(key)
    if cached and (now - cached.fetched_at) < _CACHE_TTL_SECONDS:
        return cached.terms

    async with _cache_lock:
        cached = _cache.get(key)
        if cached and (time.monotonic() - cached.fetched_at) < _CACHE_TTL_SECONDS:
            return cached.terms

        svc = get_service_client()

        def _fetch_org() -> tuple[str, ...]:
            try:
                res = (
                    svc.table("organizations")
                    .select("competitor_names")
                    .eq("id", org_id)
                    .maybe_single()
                    .execute()
                )
                row = (res.data or {}) if res else {}
                return _clean_terms(row.get("competitor_names") or [])
            except Exception as exc:
                log.warning("competitor_org_terms_fetch_failed", error=str(exc))
                return ()

        def _fetch_user() -> tuple[str, ...]:
            if not user_id:
                return ()
            try:
                res = (
                    svc.table("users")
                    .select("competitor_names")
                    .eq("id", user_id)
                    .maybe_single()
                    .execute()
                )
                row = (res.data or {}) if res else {}
                return _clean_terms(row.get("competitor_names") or [])
            except Exception as exc:
                log.warning("competitor_user_terms_fetch_failed", error=str(exc))
                return ()

        org_terms, user_terms = await asyncio.gather(
            asyncio.to_thread(_fetch_org),
            asyncio.to_thread(_fetch_user),
        )
        terms = CompetitorTerms(org_terms=org_terms, user_terms=user_terms)
        _cache[key] = _CachedTerms(terms=terms, fetched_at=time.monotonic())
        return terms


def invalidate(org_id: str, user_id: str | None = None) -> None:
    """Drop cache entries for an org. Called after settings writes so the
    same worker that handled the PATCH picks the new list up on the next
    chat turn. Other workers age out via TTL."""
    if user_id is not None:
        _cache.pop((org_id, user_id), None)
        return
    # Bulk-evict every (org, *) entry — settings UI changes the org list
    # rarely, so the scan cost is fine.
    for key in [k for k in _cache if k[0] == org_id]:
        _cache.pop(key, None)


def invalidate_all() -> None:
    """Test/dev hook."""
    _cache.clear()


# ── Persistence ──────────────────────────────────────────────────────────────

async def persist_chat_mentions(
    *,
    org_id: str,
    message_id: str,
    conversation_id: str | None,
    user_id: str | None,
    matches: list[CompetitorMatch],
) -> None:
    """Insert one row per match for a chat message. Idempotent via the
    unique index on (message_id, matched_term, watchlist_source) — a
    retry of the same write is a no-op rather than a duplicate."""
    if not matches:
        return
    rows = [
        {
            "org_id": org_id,
            "source_kind": "chat",
            "message_id": message_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "matched_term": m.term,
            "watchlist_source": m.source,
            "snippet": m.snippet,
            "match_count": m.count,
        }
        for m in matches
    ]
    await _insert_rows(rows)


async def persist_agent_mentions(
    *,
    org_id: str,
    agent_run_id: str,
    user_id: str | None,
    matches: list[CompetitorMatch],
) -> None:
    """Same shape as chat persistence, but attached to an agent_runs row."""
    if not matches:
        return
    rows = [
        {
            "org_id": org_id,
            "source_kind": "agent",
            "agent_run_id": agent_run_id,
            "user_id": user_id,
            "matched_term": m.term,
            "watchlist_source": m.source,
            "snippet": m.snippet,
            "match_count": m.count,
        }
        for m in matches
    ]
    await _insert_rows(rows)


async def _insert_rows(rows: list[dict]) -> None:
    if not rows:
        return
    svc = get_service_client()

    def _run() -> None:
        # Bulk insert — partial-unique indexes (one per source_kind) absorb
        # duplicates if a retried Inngest step replays. We swallow the
        # duplicate-key error explicitly so a legitimate replay doesn't
        # poison the surrounding flow.
        try:
            svc.table("competitor_mentions").insert(rows).execute()
        except Exception as exc:
            msg = str(exc).lower()
            if "duplicate key" in msg or "unique constraint" in msg:
                log.info("competitor_mentions_idempotent_retry count=%d", len(rows))
                return
            raise

    try:
        await asyncio.to_thread(_run)
    except Exception as exc:
        # Persistence is a side-effect — never block the user-visible flow.
        log.warning("competitor_mentions_insert_failed: %s", exc)
