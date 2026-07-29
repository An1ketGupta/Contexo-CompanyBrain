"""Validation engine tests.

Two things here are load-bearing beyond ordinary coverage:

  * `test_signature_markers_match_the_esign_app` guards a constant duplicated
    across two independently-deployed apps. If they drift, signatures land in
    the wrong place on an executed contract.

  * `test_missing_required_field_blocks_generation` is the spec's central rule:
    a partially-completed legal document must never be produced.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.services.documents.constants import (
    SIGNATURE_MARKERS,
    signer_for_variable,
)
from app.services.documents.validation.engine import (
    CODE_DATE_IN_PAST,
    CODE_INVALID_CURRENCY,
    CODE_INVALID_DATE,
    CODE_INVALID_EMAIL,
    CODE_INVALID_PHONE,
    CODE_MISSING_REQUIRED,
    CODE_NOT_ALLOWED,
    CODE_OUT_OF_RANGE,
    CODE_PATTERN_MISMATCH,
    CODE_TOO_SHORT,
    CODE_UNKNOWN_VARIABLE,
    CODE_UNMAPPED_SLOT,
    CODE_UNUSED_VARIABLE,
    build_context,
    parse_amount,
    parse_date,
    validate,
)


def _var(name, **kw):
    base = {
        "internal_name": name,
        "display_name": name.replace("_", " ").title(),
        "data_type": "text",
        "is_required": True,
        "default_value": None,
        "validation_rules": {},
    }
    base.update(kw)
    return base


def _codes(issues):
    return [i.code for i in issues]


# ── The central rule ──────────────────────────────────────────────────────


def test_missing_required_field_blocks_generation():
    report = validate(
        variables=[_var("reporting_manager"), _var("candidate_name")],
        context={"candidate_name": "Rahul"},
    )
    assert not report.ok
    assert _codes(report.errors) == [CODE_MISSING_REQUIRED]
    assert report.errors[0].variable == "reporting_manager"


def test_missing_optional_field_warns_but_allows_generation():
    report = validate(
        variables=[_var("middle_name", is_required=False)],
        context={},
    )
    assert report.ok
    assert len(report.warnings) == 1


def test_default_value_satisfies_a_required_field():
    report = validate(
        variables=[_var("probation_period", default_value="3 months")],
        context={},
    )
    assert report.ok
    assert report.errors == []


def test_whitespace_only_value_counts_as_missing():
    report = validate(variables=[_var("candidate_name")], context={"candidate_name": "   "})
    assert not report.ok
    assert _codes(report.errors) == [CODE_MISSING_REQUIRED]


# ── Type checks ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["rahul@example.com", "a.b+c@sub.domain.co.in"])
def test_valid_emails_pass(value):
    report = validate(variables=[_var("email", data_type="email")], context={"email": value})
    assert report.ok


@pytest.mark.parametrize("value", ["rahul@", "not an email", "a@b"])
def test_invalid_emails_fail(value):
    report = validate(variables=[_var("email", data_type="email")], context={"email": value})
    assert _codes(report.errors) == [CODE_INVALID_EMAIL]


@pytest.mark.parametrize("value", ["+91 98765 43210", "9876543210", "(080) 4567-8900"])
def test_valid_phones_pass(value):
    report = validate(variables=[_var("phone", data_type="phone")], context={"phone": value})
    assert report.ok


def test_invalid_phone_fails():
    report = validate(variables=[_var("phone", data_type="phone")], context={"phone": "12345"})
    assert _codes(report.errors) == [CODE_INVALID_PHONE]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-08-15", date(2026, 8, 15)),
        ("15/08/2026", date(2026, 8, 15)),
        ("15 August 2026", date(2026, 8, 15)),
        ("Aug 15, 2026", date(2026, 8, 15)),
    ],
)
def test_date_formats_hr_actually_uses(value, expected):
    assert parse_date(value) == expected


def test_unreadable_date_fails():
    report = validate(
        variables=[_var("start_date", data_type="date")],
        context={"start_date": "sometime next month"},
    )
    assert _codes(report.errors) == [CODE_INVALID_DATE]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("₹12,00,000", 1200000.0),      # Indian lakh grouping
        ("INR 2,000,000.00", 2000000.0),
        ("Rs. 50000", 50000.0),
        ("$1,250.50", 1250.50),
        (1200000, 1200000.0),
    ],
)
def test_currency_parsing(value, expected):
    assert parse_amount(value) == expected


def test_invalid_currency_fails():
    report = validate(
        variables=[_var("ctc", data_type="currency")],
        context={"ctc": "competitive"},
    )
    assert _codes(report.errors) == [CODE_INVALID_CURRENCY]


def test_boolean_words_accepted():
    report = validate(
        variables=[_var("relocation", data_type="boolean")],
        context={"relocation": "yes"},
    )
    assert report.ok


# ── Rules ─────────────────────────────────────────────────────────────────


def test_future_joining_date_rule():
    """The spec calls this out explicitly: a joining date in the past is a
    data-entry error, not a document to send."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    report = validate(
        variables=[_var("start_date", data_type="date", validation_rules={"not_past": True})],
        context={"start_date": yesterday},
    )
    assert _codes(report.errors) == [CODE_DATE_IN_PAST]


def test_future_joining_date_rule_passes_for_a_future_date():
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    report = validate(
        variables=[_var("start_date", data_type="date", validation_rules={"not_past": True})],
        context={"start_date": tomorrow},
    )
    assert report.ok


def test_salary_minimum_rule():
    report = validate(
        variables=[_var("ctc", data_type="currency", validation_rules={"min": 100000})],
        context={"ctc": "₹5,000"},
    )
    assert _codes(report.errors) == [CODE_OUT_OF_RANGE]


def test_min_length_rule():
    report = validate(
        variables=[_var("candidate_name", validation_rules={"min_length": 3})],
        context={"candidate_name": "R"},
    )
    assert _codes(report.errors) == [CODE_TOO_SHORT]


def test_allowed_values_rule():
    report = validate(
        variables=[_var("work_mode", validation_rules={"allowed_values": ["Remote", "Hybrid", "Onsite"]})],
        context={"work_mode": "Lunar"},
    )
    assert _codes(report.errors) == [CODE_NOT_ALLOWED]


def test_pattern_rule():
    report = validate(
        variables=[_var("employee_id", validation_rules={"pattern": r"^EMP\d{4}$"})],
        context={"employee_id": "XX12"},
    )
    assert _codes(report.errors) == [CODE_PATTERN_MISMATCH]


def test_malformed_pattern_warns_and_does_not_block():
    """A broken regex is a template-configuration bug. It must be visible, but
    it must not stop HR sending an otherwise-valid offer."""
    report = validate(
        variables=[_var("employee_id", validation_rules={"pattern": "([unclosed"})],
        context={"employee_id": "EMP1234"},
    )
    assert report.ok
    assert _codes(report.warnings) == [CODE_PATTERN_MISMATCH]


def test_unknown_rule_keys_are_ignored():
    report = validate(
        variables=[_var("x", validation_rules={"some_future_rule": 42})],
        context={"x": "value"},
    )
    assert report.ok


# ── Slot coherence ────────────────────────────────────────────────────────


def test_unmapped_confirmed_slot_is_an_error():
    report = validate(
        variables=[_var("candidate_name")],
        context={"candidate_name": "Rahul"},
        slots=[{"variable": None}],
    )
    assert CODE_UNMAPPED_SLOT in _codes(report.errors)


def test_slot_referencing_an_unconfirmed_variable_is_an_error():
    report = validate(
        variables=[_var("candidate_name")],
        context={"candidate_name": "Rahul"},
        slots=[{"variable": "ghost_field"}],
    )
    assert CODE_UNKNOWN_VARIABLE in _codes(report.errors)


def test_variable_that_appears_nowhere_is_only_a_warning():
    report = validate(
        variables=[_var("candidate_name"), _var("unused")],
        context={"candidate_name": "Rahul", "unused": "x"},
        slots=[{"variable": "candidate_name"}],
    )
    assert report.ok
    assert CODE_UNUSED_VARIABLE in _codes(report.warnings)


# ── Signature blocks ──────────────────────────────────────────────────────


def test_signature_markers_match_the_esign_app():
    """These constants are duplicated across two independently-deployed apps.
    A drift puts signature fields in the wrong place on a signed contract."""
    source = (
        Path(__file__).resolve().parents[4]
        / "apps" / "esign" / "app" / "field_placement.py"
    )
    assert source.exists(), f"esign field_placement not found at {source}"
    text = source.read_text(encoding="utf-8")

    block = re.search(r"SIGNATURE_MARKERS.*?\{(.*?)\}", text, re.DOTALL)
    assert block, "SIGNATURE_MARKERS not found in the esign app"
    theirs = dict(re.findall(r'"([a-z]+)":\s*"([^"]+)"', block.group(1)))

    assert theirs == SIGNATURE_MARKERS


def test_signature_block_is_not_validated_as_missing_data():
    report = validate(
        variables=[_var("hr_signature", data_type="signature_block")],
        context={},
    )
    assert report.ok
    assert report.warnings == []


def test_build_context_emits_the_sentinel_for_a_signature_block():
    context = build_context(
        variables=[_var("candidate_signature", data_type="signature_block")],
        values={},
    )
    assert context["candidate_signature"] == SIGNATURE_MARKERS["candidate"]


def test_explicit_signer_wins_over_the_name_heuristic():
    variable = _var("signature_here", data_type="signature_block",
                    validation_rules={"signer": "hr"})
    assert signer_for_variable(variable) == "hr"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("hr_signature_block", "hr"),
        ("employer_signature", "hr"),
        ("authorised_signatory", "hr"),
        ("candidate_signature_block", "candidate"),
        ("signature", "candidate"),
    ],
)
def test_signer_inferred_from_the_variable_name(name, expected):
    assert signer_for_variable(_var(name, data_type="signature_block")) == expected


# ── Context building ──────────────────────────────────────────────────────


def test_build_context_applies_defaults():
    context = build_context(
        variables=[_var("probation", default_value="3 months"), _var("name")],
        values={"name": "Rahul"},
    )
    assert context == {"probation": "3 months", "name": "Rahul"}


def test_build_context_supplies_every_variable_so_render_never_keyerrors():
    context = build_context(
        variables=[_var("a"), _var("b", is_required=False)],
        values={},
    )
    assert set(context) == {"a", "b"}


def test_supplied_value_beats_the_default():
    context = build_context(
        variables=[_var("location", default_value="Bengaluru")],
        values={"location": "Mumbai"},
    )
    assert context["location"] == "Mumbai"


# ── Report shape ──────────────────────────────────────────────────────────


def test_report_serialises_for_jsonb_storage():
    report = validate(variables=[_var("missing_one")], context={})
    payload = report.to_dict()
    assert payload["ok"] is False
    assert payload["errors"][0]["code"] == CODE_MISSING_REQUIRED
    assert payload["errors"][0]["variable"] == "missing_one"
    assert payload["warnings"] == []


def test_clean_run_reports_ok():
    report = validate(
        variables=[_var("name"), _var("email", data_type="email")],
        context={"name": "Rahul", "email": "rahul@example.com"},
        slots=[{"variable": "name"}, {"variable": "email"}],
    )
    assert report.ok
    assert report.to_dict() == {"ok": True, "errors": [], "warnings": []}


def test_multiple_problems_are_all_reported_not_just_the_first():
    """HR should fix everything in one pass, not discover errors one at a time."""
    report = validate(
        variables=[
            _var("name"),
            _var("email", data_type="email"),
            _var("ctc", data_type="currency"),
        ],
        context={"email": "bad", "ctc": "lots"},
    )
    assert len(report.errors) == 3
