"""The vocabularies in `services/documents/constants.py` are a hand-maintained
mirror of CHECK constraints in migration 099. That duplication is deliberate —
the analyzer and validation engine need them without a DB round-trip — but a
mirror that silently drifts is worse than no mirror at all: the Python side
would accept a value the database then rejects at INSERT time, deep inside a
generation the user is waiting on.

So the mirror is asserted here, by parsing the migration itself.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.documents import constants as c

MIGRATION = (
    Path(__file__).resolve().parents[4]
    / "supabase"
    / "migrations"
    / "099_document_generation_pipeline.sql"
)


@pytest.fixture(scope="module")
def sql() -> str:
    assert MIGRATION.exists(), f"migration not found at {MIGRATION}"
    return MIGRATION.read_text(encoding="utf-8")


def _check_values(sql: str, constraint_name: str) -> set[str]:
    """Pull the quoted values out of `ADD CONSTRAINT <name> CHECK (... IN (...))`."""
    match = re.search(
        rf"ADD CONSTRAINT {re.escape(constraint_name)}\s+CHECK\s*\((.*?)\n\s*\);",
        sql,
        re.DOTALL,
    )
    assert match, f"constraint {constraint_name} not found in migration"
    body = match.group(1)

    in_match = re.search(r"IN\s*\((.*?)\)", body, re.DOTALL)
    assert in_match, f"no IN (...) list inside {constraint_name}"
    return set(re.findall(r"'([^']+)'", in_match.group(1)))


@pytest.mark.parametrize(
    ("constraint", "python_values"),
    [
        ("doc_template_variables_type_check", c.DATA_TYPES),
        ("doc_template_variables_status_check", c.REVIEW_STATUSES),
        ("doc_template_variables_source_check", c.SOURCES),
        ("doc_template_slots_action_check", c.SLOT_ACTIONS),
        ("doc_template_slots_kind_check", c.PARAGRAPH_KINDS),
        ("doc_template_slots_status_check", c.REVIEW_STATUSES),
        ("doc_template_slots_source_check", c.SOURCES),
        ("doc_template_versions_analysis_check", c.ANALYSIS_STATUSES),
        ("doc_template_versions_mime_check", c.UPLOADABLE_MIMES),
        ("doc_templates_status_check", c.TEMPLATE_STATUSES),
        ("generated_documents_status_check", c.GENERATION_STATUSES),
        ("generated_files_format_check", c.GENERATED_FORMATS),
    ],
)
def test_python_vocabulary_matches_sql_check(
    sql: str, constraint: str, python_values: tuple[str, ...]
) -> None:
    assert _check_values(sql, constraint) == set(python_values)


def test_document_types_are_data_not_a_check(sql: str) -> None:
    """The whole point of the `document_types` table is that adding a type is an
    INSERT. A CHECK constraint on a type column would reintroduce exactly the
    rigidity that `documents.template_kind` had."""
    assert "document_types_key_check" not in sql
    assert not re.search(r"CHECK\s*\(\s*key\s+IN", sql)


def test_seeded_system_types_cover_the_supported_set(sql: str) -> None:
    seeded = set(re.findall(r"\(NULL,\s*'([a-z_]+)',", sql))
    required = {
        "offer_letter",
        "nda",
        "letter_of_intent",
        "employment_agreement",
        "internship_agreement",
        "consultant_agreement",
        "contractor_agreement",
        "confidentiality_agreement",
    }
    assert required <= seeded, f"missing seeded types: {sorted(required - seeded)}"


def test_insertion_actions_are_a_subset_of_actions() -> None:
    assert c.INSERTION_ACTIONS < set(c.SLOT_ACTIONS)


def test_non_data_types_are_real_data_types() -> None:
    assert c.NON_DATA_TYPES <= set(c.DATA_TYPES)


def test_failure_statuses_are_real_generation_statuses() -> None:
    assert c.GENERATION_FAILURE_STATUSES <= set(c.GENERATION_STATUSES)


def test_confirm_threshold_is_a_probability() -> None:
    assert 0.0 < c.DEFAULT_CONFIRM_THRESHOLD <= 1.0
