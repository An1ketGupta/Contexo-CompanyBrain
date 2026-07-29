"""Preview sample values.

The rule worth enforcing: preview data must be obviously fake. A preview that
looks like a real offer to a real person is a document somebody eventually
sends by mistake.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.services.documents.preview import (
    preview_signature_labels,
    sample_for,
    sample_values,
)
from app.services.documents.validation.engine import parse_date


def _var(name, **kw):
    base = {
        "internal_name": name,
        "display_name": name.replace("_", " ").title(),
        "data_type": "text",
        "example_value": None,
        "default_value": None,
    }
    base.update(kw)
    return base


def test_example_value_from_the_document_wins():
    assert sample_for(_var("candidate_name", example_value="Rahul")) == "Rahul"


def test_default_value_is_used_when_there_is_no_example():
    assert sample_for(_var("probation", default_value="3 months")) == "3 months"


@pytest.mark.parametrize(
    ("data_type", "expected_fragment"),
    [
        ("email", "@example.com"),
        ("phone", "+91"),
        ("currency", "INR"),
        ("city", "Bengaluru"),
        ("country", "India"),
    ],
)
def test_type_appropriate_placeholders(data_type, expected_fragment):
    assert expected_fragment in sample_for(_var("f", data_type=data_type))


def test_sample_date_is_readable_and_not_in_the_past():
    """A past sample date would trip a `not_past` rule during preview and
    confuse HR about whether their template is broken."""
    value = sample_for(_var("start_date", data_type="date"))
    parsed = parse_date(value)
    assert parsed is not None
    assert parsed >= date.today()


def test_unknown_type_falls_back_to_a_labelled_gap():
    assert sample_for(_var("weird_field", data_type="custom")) == "[Weird Field]"


def test_overrides_win_over_generated_samples():
    values = sample_values(
        [_var("candidate_name", example_value="Rahul")],
        overrides={"candidate_name": "Priya"},
    )
    assert values["candidate_name"] == "Priya"


def test_empty_override_falls_back_rather_than_blanking_the_field():
    values = sample_values(
        [_var("candidate_name", example_value="Rahul")], overrides={"candidate_name": ""}
    )
    assert values["candidate_name"] == "Rahul"


def test_signature_blocks_are_excluded_from_sample_values():
    values = sample_values([_var("hr_sig", data_type="signature_block"), _var("name")])
    assert set(values) == {"name"}


def test_signature_blocks_render_as_readable_labels_in_previews():
    labels = preview_signature_labels(
        [_var("hr_sig", data_type="signature_block", display_name="HR Signature")]
    )
    assert labels == {"hr_sig": "[ HR Signature ]"}


def test_every_data_variable_gets_a_value():
    variables = [_var("a"), _var("b", data_type="email"), _var("c", data_type="date")]
    assert set(sample_values(variables)) == {"a", "b", "c"}
