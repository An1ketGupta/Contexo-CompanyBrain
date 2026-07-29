"""Tests for the analyzer's validation surface.

`_build_analysis` is where a language model's output is turned into rows that
end up in a legally-binding document, so it is the part worth testing hard. All
of it runs without an LLM: the tests feed it the payloads a model might return,
including bad ones.

The property that matters most is in `test_hallucinated_literal_is_dropped`. The
model proposes a substring; offsets are computed here by exact search. If the
text is not present verbatim, nothing is produced. That boundary is what makes
it safe to point a model at a contract.
"""
from __future__ import annotations

from app.services.documents.analysis.analyzer import (
    _build_analysis,
    _clamp_confidence,
    _slugify_name,
)
from app.services.documents.analysis.detector import (
    KIND_LABEL_COLON_VALUE,
    KIND_LLM_LITERAL,
    FillCandidate,
)
from app.services.documents.constants import ACTION_REPLACE_SPAN

PARAS = [
    "Dear Rahul,",
    "Joining Date: 15 August 2026",
    "Rahul will report to John Smith.",
]
KINDS = ["body", "body", "body"]
ALLOWED = ["offer_letter", "nda", "employment_agreement"]


def _candidate(**kw) -> FillCandidate:
    base = dict(
        candidate_id=0,
        paragraph_index=1,
        start_offset=14,
        end_offset=28,
        original_text="15 August 2026",
        context_before="Joining Date: ",
        context_after="",
        paragraph_kind="body",
        action=ACTION_REPLACE_SPAN,
        detection_kind=KIND_LABEL_COLON_VALUE,
        label_hint="Joining Date",
    )
    base.update(kw)
    return FillCandidate(**base)


def _run(payload, candidates=None):
    return _build_analysis(
        payload=payload,
        candidates=candidates if candidates is not None else [_candidate()],
        para_texts=PARAS,
        canonical_kinds=KINDS,
        allowed_type_keys=ALLOWED,
        truncated=False,
    )


# ── The anti-hallucination boundary ───────────────────────────────────────


def test_hallucinated_literal_is_dropped():
    """The model quotes text that is not in the document. Nothing must be
    produced from it — no slot, no guessed position."""
    result = _run({
        "variables": [{"internal_name": "candidate_name", "display_name": "Name"}],
        "literals": [
            {"paragraph": 0, "text": "Priya Sharma", "internal_name": "candidate_name"}
        ],
    })
    assert not [s for s in result.slots if s.detection_kind == KIND_LLM_LITERAL]
    assert result.rejected_literals == ["Priya Sharma"]


def test_reworded_literal_is_dropped():
    """A near-miss is still a miss. `Dear Rahul` (no comma) is not verbatim."""
    result = _run({
        "variables": [{"internal_name": "candidate_name", "display_name": "Name"}],
        "literals": [{"paragraph": 0, "text": "Dear Rahul!", "internal_name": "candidate_name"}],
    })
    assert result.rejected_literals == ["Dear Rahul!"]


def test_real_literal_is_located_at_true_offsets():
    result = _run({
        "variables": [{"internal_name": "candidate_name", "display_name": "Name"}],
        "literals": [{"paragraph": 0, "text": "Rahul", "internal_name": "candidate_name", "confidence": 0.99}],
    })
    literals = [s for s in result.slots if s.detection_kind == KIND_LLM_LITERAL]
    assert len(literals) == 1
    slot = literals[0]
    assert PARAS[0][slot.start_offset:slot.end_offset] == "Rahul"
    assert slot.internal_name == "candidate_name"
    assert slot.confidence == 0.99


def test_literal_out_of_paragraph_range_is_dropped():
    result = _run({
        "variables": [{"internal_name": "x_name", "display_name": "X"}],
        "literals": [{"paragraph": 99, "text": "Rahul", "internal_name": "x_name"}],
    })
    assert result.rejected_literals == ["Rahul"]


def test_literal_overlapping_an_existing_fill_point_is_skipped():
    """The date is already a located fill-point. A literal proposing the same
    characters must not create a second slot writing the same span."""
    result = _run({
        "variables": [{"internal_name": "joining_date", "display_name": "Joining Date"}],
        "assignments": [{"fill_point_id": 0, "internal_name": "joining_date"}],
        "literals": [{"paragraph": 1, "text": "15 August 2026", "internal_name": "joining_date"}],
    })
    spans = [(s.start_offset, s.end_offset) for s in result.slots if s.paragraph_index == 1]
    assert len(spans) == len(set(spans))
    assert result.rejected_literals == ["15 August 2026"]


def test_repeated_literal_picks_a_free_occurrence():
    """`Rahul` appears in paragraphs 0 and 2. Two proposals must land on
    different characters, not the same one twice."""
    result = _run({
        "variables": [{"internal_name": "candidate_name", "display_name": "Name"}],
        "literals": [
            {"paragraph": 0, "text": "Rahul", "internal_name": "candidate_name"},
            {"paragraph": 2, "text": "Rahul", "internal_name": "candidate_name"},
        ],
    })
    literals = [s for s in result.slots if s.detection_kind == KIND_LLM_LITERAL]
    assert len(literals) == 2
    assert {s.paragraph_index for s in literals} == {0, 2}


# ── Document type ─────────────────────────────────────────────────────────


def test_document_type_outside_allowed_keys_is_rejected():
    result = _run({"document_type": "termination_letter", "document_type_confidence": 0.9})
    assert result.document_type_key is None


def test_allowed_document_type_is_accepted():
    result = _run({"document_type": "offer_letter", "document_type_confidence": 0.97})
    assert result.document_type_key == "offer_letter"
    assert result.document_type_confidence == 0.97


# ── Name and value coercion ───────────────────────────────────────────────


def test_display_name_style_internal_name_is_slugified():
    assert _slugify_name("Candidate Name") == "candidate_name"
    assert _slugify_name("Employee-ID") == "employee_id"
    assert _slugify_name("  CTC (Annual)  ") == "ctc_annual"


def test_leading_digit_name_is_prefixed_not_rejected():
    assert _slugify_name("1st Manager") == "f_1st_manager"


def test_unusable_name_returns_none():
    assert _slugify_name("") is None
    assert _slugify_name("***") is None
    assert _slugify_name(None) is None
    assert _slugify_name(42) is None


def test_percent_confidence_is_normalised():
    assert _clamp_confidence(97) == 0.97
    assert _clamp_confidence("0.94") == 0.94


def test_out_of_range_and_nonsense_confidence():
    assert _clamp_confidence(-5) == 0.0
    assert _clamp_confidence(float("nan")) is None
    assert _clamp_confidence("high") is None
    assert _clamp_confidence(None) is None


def test_unknown_data_type_falls_back_to_text():
    result = _run({
        "variables": [
            {"internal_name": "start", "display_name": "Start", "data_type": "datetime"}
        ],
        "assignments": [{"fill_point_id": 0, "internal_name": "start"}],
    })
    assert result.variables[0].data_type == "text"


def test_known_data_type_is_preserved():
    result = _run({
        "variables": [
            {"internal_name": "start", "display_name": "Start", "data_type": "date"}
        ],
        "assignments": [{"fill_point_id": 0, "internal_name": "start"}],
    })
    assert result.variables[0].data_type == "date"


# ── Assignment integrity ──────────────────────────────────────────────────


def test_assignment_to_an_undefined_variable_leaves_the_slot_unmapped():
    """The model assigns a name it never defined. We must not invent the
    variable — the slot stays unmapped so HR picks a value."""
    result = _run({
        "variables": [],
        "assignments": [{"fill_point_id": 0, "internal_name": "mystery_field"}],
    })
    assert len(result.slots) == 1
    assert result.slots[0].internal_name is None


def test_assignment_to_an_unknown_fill_point_is_ignored():
    result = _run({
        "variables": [{"internal_name": "joining_date", "display_name": "D"}],
        "assignments": [{"fill_point_id": 99, "internal_name": "joining_date"}],
    })
    assert result.slots[0].internal_name is None


def test_every_located_fill_point_becomes_a_slot_even_when_unassigned():
    """A fill-point the model didn't understand is exactly the one that must
    not go invisible."""
    cands = [_candidate(candidate_id=i, paragraph_index=1, start_offset=i * 2, end_offset=i * 2 + 1)
             for i in range(4)]
    result = _build_analysis(
        payload={"variables": [], "assignments": []},
        candidates=cands,
        para_texts=PARAS,
        canonical_kinds=KINDS,
        allowed_type_keys=ALLOWED,
        truncated=False,
    )
    assert len(result.slots) == 4
    assert all(s.internal_name is None for s in result.slots)


def test_variables_nothing_points_at_are_pruned():
    result = _run({
        "variables": [
            {"internal_name": "joining_date", "display_name": "Joining Date"},
            {"internal_name": "unused_field", "display_name": "Unused"},
        ],
        "assignments": [{"fill_point_id": 0, "internal_name": "joining_date"}],
    })
    assert [v.internal_name for v in result.variables] == ["joining_date"]


def test_duplicate_variable_definitions_are_deduped():
    result = _run({
        "variables": [
            {"internal_name": "joining_date", "display_name": "First"},
            {"internal_name": "joining_date", "display_name": "Second"},
        ],
        "assignments": [{"fill_point_id": 0, "internal_name": "joining_date"}],
    })
    assert len(result.variables) == 1
    assert result.variables[0].display_name == "First"


def test_slots_are_sorted_in_document_order():
    result = _run({
        "variables": [{"internal_name": "candidate_name", "display_name": "Name"}],
        "literals": [
            {"paragraph": 2, "text": "John Smith", "internal_name": "candidate_name"},
            {"paragraph": 0, "text": "Rahul", "internal_name": "candidate_name"},
        ],
    })
    positions = [(s.paragraph_index, s.start_offset) for s in result.slots]
    assert positions == sorted(positions)


# ── Malformed payloads must not raise ─────────────────────────────────────


def test_garbage_payload_degrades_gracefully():
    result = _run({
        "document_type": 12,
        "variables": "not a list",
        "assignments": [None, {}, {"fill_point_id": "x"}],
        "literals": [None, {"text": ""}, {"paragraph": None, "text": "Rahul"}],
    })
    assert result.document_type_key is None
    assert result.variables == []
    assert len(result.slots) == 1  # the located candidate still survives


def test_empty_payload_still_yields_the_located_slots():
    result = _run({})
    assert len(result.slots) == 1
    assert result.slots[0].internal_name is None
