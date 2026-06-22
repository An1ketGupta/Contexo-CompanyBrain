"""V3 #50 — recommendations service unit tests.

Pure-logic surface (templates + fuzzy match). No DB, no network.
"""
from app.services.recommendations import (
    best_recommendation_match,
    mark_match,
    recommendations_for,
)

# ── template selection ────────────────────────────────────────────────────


def test_returns_use_case_specific_templates():
    recs = recommendations_for(primary_use_case="hr_policies")
    names = {r["name"] for r in recs}
    assert "Employee Handbook" in names
    assert "PTO & Leave Policy" in names
    # Use-case-specific items shouldn't bleed across (engineering ≠ HR).
    assert "Runbooks" not in names


def test_falls_back_to_general_on_unknown_use_case():
    recs = recommendations_for(primary_use_case="unknown_segment")
    names = {r["name"] for r in recs}
    assert "Company Overview" in names


def test_falls_back_to_general_on_none():
    recs = recommendations_for(primary_use_case=None)
    assert len(recs) > 0


def test_returned_entries_start_unmatched():
    recs = recommendations_for(primary_use_case="hr_policies")
    assert all(r["matched_document_id"] is None for r in recs)
    assert all(r["matched_at"] is None for r in recs)
    assert all(r["dismissed_at"] is None for r in recs)


# ── fuzzy matching ───────────────────────────────────────────────────────


def test_matches_named_file_to_template():
    recs = recommendations_for(primary_use_case="hr_policies")
    idx = best_recommendation_match("Employee Handbook v3 (final).pdf", recs)
    assert idx is not None
    assert recs[idx]["name"] == "Employee Handbook"


def test_extension_does_not_dilute_match():
    recs = recommendations_for(primary_use_case="engineering")
    idx = best_recommendation_match("Runbooks.docx", recs)
    assert idx is not None
    assert recs[idx]["name"] == "Runbooks"


def test_returns_none_when_below_threshold():
    recs = recommendations_for(primary_use_case="hr_policies")
    # Completely unrelated name — should not false-positive.
    idx = best_recommendation_match("annual_budget_q4.xlsx", recs)
    assert idx is None


def test_already_matched_recs_are_skipped():
    recs = recommendations_for(primary_use_case="hr_policies")
    # Pretend "Employee Handbook" is already matched.
    recs = mark_match(recs, 0, document_id="doc-existing")
    # A second upload with a clear match to it should NOT re-match.
    idx = best_recommendation_match("Employee Handbook v2.pdf", recs)
    if idx is not None:
        # If it picks something at all, it must not be the same already-matched item.
        assert recs[idx]["name"] != "Employee Handbook"


def test_dismissed_recs_are_skipped():
    recs = recommendations_for(primary_use_case="hr_policies")
    # Dismiss the handbook recommendation.
    recs[0]["dismissed_at"] = "2026-01-01T00:00:00Z"
    idx = best_recommendation_match("Employee Handbook.pdf", recs)
    if idx is not None:
        assert recs[idx]["name"] != recs[0]["name"]


def test_mark_match_does_not_mutate_original():
    recs = recommendations_for(primary_use_case="hr_policies")
    snapshot = [dict(r) for r in recs]
    updated = mark_match(recs, 0, document_id="doc-1")
    assert recs == snapshot  # original untouched
    assert updated[0]["matched_document_id"] == "doc-1"
    assert updated[0]["matched_at"] is not None
