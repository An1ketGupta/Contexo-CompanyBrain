"""Variable → data-source binding.

A template says it needs `joining_date`. The candidate profile has
`offer.start_date`. This module decides that those are the same thing.

Four layers, most specific first:

  0. A value a human typed for this document (`manual`). Nothing outranks it.
  1. A configured `doc_variable_mappings` row — template_version, then
     document_type, then org. Explicit configuration always wins.
  2. The variable's own `aliases`, matched against the profile's dotted paths.
  3. A built-in guess from the variable's name and type.

Layer 3 is what makes a freshly-analysed template useful immediately, without
anyone configuring anything. Layers 1 and 2 are what make it *correctable* —
the guess is a starting point, never a commitment, which matters because the
analyzer now proposes open-vocabulary names rather than picking from a fixed
list. Layer 0 is the escape hatch for the field no system of record holds:
HR answers the validation error by typing the value, rather than by going to
populate a table somewhere so the mapping has something to find.

A variable nothing can resolve is not an error here. It resolves to nothing, and
the validation engine decides whether that is fatal — a required field with no
source blocks generation; an optional one does not.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from app.database import get_service_client
from app.services.documents.constants import (
    DATA_TYPE_COMPANY,
    DATA_TYPE_CURRENCY,
    DATA_TYPE_DATE,
    DATA_TYPE_DEPARTMENT,
    DATA_TYPE_DESIGNATION,
    DATA_TYPE_EMAIL,
    DATA_TYPE_MANAGER,
    DATA_TYPE_PHONE,
    NON_DATA_TYPES,
    SOURCE_MANUAL,
)

log = logging.getLogger(__name__)

# Built-in guesses, keyed by a normalised variable name. Ordered dict semantics
# do not matter: lookup is exact-first, then substring.
_BY_NAME: dict[str, str] = {
    # Candidate
    "candidate_name": "candidate.full_name",
    "employee_name": "candidate.full_name",
    "full_name": "candidate.full_name",
    "name": "candidate.full_name",
    "candidate_email": "candidate.email",
    "email": "candidate.email",
    "email_id": "candidate.email",
    "candidate_phone": "candidate.phone",
    "phone": "candidate.phone",
    "mobile": "candidate.phone",
    "contact_number": "candidate.phone",
    # Offer
    "role_title": "offer.role_title",
    "job_title": "offer.role_title",
    "position": "offer.role_title",
    "role": "offer.role_title",
    "designation": "offer.designation",
    "joining_date": "offer.start_date",
    "start_date": "offer.start_date",
    "date_of_joining": "offer.start_date",
    "effective_date": "offer.start_date",
    "ctc": "offer.ctc_formatted",
    "salary": "offer.ctc_formatted",
    "annual_salary": "offer.ctc_formatted",
    "gross_salary": "offer.ctc_formatted",
    "compensation": "offer.ctc_formatted",
    "remuneration": "offer.ctc_formatted",
    "ctc_amount": "offer.ctc_amount",
    "ctc_currency": "offer.ctc_currency",
    "work_location": "offer.work_location",
    "location": "offer.work_location",
    "office_location": "offer.work_location",
    "probation_period": "offer.probation_period_months",
    "probation_period_months": "offer.probation_period_months",
    # Manager
    "reporting_manager": "manager.full_name",
    "reporting_manager_name": "manager.full_name",
    "manager_name": "manager.full_name",
    "manager": "manager.full_name",
    "reports_to": "manager.full_name",
    "supervisor": "manager.full_name",
    "reporting_manager_email": "manager.email",
    "manager_email": "manager.email",
    # Company
    "company_name": "company.name",
    "company": "company.name",
    "employer": "company.name",
    "company_legal_name": "company.legal_name",
    "legal_name": "company.legal_name",
    "company_address": "company.registered_address",
    "registered_address": "company.registered_address",
    "office_address": "company.registered_address",
    "jurisdiction": "company.jurisdiction",
    "governing_law": "company.jurisdiction",
    # Meta
    "today_date": "meta.today",
    "today": "meta.today",
    "date": "meta.today",
    "issue_date": "meta.today",
    "offer_date": "meta.today",
}

# Weakest layer: when the name says nothing, the declared type still narrows it.
_BY_TYPE: dict[str, str] = {
    DATA_TYPE_EMAIL: "candidate.email",
    DATA_TYPE_PHONE: "candidate.phone",
    DATA_TYPE_DATE: "offer.start_date",
    DATA_TYPE_CURRENCY: "offer.ctc_formatted",
    DATA_TYPE_MANAGER: "manager.full_name",
    DATA_TYPE_COMPANY: "company.name",
    DATA_TYPE_DESIGNATION: "offer.designation",
    DATA_TYPE_DEPARTMENT: "offer.role_title",
}

_NORMALISE = re.compile(r"[^a-z0-9]+")


def _normalise(name: str) -> str:
    return _NORMALISE.sub("_", (name or "").strip().lower()).strip("_")


def guess_source_path(variable: dict[str, Any]) -> str | None:
    """Built-in guess for where a variable's value comes from.

    Exact name match, then the variable's aliases, then a substring match on the
    name, then the type. Returns None when nothing plausible fits — deliberately,
    because a wrong guess in a contract is worse than an empty field that
    validation will flag.
    """
    name = _normalise(variable.get("internal_name", ""))
    if not name:
        return None

    if name in _BY_NAME:
        return _BY_NAME[name]

    for alias in variable.get("aliases") or []:
        normalised = _normalise(alias)
        if normalised in _BY_NAME:
            return _BY_NAME[normalised]

    # Substring, longest key first so `reporting_manager_email` beats `manager`.
    for key in sorted(_BY_NAME, key=len, reverse=True):
        if len(key) >= 5 and key in name:
            return _BY_NAME[key]

    return _BY_TYPE.get(variable.get("data_type") or "")


async def load_configured_mappings(
    *,
    org_id: str,
    version_id: str,
    document_type_id: str | None,
) -> dict[str, dict[str, Any]]:
    """Configured mappings for one version, resolved most-specific-first.

    One query, then precedence applied in Python: three round-trips to answer
    "which of these three scopes has a row" would be three times the latency for
    a table that is small by construction.
    """
    def _fetch() -> list[dict[str, Any]]:
        res = (
            get_service_client().table("doc_variable_mappings")
            .select("*")
            .eq("org_id", org_id)
            .execute()
        )
        return res.data or []

    try:
        rows = await asyncio.to_thread(_fetch)
    except Exception as exc:  # noqa: BLE001
        log.warning("documents.mapping_load_failed org=%s err=%s", org_id, exc)
        return {}

    precedence = {"org": 0, "document_type": 1, "template_version": 2}
    best: dict[str, dict[str, Any]] = {}

    for row in rows:
        scope = row.get("scope")
        if scope == "template_version" and row.get("version_id") != version_id:
            continue
        if scope == "document_type" and row.get("document_type_id") != document_type_id:
            continue
        if scope not in precedence:
            continue

        name = row["internal_name"]
        current = best.get(name)
        if current is None or precedence[scope] > precedence[current["scope"]]:
            best[name] = row

    return best


def resolve_values(
    *,
    variables: list[dict[str, Any]],
    flat_profile: dict[str, Any],
    configured: dict[str, dict[str, Any]] | None = None,
    manual: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, str | None]]:
    """Produce `{internal_name: value}` plus the path each value came from.

    The second return value is the explanation. HR asking "why does this say
    Bengaluru?" should get "because it is mapped to offer.work_location", and
    the generation record stores it so the answer survives the conversation. A
    manually-entered value records `SOURCE_MANUAL` there instead of a path,
    which is the honest answer to the same question.

    `manual` is keyed by `internal_name` and only applies to variables this
    template actually declares — a stale key left over from an earlier version
    of the template is ignored rather than smuggled into the context.
    """
    configured = configured or {}
    manual = manual or {}
    values: dict[str, Any] = {}
    sources: dict[str, str | None] = {}

    for variable in variables:
        name = variable["internal_name"]

        # Signature blocks are placeholders for the e-sign service, not data.
        if (variable.get("data_type") or "text") in NON_DATA_TYPES:
            sources[name] = None
            continue

        entered = manual.get(name)
        if entered not in (None, ""):
            values[name] = entered
            sources[name] = SOURCE_MANUAL
            continue

        mapping = configured.get(name)
        path = mapping["source_path"] if mapping else guess_source_path(variable)
        sources[name] = path

        if not path:
            continue

        value = flat_profile.get(path)
        if value in (None, ""):
            continue

        transform = mapping.get("transform") if mapping else None
        values[name] = apply_transform(value, transform)

    return values, sources


def apply_transform(value: Any, transform: str | None) -> Any:
    """Apply a named transform.

    Named rather than expression-based on purpose: an eval-able expression in a
    pipeline that produces legally-binding documents is an injection surface,
    and there is no requirement that justifies one.
    """
    if not transform or not isinstance(value, str):
        return value
    match transform:
        case "upper":
            return value.upper()
        case "lower":
            return value.lower()
        case "title_case":
            return value.title()
        case "strip":
            return value.strip()
        case _:
            log.info("documents.unknown_transform transform=%s", transform)
            return value


__all__ = [
    "apply_transform",
    "guess_source_path",
    "load_configured_mappings",
    "resolve_values",
]
