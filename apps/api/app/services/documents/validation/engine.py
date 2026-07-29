"""Pre-generation validation.

Answers one question: is there enough correct data to produce this document?

The contract is that a failing validation **blocks generation entirely**. The
spec is explicit about why, and it matches the failure this pipeline exists to
prevent: a partially-completed legal document is worse than no document. A
missing reporting manager should surface as "Reporting Manager is missing", not
as an offer letter that reaches a candidate with a blank where their manager's
name belongs.

The report is structured rather than a string, because three different consumers
need it: the builder UI renders errors beside the fields they belong to, the
generation service persists it to `generated_documents.validation_report` as
evidence of what was known at approval time, and the agent decides whether to
park a run or continue.

Rules live in `doc_template_variables.validation_rules` as JSONB. Unknown keys
are ignored by design — adding a rule is a change to this engine, never a
migration.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

from app.services.documents.constants import (
    NON_DATA_TYPES,
    SIGNATURE_MARKERS,
    signer_for_variable,
)

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"

# Issue codes. Stable identifiers so the UI can special-case presentation
# without matching on message text.
CODE_MISSING_REQUIRED = "missing_required"
CODE_EMPTY_OPTIONAL = "empty_optional"
CODE_INVALID_EMAIL = "invalid_email"
CODE_INVALID_PHONE = "invalid_phone"
CODE_INVALID_DATE = "invalid_date"
CODE_INVALID_NUMBER = "invalid_number"
CODE_INVALID_CURRENCY = "invalid_currency"
CODE_INVALID_BOOLEAN = "invalid_boolean"
CODE_TOO_SHORT = "too_short"
CODE_TOO_LONG = "too_long"
CODE_PATTERN_MISMATCH = "pattern_mismatch"
CODE_OUT_OF_RANGE = "out_of_range"
CODE_NOT_ALLOWED = "not_allowed_value"
CODE_DATE_IN_PAST = "date_in_past"
CODE_DATE_IN_FUTURE = "date_in_future"
CODE_UNMAPPED_SLOT = "unmapped_slot"
CODE_UNKNOWN_VARIABLE = "unknown_variable"
CODE_UNUSED_VARIABLE = "unused_variable"

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%m/%d/%Y",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B, %Y",
)

_TRUE_WORDS = {"true", "yes", "y", "1"}
_FALSE_WORDS = {"false", "no", "n", "0"}

# Characters stripped before parsing a currency amount. Covers the symbols the
# seeded document types realistically carry.
_CURRENCY_STRIP = "₹$€£,  "
_CURRENCY_PREFIXES = ("inr", "usd", "eur", "gbp", "rs.", "rs", "aed", "sgd", "us$")


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    message: str
    variable: str | None = None


@dataclass
class ValidationReport:
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when generation may proceed. Warnings never block."""
        return not self.errors

    def add(self, issue: ValidationIssue) -> None:
        if issue.severity == SEVERITY_ERROR:
            self.errors.append(issue)
        else:
            self.warnings.append(issue)

    def to_dict(self) -> dict[str, Any]:
        """JSONB-ready shape for `generated_documents.validation_report`."""
        return {
            "ok": self.ok,
            "errors": [asdict(i) for i in self.errors],
            "warnings": [asdict(i) for i in self.warnings],
        }


# ── Value coercion ─────────────────────────────────────────────────────────


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def parse_date(value: Any) -> date | None:
    """Parse a date from the formats HR data realistically arrives in."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(value: Any) -> float | None:
    """Parse a currency amount, tolerating symbols, codes, and grouping.

    Handles Indian lakh grouping ("12,00,000") for free, since grouping commas
    are simply removed rather than assumed to be in threes.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    for prefix in _CURRENCY_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    text = text.strip(_CURRENCY_STRIP)
    text = text.replace(",", "").replace(" ", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    try:
        return float(value.strip().replace(",", ""))
    except ValueError:
        return None


def parse_boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_WORDS:
            return True
        if text in _FALSE_WORDS:
            return False
    return None


def _digit_count(value: str) -> int:
    return sum(1 for ch in value if ch.isdigit())


# ── Per-type checks ────────────────────────────────────────────────────────


def _check_type(
    *, name: str, data_type: str, value: Any, report: ValidationReport
) -> Any:
    """Validate `value` against `data_type`. Returns the parsed value, or None
    when it could not be parsed (an issue has been recorded in that case)."""
    text = value.strip() if isinstance(value, str) else value

    if data_type == "email":
        if not isinstance(text, str) or not _EMAIL_RE.match(text):
            report.add(ValidationIssue(
                SEVERITY_ERROR, CODE_INVALID_EMAIL,
                f"'{value}' is not a valid email address.", name,
            ))
            return None
        return text

    if data_type == "phone":
        if not isinstance(text, str) or not (7 <= _digit_count(text) <= 15):
            report.add(ValidationIssue(
                SEVERITY_ERROR, CODE_INVALID_PHONE,
                f"'{value}' is not a valid phone number.", name,
            ))
            return None
        return text

    if data_type == "date":
        parsed = parse_date(text)
        if parsed is None:
            report.add(ValidationIssue(
                SEVERITY_ERROR, CODE_INVALID_DATE,
                f"'{value}' is not a date we can read.", name,
            ))
            return None
        return parsed

    if data_type == "currency":
        parsed = parse_amount(text)
        if parsed is None:
            report.add(ValidationIssue(
                SEVERITY_ERROR, CODE_INVALID_CURRENCY,
                f"'{value}' is not a valid amount.", name,
            ))
            return None
        return parsed

    if data_type == "number":
        parsed = parse_number(text)
        if parsed is None:
            report.add(ValidationIssue(
                SEVERITY_ERROR, CODE_INVALID_NUMBER,
                f"'{value}' is not a number.", name,
            ))
            return None
        return parsed

    if data_type == "boolean":
        parsed = parse_boolean(text)
        if parsed is None:
            report.add(ValidationIssue(
                SEVERITY_ERROR, CODE_INVALID_BOOLEAN,
                f"'{value}' is not a yes/no value.", name,
            ))
            return None
        return parsed

    return text


def _check_rules(
    *,
    name: str,
    data_type: str,
    raw: Any,
    parsed: Any,
    rules: dict[str, Any],
    report: ValidationReport,
) -> None:
    """Apply `validation_rules`. Unknown keys are ignored on purpose."""
    if not isinstance(rules, dict) or not rules:
        return

    text = raw.strip() if isinstance(raw, str) else str(raw)

    min_length = rules.get("min_length")
    if isinstance(min_length, int) and len(text) < min_length:
        report.add(ValidationIssue(
            SEVERITY_ERROR, CODE_TOO_SHORT,
            f"Must be at least {min_length} characters.", name,
        ))

    max_length = rules.get("max_length")
    if isinstance(max_length, int) and len(text) > max_length:
        report.add(ValidationIssue(
            SEVERITY_ERROR, CODE_TOO_LONG,
            f"Must be at most {max_length} characters.", name,
        ))

    pattern = rules.get("pattern")
    if isinstance(pattern, str) and pattern:
        try:
            if not re.search(pattern, text):
                report.add(ValidationIssue(
                    SEVERITY_ERROR, CODE_PATTERN_MISMATCH,
                    f"'{raw}' is not in the expected format.", name,
                ))
        except re.error:
            # A malformed pattern is a template-configuration bug, not a data
            # problem. Warn so it is visible, but never block a generation on it.
            report.add(ValidationIssue(
                SEVERITY_WARNING, CODE_PATTERN_MISMATCH,
                "This field's validation pattern is invalid and was skipped.", name,
            ))

    allowed = rules.get("allowed_values")
    if isinstance(allowed, list) and allowed and text not in [str(a) for a in allowed]:
        report.add(ValidationIssue(
            SEVERITY_ERROR, CODE_NOT_ALLOWED,
            f"'{raw}' is not one of the allowed values.", name,
        ))

    if isinstance(parsed, (int, float)) and not isinstance(parsed, bool):
        minimum, maximum = rules.get("min"), rules.get("max")
        if isinstance(minimum, (int, float)) and parsed < minimum:
            report.add(ValidationIssue(
                SEVERITY_ERROR, CODE_OUT_OF_RANGE,
                f"Must be at least {minimum}.", name,
            ))
        if isinstance(maximum, (int, float)) and parsed > maximum:
            report.add(ValidationIssue(
                SEVERITY_ERROR, CODE_OUT_OF_RANGE,
                f"Must be at most {maximum}.", name,
            ))

    if isinstance(parsed, date) and data_type == "date":
        today = date.today()
        if rules.get("not_past") and parsed < today:
            report.add(ValidationIssue(
                SEVERITY_ERROR, CODE_DATE_IN_PAST,
                f"{parsed.isoformat()} is in the past.", name,
            ))
        if rules.get("not_future") and parsed > today:
            report.add(ValidationIssue(
                SEVERITY_ERROR, CODE_DATE_IN_FUTURE,
                f"{parsed.isoformat()} is in the future.", name,
            ))


# ── Entry point ────────────────────────────────────────────────────────────


def validate(
    *,
    variables: list[dict[str, Any]],
    context: dict[str, Any],
    slots: list[dict[str, Any]] | None = None,
) -> ValidationReport:
    """Check that `context` can fill `variables`, and that `slots` are coherent.

    `variables` are the CONFIRMED `doc_template_variables` rows; `slots` the
    CONFIRMED `doc_template_slots` rows (each carrying `variable`, the resolved
    internal_name). Both filters are the caller's job.

    Returns a report. `report.ok` is False when generation must not proceed.
    """
    report = ValidationReport()
    by_name = {v["internal_name"]: v for v in variables}

    # ── Field-level checks ─────────────────────────────────────────────────
    for variable in variables:
        name = variable["internal_name"]
        data_type = variable.get("data_type") or "text"
        label = variable.get("display_name") or name

        # A signature block emits a sentinel marker for the e-sign service.
        # There is no upstream value to supply and a missing one is not an error.
        if data_type in NON_DATA_TYPES:
            continue

        value = context.get(name)
        if _is_blank(value):
            default = variable.get("default_value")
            if not _is_blank(default):
                continue  # the default fills it
            if variable.get("is_required", True):
                report.add(ValidationIssue(
                    SEVERITY_ERROR, CODE_MISSING_REQUIRED,
                    f"{label} is required but has no value.", name,
                ))
            else:
                report.add(ValidationIssue(
                    SEVERITY_WARNING, CODE_EMPTY_OPTIONAL,
                    f"{label} is empty and will appear blank in the document.", name,
                ))
            continue

        parsed = _check_type(
            name=name, data_type=data_type, value=value, report=report
        )
        if parsed is None:
            continue
        _check_rules(
            name=name,
            data_type=data_type,
            raw=value,
            parsed=parsed,
            rules=variable.get("validation_rules") or {},
            report=report,
        )

    # ── Slot-level checks ──────────────────────────────────────────────────
    used: set[str] = set()
    for slot in slots or []:
        variable_name = slot.get("variable")
        if not variable_name:
            report.add(ValidationIssue(
                SEVERITY_ERROR, CODE_UNMAPPED_SLOT,
                "A confirmed field in this template has no value assigned to it.",
            ))
            continue
        used.add(variable_name)
        if variable_name not in by_name:
            report.add(ValidationIssue(
                SEVERITY_ERROR, CODE_UNKNOWN_VARIABLE,
                f"This template fills '{variable_name}', which is not a "
                "confirmed field. Re-open the template and confirm it.",
                variable_name,
            ))

    if slots is not None:
        for name in by_name:
            if name not in used:
                report.add(ValidationIssue(
                    SEVERITY_WARNING, CODE_UNUSED_VARIABLE,
                    f"'{by_name[name].get('display_name') or name}' is defined "
                    "but does not appear anywhere in the document.",
                    name,
                ))

    return report


def build_context(
    *,
    variables: list[dict[str, Any]],
    values: dict[str, Any],
) -> dict[str, Any]:
    """Produce the render context: supplied values, with defaults filled in.

    Kept beside validation so that what gets validated and what gets rendered
    are derived from the same rules. A variable whose value is blank falls back
    to its `default_value`; one with neither resolves to an empty string, which
    only happens for optional fields since a required blank has already failed
    validation.
    """
    context: dict[str, Any] = {}
    for variable in variables:
        name = variable["internal_name"]

        # A signature block has no data source. It renders as the sentinel the
        # e-sign service searches for, so it must never be resolved from
        # `values` or blanked to "" — doing either would leave the signer's
        # field with nowhere to land on the finished PDF.
        if (variable.get("data_type") or "text") in NON_DATA_TYPES:
            context[name] = SIGNATURE_MARKERS[signer_for_variable(variable)]
            continue

        value = values.get(name)
        if _is_blank(value):
            default = variable.get("default_value")
            value = default if not _is_blank(default) else ""
        context[name] = value
    return context
