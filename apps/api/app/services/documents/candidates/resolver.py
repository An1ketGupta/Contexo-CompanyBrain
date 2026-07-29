"""The canonical candidate profile.

One shape, assembled from whatever upstream records exist, so that everything
downstream — mapping, validation, generation — reads from a single dotted
namespace instead of knowing which table a value came from.

Deliberately NOT a new table. The data already exists:

    job_requisitions        the role that was posted
    recruiting_candidates   the person, synced from the ATS
    onboarding_runs         the offer terms, snapshotted when HR triggered the
                            run so a later ATS edit cannot retroactively
                            contradict a document already sent
    organizations           the employer's own details

`onboarding_runs` is the highest-precedence source for anything it holds, for
exactly that snapshot reason. A resolver that preferred the live ATS row would
let a recruiter renaming a candidate in Greenhouse change the name on an offer
letter that has already gone out.

The namespace:

    candidate.*   who they are
    offer.*       what they were offered
    manager.*     who they report to
    company.*     the employer
    requisition.* the role that was posted
    meta.*        generation-time facts (today's date)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from app.database import get_service_client

log = logging.getLogger(__name__)


class CandidateNotResolvable(LookupError):
    """There is no record to build a profile from."""


def _svc():
    return get_service_client()


async def _run(fn):
    return await asyncio.to_thread(fn)


async def _fetch_one(table: str, row_id: str, org_id: str) -> dict[str, Any] | None:
    def _fetch() -> dict[str, Any] | None:
        res = (
            _svc().table(table)
            .select("*")
            .eq("id", row_id)
            .eq("org_id", org_id)
            .limit(1)
            .execute()
        )
        return (res.data or [None])[0]

    try:
        return await _run(_fetch)
    except Exception as exc:  # noqa: BLE001
        log.warning("documents.resolver_fetch_failed table=%s err=%s", table, exc)
        return None


async def _fetch_org(org_id: str) -> dict[str, Any] | None:
    """The employer's own row.

    Not `_fetch_one`: that scopes every read with `.eq("org_id", ...)`, and
    `organizations` has no such column — its `id` *is* the org. Filtering on it
    raised 42703, which `_fetch_one` logs and swallows, so every `company.*`
    field silently resolved to "" and any template with a required company name
    failed validation with no hint as to why.
    """
    def _fetch() -> dict[str, Any] | None:
        res = (
            _svc().table("organizations")
            .select("*")
            .eq("id", org_id)
            .limit(1)
            .execute()
        )
        return (res.data or [None])[0]

    try:
        return await _run(_fetch)
    except Exception as exc:  # noqa: BLE001
        log.warning("documents.resolver_org_fetch_failed err=%s", exc)
        return None


def _format_amount(amount: Any, currency: str | None) -> str:
    """Thousands-grouped with the currency code in front.

    Deliberately plain: a document that needs lakh grouping or words should
    define its own field for it rather than have the resolver guess at a
    locale it cannot know.
    """
    if amount in (None, ""):
        return ""
    try:
        return f"{currency or 'INR'} {float(amount):,.2f}"
    except (TypeError, ValueError):
        return str(amount)


def flatten(profile: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Turn the nested profile into `namespace.field` keys.

    This is what the mapping engine resolves `source_path` against, so a
    mapping is a plain dictionary lookup rather than a traversal that could
    raise halfway down.
    """
    out: dict[str, Any] = {}
    for namespace, fields in profile.items():
        for key, value in fields.items():
            out[f"{namespace}.{key}"] = value
    return out


async def resolve_profile(
    *,
    org_id: str,
    onboarding_run_id: str | None = None,
    candidate_id: str | None = None,
    requisition_id: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Assemble the canonical profile.

    At least one of the three ids must resolve to a row, or there is nothing to
    build a document about. `overrides` are HR-entered values keyed by dotted
    path, applied last — they are the manual-input source in the spec and must
    beat every system of record, because a human correcting a value is the most
    authoritative source there is.
    """
    run = (
        await _fetch_one("onboarding_runs", onboarding_run_id, org_id)
        if onboarding_run_id
        else None
    )

    # Fall back to the ids the run itself carries, so a caller only has to know
    # the run.
    if run:
        candidate_id = candidate_id or run.get("candidate_id")
        requisition_id = requisition_id or run.get("requisition_id")

    candidate = (
        await _fetch_one("recruiting_candidates", candidate_id, org_id)
        if candidate_id
        else None
    )
    requisition = (
        await _fetch_one("job_requisitions", requisition_id, org_id)
        if requisition_id
        else None
    )
    org = await _fetch_org(org_id) or {}

    if not run and not candidate:
        raise CandidateNotResolvable(
            "There is no candidate or onboarding record to build this document from."
        )

    run = run or {}
    candidate = candidate or {}
    requisition = requisition or {}

    # `run` wins wherever it has a value: it is the snapshot taken when the
    # offer was made, and the ATS row is live.
    profile: dict[str, dict[str, Any]] = {
        "candidate": {
            "full_name": run.get("candidate_name") or candidate.get("full_name") or "",
            "email": run.get("candidate_email") or candidate.get("email") or "",
            "phone": run.get("candidate_phone") or candidate.get("phone") or "",
            "current_company": candidate.get("current_company") or "",
            "current_title": candidate.get("current_title") or "",
            "resume_url": candidate.get("resume_url") or "",
        },
        "offer": {
            "role_title": run.get("role_title") or candidate.get("current_title") or "",
            "designation": run.get("designation") or run.get("role_title") or "",
            "ctc_amount": run.get("ctc_amount"),
            "ctc_currency": run.get("ctc_currency") or "INR",
            "ctc_formatted": _format_amount(
                run.get("ctc_amount"), run.get("ctc_currency")
            ),
            "start_date": str(run["start_date"]) if run.get("start_date") else "",
            "work_location": run.get("work_location") or "",
            "probation_period_months": run.get("probation_period_months"),
        },
        "manager": {
            "full_name": run.get("reporting_manager_name") or "",
            "email": run.get("reporting_manager_email") or "",
        },
        "company": {
            "name": org.get("name") or "",
            "legal_name": org.get("legal_name") or org.get("name") or "",
            "registered_address": org.get("registered_address") or "",
            "jurisdiction": org.get("jurisdiction") or "",
        },
        "requisition": {
            "role_request": requisition.get("role_request") or "",
            "hiring_manager_email": requisition.get("hiring_manager_email") or "",
        },
        "meta": {
            "today": datetime.now(UTC).date().isoformat(),
        },
    }

    for path, value in (overrides or {}).items():
        namespace, _, field = path.partition(".")
        if field and namespace in profile:
            profile[namespace][field] = value

    return profile


__all__ = ["CandidateNotResolvable", "flatten", "resolve_profile"]
