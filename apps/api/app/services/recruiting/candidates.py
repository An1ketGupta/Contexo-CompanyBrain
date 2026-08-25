"""Recruiting candidate sync — fan out across ATS adapters, upsert to Notion.

The publish flow creates a Notion tracker page with a child candidate
database. This module fills that database: it pulls applications from every
ATS the requisition was posted to, normalises them, and either creates a new
Notion row or PATCHes the existing one keyed by external_id.

The sync is intentionally on-demand (no background poller yet): the
recruiter clicks "Sync Candidates" on the requisition detail page and we
fan out in parallel. The local recruiting_candidates row stores the
candidate state + the Notion page id so future syncs upsert in place
instead of duplicating rows.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from app.database import get_service_client
from app.services.integrations import notion as notion_svc
from app.services.integrations.ats import ashby, greenhouse, lever
from app.services.recruiting import audit_log

log = logging.getLogger(__name__)


_CANDIDATE_FETCHERS = {
    "greenhouse": greenhouse.fetch_candidates,
    "lever": lever.fetch_candidates,
    "ashby": ashby.fetch_candidates,
}


async def sync_candidates(
    *,
    org_id: str,
    user_id: str,
    requisition_id: str,
) -> dict[str, Any]:
    """Fetch candidates from every ATS the requisition was published to and
    mirror them into the tracker's Notion database.

    Returns a SyncCandidatesResponse-shaped dict — per-platform counts plus
    aggregate totals and the first DB url (for the UI's "Open in Notion"
    button). Raises LookupError when the requisition doesn't exist or
    RuntimeError when no Notion candidate database has been provisioned
    (which happens when the publish flow ran before migration 070 or when
    the parent page wasn't accessible at publish time).
    """
    svc = get_service_client()

    def _fetch_req() -> dict[str, Any] | None:
        res = (
            svc.table("job_requisitions")
            .select(
                "id, org_id, ats_postings, notion_candidates_db_id, "
                "notion_tracker_url, status"
            )
            .eq("id", requisition_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    row = await asyncio.to_thread(_fetch_req)
    if not row:
        raise LookupError("requisition_not_found")

    db_id = row.get("notion_candidates_db_id")
    if not db_id:
        # No DB to write to. Surface as RuntimeError so the router maps it
        # to a 409 the UI can translate into "Re-publish this requisition
        # so we can set up the candidate tracker."
        raise RuntimeError("notion_candidates_db_missing")

    # Only sync from successful ATS postings (URL set, no error).
    postings: list[dict[str, Any]] = row.get("ats_postings") or []
    targets: list[tuple[str, str]] = []
    for p in postings:
        platform = p.get("platform")
        job_id = p.get("job_id")
        if not platform or not job_id:
            continue
        if platform not in _CANDIDATE_FETCHERS:
            continue
        if p.get("error"):
            continue
        targets.append((platform, str(job_id)))

    if not targets:
        # Nothing to do — record a skipped audit row and return zeros so the
        # UI can render "No connected ATS to sync from".
        await audit_log.write(
            org_id=org_id,
            requisition_id=requisition_id,
            actor_user_id=user_id,
            action="candidate_sync",
            status="skipped",
            request_summary={"reason": "no_ats_postings"},
        )
        synced_at = datetime.now(UTC)
        await _persist_sync_state(
            requisition_id=requisition_id,
            org_id=org_id,
            synced_at=synced_at,
            error=None,
        )
        return {
            "requisition_id": requisition_id,
            "synced_at": synced_at.isoformat(),
            "total_candidates": 0,
            "new_candidates": 0,
            "updated_candidates": 0,
            "per_platform": {},
            "notion_db_url": None,
            "errors": [],
        }

    fetch_tasks = [
        asyncio.create_task(_fetch_one(platform, org_id, job_id))
        for platform, job_id in targets
    ]
    fetch_results = await asyncio.gather(*fetch_tasks)

    # Per-platform summary the UI can render verbatim.
    per_platform: dict[str, dict[str, Any]] = {}
    all_candidates: list[dict[str, Any]] = []
    errors: list[str] = []
    for (platform, job_id), result in zip(targets, fetch_results, strict=True):
        if isinstance(result, BaseException) or result.get("error"):
            err = (
                str(result)
                if isinstance(result, BaseException)
                else result.get("error")
            )
            errors.append(f"{platform}: {err}")
            per_platform[platform] = {"error": err, "count": 0}
            continue
        candidates = result.get("candidates") or []
        per_platform[platform] = {"count": len(candidates), "error": None}
        all_candidates.extend(candidates)

    # Upsert each candidate. We do this sequentially so Notion rate limits
    # don't get hammered on a 50-candidate sync — Notion's stated cap is 3
    # req/s averaged.
    new_count = 0
    updated_count = 0
    for cand in all_candidates:
        outcome = await _upsert_candidate(
            org_id=org_id,
            requisition_id=requisition_id,
            db_id=db_id,
            candidate=cand,
        )
        if outcome == "created":
            new_count += 1
        elif outcome == "updated":
            updated_count += 1

    synced_at = datetime.now(UTC)
    aggregate_error = "; ".join(errors) if errors else None
    await _persist_sync_state(
        requisition_id=requisition_id,
        org_id=org_id,
        synced_at=synced_at,
        error=aggregate_error,
    )
    await audit_log.write(
        org_id=org_id,
        requisition_id=requisition_id,
        actor_user_id=user_id,
        action="candidate_sync",
        status="failure" if errors else "success",
        request_summary={"platforms": [p for p, _ in targets]},
        response_summary={
            "new": new_count,
            "updated": updated_count,
            "total": len(all_candidates),
            "per_platform": per_platform,
        },
        error_message=aggregate_error,
    )

    log.info(
        "recruiting.candidate_sync.done req=%s total=%d new=%d updated=%d errors=%d",
        requisition_id,
        len(all_candidates),
        new_count,
        updated_count,
        len(errors),
    )
    return {
        "requisition_id": requisition_id,
        "synced_at": synced_at.isoformat(),
        "total_candidates": len(all_candidates),
        "new_candidates": new_count,
        "updated_candidates": updated_count,
        "per_platform": per_platform,
        "notion_db_url": row.get("notion_tracker_url"),
        "errors": errors,
    }


async def _fetch_one(
    platform: str, org_id: str, job_id: str
) -> dict[str, Any]:
    """Adapter dispatch wrapped so a single-platform failure doesn't abort
    the others. Returns {candidates: [...]} on success, {error: "..."} on
    any exception."""
    fetcher = _CANDIDATE_FETCHERS[platform]
    try:
        candidates = await fetcher(org_id=org_id, job_id=job_id)
        return {"candidates": candidates}
    except PermissionError as exc:
        return {"error": f"not_connected_or_unauthorized: {exc}"}
    except Exception as exc:
        log.warning(
            "recruiting.candidate_sync.fetch_failed platform=%s job=%s err=%s",
            platform,
            job_id,
            exc,
        )
        return {"error": f"{type(exc).__name__}: {exc}"}


async def _upsert_candidate(
    *,
    org_id: str,
    requisition_id: str,
    db_id: str,
    candidate: dict[str, Any],
) -> str:
    """Insert or update one candidate locally + in Notion.

    Returns one of: "created", "updated", "skipped". "skipped" covers
    "Notion write failed but local cache write succeeded" — the next sync
    will retry the Notion side without re-fetching.
    """
    svc = get_service_client()
    platform = candidate.get("ats_platform")
    external_id = candidate.get("external_id")
    if not platform or not external_id:
        return "skipped"

    def _find_existing() -> dict[str, Any] | None:
        res = (
            svc.table("recruiting_candidates")
            .select("id, notion_page_id")
            .eq("org_id", org_id)
            .eq("requisition_id", requisition_id)
            .eq("ats_platform", platform)
            .eq("external_id", external_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    existing = await asyncio.to_thread(_find_existing)

    common_row = {
        "org_id": org_id,
        "requisition_id": requisition_id,
        "ats_platform": platform,
        "external_id": external_id,
        "full_name": candidate.get("full_name"),
        "email": candidate.get("email"),
        "phone": candidate.get("phone"),
        "current_company": candidate.get("current_company"),
        "current_title": candidate.get("current_title"),
        "stage": candidate.get("stage"),
        "resume_url": candidate.get("resume_url"),
        "candidate_url": candidate.get("candidate_url"),
        "applied_at": candidate.get("applied_at"),
        "raw_data": candidate,
        "updated_at": datetime.now(UTC).isoformat(),
    }

    if existing:
        page_id = existing.get("notion_page_id")
        if page_id:
            try:
                await notion_svc.update_candidate_row(
                    org_id=org_id, page_id=page_id, candidate=candidate
                )
            except Exception as exc:
                log.warning(
                    "recruiting.candidate_sync.notion_patch_failed page=%s err=%s",
                    page_id,
                    exc,
                )
        else:
            # We have a local cache hit but no Notion page id — happens when
            # a prior sync created the local row before the DB existed.
            # Create the page now so the row catches up.
            try:
                page_id = await notion_svc.create_candidate_row(
                    org_id=org_id, database_id=db_id, candidate=candidate
                )
                common_row["notion_page_id"] = page_id
            except Exception as exc:
                log.warning(
                    "recruiting.candidate_sync.notion_create_failed_on_update err=%s",
                    exc,
                )

        await asyncio.to_thread(
            lambda: svc.table("recruiting_candidates")
            .update(common_row)
            .eq("id", existing["id"])
            .execute()
        )
        return "updated"

    # Net-new candidate: create the Notion row first so we can persist the
    # page id on the local row in a single insert.
    try:
        page_id = await notion_svc.create_candidate_row(
            org_id=org_id, database_id=db_id, candidate=candidate
        )
    except Exception as exc:
        log.warning("recruiting.candidate_sync.notion_create_failed err=%s", exc)
        page_id = None
    common_row["notion_page_id"] = page_id

    await asyncio.to_thread(
        lambda: svc.table("recruiting_candidates").insert(common_row).execute()
    )
    return "created"


async def _persist_sync_state(
    *,
    requisition_id: str,
    org_id: str,
    synced_at: datetime,
    error: str | None,
) -> None:
    """Stamp the requisition with the last sync time + aggregated error.

    Kept as a separate function so it's easy to call from both the success
    path and the early-return "nothing to sync" path without copying the
    update payload twice.
    """
    svc = get_service_client()
    payload: dict[str, Any] = {
        "candidates_last_synced_at": synced_at.isoformat(),
        "candidates_last_sync_error": error,
    }
    await asyncio.to_thread(
        lambda: svc.table("job_requisitions")
        .update(payload)
        .eq("id", requisition_id)
        .eq("org_id", org_id)
        .execute()
    )
