"""Sample values for previewing a template before any candidate exists.

HR needs to answer one question before a template goes live: *does my document
still look right once the fields are filled?* That question is about layout and
formatting, not data, so it must be answerable without picking a candidate.

Values are obviously fake by design. A preview that looks like a real offer to
a real person is a document someone will eventually send by mistake.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.documents.constants import (
    DATA_TYPE_ADDRESS,
    DATA_TYPE_BOOLEAN,
    DATA_TYPE_CITY,
    DATA_TYPE_COMPANY,
    DATA_TYPE_COUNTRY,
    DATA_TYPE_CURRENCY,
    DATA_TYPE_DATE,
    DATA_TYPE_DEPARTMENT,
    DATA_TYPE_DESIGNATION,
    DATA_TYPE_EMAIL,
    DATA_TYPE_MANAGER,
    DATA_TYPE_NUMBER,
    DATA_TYPE_PHONE,
    DATA_TYPE_STATE,
    NON_DATA_TYPES,
)

_BY_TYPE: dict[str, str] = {
    DATA_TYPE_EMAIL: "sample.candidate@example.com",
    DATA_TYPE_PHONE: "+91 98765 43210",
    DATA_TYPE_CURRENCY: "INR 12,00,000",
    DATA_TYPE_NUMBER: "3",
    DATA_TYPE_BOOLEAN: "Yes",
    DATA_TYPE_ADDRESS: "12 Sample Road, Example Park",
    DATA_TYPE_COUNTRY: "India",
    DATA_TYPE_STATE: "Karnataka",
    DATA_TYPE_CITY: "Bengaluru",
    DATA_TYPE_DESIGNATION: "Sample Designation",
    DATA_TYPE_DEPARTMENT: "Sample Department",
    DATA_TYPE_MANAGER: "Sample Manager",
    DATA_TYPE_COMPANY: "Sample Company Pvt. Ltd.",
}


def _sample_date() -> str:
    """A fixed offset from today, so a preview is stable within a day but never
    lands in the past and trips a `not_past` rule."""
    return (datetime.now(UTC).date() + timedelta(days=30)).isoformat()


def sample_for(variable: dict[str, Any]) -> Any:
    """One plausible value for one variable.

    Preference order: the value HR entered as an example, then the value the
    analyzer extracted from the document, then a type-appropriate placeholder,
    then the field's display name in brackets so an unrecognised type still
    reads as a labelled gap rather than an empty space.
    """
    for key in ("example_value", "default_value"):
        candidate = variable.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()

    data_type = variable.get("data_type") or "text"
    if data_type == DATA_TYPE_DATE:
        return _sample_date()
    if data_type in _BY_TYPE:
        return _BY_TYPE[data_type]

    label = variable.get("display_name") or variable.get("internal_name") or "value"
    return f"[{label}]"


def sample_values(
    variables: list[dict[str, Any]],
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a full set of preview values, honouring any caller overrides.

    Signature blocks are skipped: `build_context` substitutes the e-sign
    sentinel for those, and a preview should show HR where the signature will
    sit rather than a cryptic marker.
    """
    overrides = overrides or {}
    out: dict[str, Any] = {}
    for variable in variables:
        name = variable["internal_name"]
        if (variable.get("data_type") or "text") in NON_DATA_TYPES:
            continue
        if name in overrides and overrides[name] not in (None, ""):
            out[name] = overrides[name]
        else:
            out[name] = sample_for(variable)
    return out


def preview_signature_labels(variables: list[dict[str, Any]]) -> dict[str, str]:
    """Readable stand-ins for signature sentinels, for preview renders only.

    A real generation emits `◇SIGN:HR◇`, which the e-sign service finds and
    whites out before stamping. Nobody whites it out in a preview, so HR would
    see the raw marker — the position is what they are checking, not the token.
    """
    out: dict[str, str] = {}
    for variable in variables:
        if (variable.get("data_type") or "text") not in NON_DATA_TYPES:
            continue
        label = variable.get("display_name") or variable["internal_name"]
        out[variable["internal_name"]] = f"[ {label} ]"
    return out


__all__ = ["preview_signature_labels", "sample_for", "sample_values"]
