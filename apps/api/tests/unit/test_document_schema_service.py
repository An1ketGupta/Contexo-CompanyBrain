"""Persistence rules for template schemas.

The central rule under test: **re-running analysis never undoes a human
decision.** Re-analysis is how HR recovers from a bad first pass, so it has to
be safe to press. If pressing it could revert confirmations, nobody would.
"""
from __future__ import annotations

import pytest

from app.services.documents import audit
from app.services.documents import schema as schema_service
from app.services.documents.analysis.analyzer import AnalyzedSlot, AnalyzedVariable
from app.services.documents.constants import (
    ACTION_REPLACE_SPAN,
    SOURCE_AI,
    STATUS_CONFIRMED,
    STATUS_PROPOSED,
    STATUS_REJECTED,
)
from tests.unit.fake_supabase import FakeSupabase

ORG = "11111111-1111-1111-1111-111111111111"
VERSION = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def db(monkeypatch):
    fake = FakeSupabase({
        "doc_template_variables": [],
        "doc_template_slots": [],
        "document_audit_logs": [],
    })
    monkeypatch.setattr(schema_service, "_svc", lambda: fake)
    monkeypatch.setattr(audit, "get_service_client", lambda: fake)
    return fake


def _variable(name="joining_date", **kw):
    base = dict(
        internal_name=name,
        display_name=name.replace("_", " ").title(),
        description="",
        data_type="date",
        is_required=True,
        example_value="",
        confidence=0.9,
    )
    base.update(kw)
    return AnalyzedVariable(**base)


def _slot(name="joining_date", para=3, start=10, end=24, **kw):
    base = dict(
        paragraph_index=para,
        start_offset=start,
        end_offset=end,
        original_text="15 August 2026",
        context_before="Joining Date: ",
        context_after="",
        paragraph_kind="body",
        action=ACTION_REPLACE_SPAN,
        detection_kind="label_colon_value",
        internal_name=name,
        confidence=0.94,
    )
    base.update(kw)
    return AnalyzedSlot(**base)


async def _save(variables, slots, hashes=None):
    return await schema_service.save_analysis(
        org_id=ORG,
        version_id=VERSION,
        variables=variables,
        slots=slots,
        source_file_sha256="abc123",
        paragraph_hashes=hashes or {3: "hash3"},
    )


# ── First analysis ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_first_analysis_creates_variables_and_slots(db):
    counts = await _save([_variable()], [_slot()])
    assert counts["variables_created"] == 1
    assert counts["slots_created"] == 1

    variable = db.tables["doc_template_variables"][0]
    assert variable["internal_name"] == "joining_date"
    assert variable["status"] == STATUS_PROPOSED
    assert variable["source"] == SOURCE_AI


@pytest.mark.asyncio
async def test_nothing_is_auto_confirmed_however_confident(db):
    """A high-confidence guess is still a guess, and this is a contract."""
    await _save([_variable(confidence=0.99)], [_slot(confidence=0.99)])
    assert db.tables["doc_template_variables"][0]["status"] == STATUS_PROPOSED
    assert db.tables["doc_template_slots"][0]["status"] == STATUS_PROPOSED


@pytest.mark.asyncio
async def test_slot_is_linked_to_its_variable_by_id(db):
    await _save([_variable()], [_slot()])
    variable_id = db.tables["doc_template_variables"][0]["id"]
    assert db.tables["doc_template_slots"][0]["variable_id"] == variable_id


@pytest.mark.asyncio
async def test_unmapped_slot_is_persisted_with_no_variable(db):
    """A fill-point the model couldn't name must still be visible to HR."""
    await _save([], [_slot(name=None)])
    assert len(db.tables["doc_template_slots"]) == 1
    assert db.tables["doc_template_slots"][0]["variable_id"] is None


@pytest.mark.asyncio
async def test_one_variable_can_back_several_slots(db):
    """A name in the header and again in the signature block is one variable."""
    slots = [
        _slot(para=0, start=5, end=10),
        _slot(para=9, start=0, end=5),
    ]
    await _save([_variable(name="joining_date")], slots, hashes={0: "h0", 9: "h9"})

    assert len(db.tables["doc_template_variables"]) == 1
    assert len(db.tables["doc_template_slots"]) == 2
    variable_id = db.tables["doc_template_variables"][0]["id"]
    assert all(s["variable_id"] == variable_id for s in db.tables["doc_template_slots"])


# ── Re-analysis: the never-clobber rule ───────────────────────────────────


@pytest.mark.asyncio
async def test_reanalysis_does_not_revert_a_confirmed_variable(db):
    await _save([_variable()], [_slot()])
    variable = db.tables["doc_template_variables"][0]
    variable["status"] = STATUS_CONFIRMED
    variable["display_name"] = "Date of Joining"  # HR renamed it

    await _save([_variable(display_name="Joining Date")], [_slot()])

    refreshed = db.tables["doc_template_variables"][0]
    assert refreshed["status"] == STATUS_CONFIRMED
    assert refreshed["display_name"] == "Date of Joining", "HR's rename was lost"


@pytest.mark.asyncio
async def test_reanalysis_does_not_resurrect_a_rejected_variable(db):
    await _save([_variable()], [_slot()])
    db.tables["doc_template_variables"][0]["status"] = STATUS_REJECTED

    await _save([_variable()], [_slot()])

    assert db.tables["doc_template_variables"][0]["status"] == STATUS_REJECTED


@pytest.mark.asyncio
async def test_reanalysis_does_not_revert_a_confirmed_slot(db):
    await _save([_variable()], [_slot()])
    slot = db.tables["doc_template_slots"][0]
    slot["status"] = STATUS_CONFIRMED
    slot["variable_id"] = "a-different-variable-hr-picked"

    await _save([_variable()], [_slot()])

    assert db.tables["doc_template_slots"][0]["status"] == STATUS_CONFIRMED
    assert db.tables["doc_template_slots"][0]["variable_id"] == "a-different-variable-hr-picked"


@pytest.mark.asyncio
async def test_reanalysis_refreshes_a_still_proposed_variable(db):
    await _save([_variable(data_type="text", confidence=0.4)], [_slot()])

    await _save([_variable(data_type="date", confidence=0.97)], [_slot()])

    refreshed = db.tables["doc_template_variables"][0]
    assert refreshed["data_type"] == "date"
    assert refreshed["confidence"] == 0.97
    assert refreshed["status"] == STATUS_PROPOSED


@pytest.mark.asyncio
async def test_reanalysis_is_idempotent_and_does_not_duplicate(db):
    await _save([_variable()], [_slot()])
    await _save([_variable()], [_slot()])
    await _save([_variable()], [_slot()])

    assert len(db.tables["doc_template_variables"]) == 1
    assert len(db.tables["doc_template_slots"]) == 1


@pytest.mark.asyncio
async def test_reanalysis_adds_newly_detected_fields(db):
    await _save([_variable()], [_slot()])

    counts = await _save(
        [_variable(), _variable(name="annual_salary", data_type="currency")],
        [_slot(), _slot(name="annual_salary", para=5, start=0, end=12)],
        hashes={3: "hash3", 5: "hash5"},
    )

    assert counts["variables_created"] == 1
    assert counts["slots_created"] == 1
    assert len(db.tables["doc_template_variables"]) == 2


@pytest.mark.asyncio
async def test_a_slot_at_a_new_anchor_is_a_new_row(db):
    await _save([_variable()], [_slot(start=10, end=24)])
    await _save([_variable()], [_slot(start=30, end=44)], hashes={3: "hash3"})
    assert len(db.tables["doc_template_slots"]) == 2


# ── Drift metadata ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_slot_records_the_hashes_the_renderer_will_check(db):
    await _save([_variable()], [_slot()], hashes={3: "the-real-hash"})
    slot = db.tables["doc_template_slots"][0]
    assert slot["paragraph_hash"] == "the-real-hash"
    assert slot["source_file_sha256"] == "abc123"


# ── Reads ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_confirmed_schema_returns_only_confirmed_rows(db):
    await _save(
        [_variable(), _variable(name="annual_salary", data_type="currency")],
        [_slot(), _slot(name="annual_salary", para=5, start=0, end=12)],
        hashes={3: "h3", 5: "h5"},
    )
    # HR confirms one of each and leaves the other proposed.
    db.tables["doc_template_variables"][0]["status"] = STATUS_CONFIRMED
    db.tables["doc_template_slots"][0]["status"] = STATUS_CONFIRMED

    variables, slots = await schema_service.confirmed_schema(
        org_id=ORG, version_id=VERSION
    )
    assert [v["internal_name"] for v in variables] == ["joining_date"]
    assert len(slots) == 1


@pytest.mark.asyncio
async def test_list_slots_flattens_the_variable_name_for_the_renderer(db):
    await _save([_variable()], [_slot()])
    slots = await schema_service.list_slots(org_id=ORG, version_id=VERSION)
    assert slots[0]["variable"] == "joining_date"
    assert slots[0]["variable_type"] == "date"


@pytest.mark.asyncio
async def test_org_scoping_is_enforced_on_reads(db):
    await _save([_variable()], [_slot()])
    variables = await schema_service.list_variables(
        org_id="99999999-9999-9999-9999-999999999999", version_id=VERSION
    )
    assert variables == []


# ── Manual additions ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_manually_added_variable_is_confirmed_immediately(db):
    """HR typed it themselves; there is nothing left to review."""
    row = await schema_service.create_variable(
        org_id=ORG,
        version_id=VERSION,
        internal_name="employee_id",
        display_name="Employee ID",
        actor_user_id="user-1",
    )
    assert row["status"] == STATUS_CONFIRMED
    assert row["source"] == "manual"


@pytest.mark.asyncio
async def test_manual_variable_survives_a_later_reanalysis(db):
    await schema_service.create_variable(
        org_id=ORG,
        version_id=VERSION,
        internal_name="employee_id",
        display_name="Employee ID",
    )
    await _save([_variable()], [_slot()])

    names = {v["internal_name"] for v in db.tables["doc_template_variables"]}
    assert "employee_id" in names


@pytest.mark.asyncio
async def test_confirming_a_variable_stamps_who_and_when(db):
    await _save([_variable()], [_slot()])
    variable_id = db.tables["doc_template_variables"][0]["id"]

    await schema_service.update_variable(
        org_id=ORG,
        variable_id=variable_id,
        patch={"status": STATUS_CONFIRMED},
        actor_user_id="user-7",
    )
    row = db.tables["doc_template_variables"][0]
    assert row["status"] == STATUS_CONFIRMED
    assert row["confirmed_by"] == "user-7"


@pytest.mark.asyncio
async def test_confirming_a_variable_confirms_the_slots_it_fills(db):
    """The whole point of confirming a field.

    HR only ever confirms fields — the builder has no per-position control for a
    mapped slot. If the slots stayed `proposed`, `confirmed_schema` would return
    nothing and every generation would block on a template HR had just reviewed.
    """
    await _save([_variable()], [_slot(para=0, start=5, end=10), _slot(para=9, start=0, end=5)],
                hashes={0: "h0", 9: "h9"})
    variable_id = db.tables["doc_template_variables"][0]["id"]

    await schema_service.update_variable(
        org_id=ORG,
        variable_id=variable_id,
        patch={"status": STATUS_CONFIRMED},
        actor_user_id="user-7",
    )

    assert [s["status"] for s in db.tables["doc_template_slots"]] == [
        STATUS_CONFIRMED,
        STATUS_CONFIRMED,
    ]

    _variables, slots = await schema_service.confirmed_schema(
        org_id=ORG, version_id=VERSION
    )
    assert len(slots) == 2


@pytest.mark.asyncio
async def test_unconfirming_a_variable_stands_its_slots_down(db):
    await _save([_variable()], [_slot()])
    variable_id = db.tables["doc_template_variables"][0]["id"]

    await schema_service.update_variable(
        org_id=ORG, variable_id=variable_id, patch={"status": STATUS_CONFIRMED}
    )
    await schema_service.update_variable(
        org_id=ORG, variable_id=variable_id, patch={"status": STATUS_PROPOSED}
    )

    assert db.tables["doc_template_slots"][0]["status"] == STATUS_PROPOSED


@pytest.mark.asyncio
async def test_rejecting_a_variable_rejects_its_slots(db):
    """Rejecting a field means the sample text stays put — the slot must not
    remain confirmed and overwrite it."""
    await _save([_variable()], [_slot()])
    variable_id = db.tables["doc_template_variables"][0]["id"]

    await schema_service.update_variable(
        org_id=ORG, variable_id=variable_id, patch={"status": STATUS_REJECTED}
    )

    assert db.tables["doc_template_slots"][0]["status"] == STATUS_REJECTED


@pytest.mark.asyncio
async def test_editing_a_variable_without_a_status_leaves_slots_alone(db):
    await _save([_variable()], [_slot()])
    variable_id = db.tables["doc_template_variables"][0]["id"]
    await schema_service.update_variable(
        org_id=ORG, variable_id=variable_id, patch={"status": STATUS_CONFIRMED}
    )

    await schema_service.update_variable(
        org_id=ORG, variable_id=variable_id, patch={"data_type": "text"}
    )

    assert db.tables["doc_template_slots"][0]["status"] == STATUS_CONFIRMED


@pytest.mark.asyncio
async def test_a_slot_decision_never_writes_columns_the_table_lacks(db):
    """`doc_template_slots` has no confirmed_by / confirmed_at — writing them is
    a PostgREST 42703, which took out both "assign a field" and "place a field"."""
    await _save([_variable()], [_slot()])
    slot_id = db.tables["doc_template_slots"][0]["id"]
    variable_id = db.tables["doc_template_variables"][0]["id"]

    await schema_service.update_slot(
        org_id=ORG,
        slot_id=slot_id,
        patch={"variable_id": variable_id, "status": STATUS_CONFIRMED},
        actor_user_id="user-7",
    )
    created = await schema_service.create_slot(
        org_id=ORG,
        version_id=VERSION,
        variable_id=variable_id,
        paragraph_index=12,
        paragraph_kind="body",
        paragraph_hash="h12",
        source_file_sha256="abc123",
        start_offset=0,
        end_offset=0,
        action=ACTION_REPLACE_SPAN,
        actor_user_id="user-7",
    )

    for row in db.tables["doc_template_slots"]:
        assert "confirmed_by" not in row
        assert "confirmed_at" not in row
    assert created["status"] == STATUS_CONFIRMED
    assert "slot.confirmed" in [r["action"] for r in db.tables["document_audit_logs"]]


@pytest.mark.asyncio
async def test_internal_name_cannot_be_renamed_in_place(db):
    """Mappings key on the name, so an in-place rename would orphan them."""
    await _save([_variable()], [_slot()])
    variable_id = db.tables["doc_template_variables"][0]["id"]

    await schema_service.update_variable(
        org_id=ORG, variable_id=variable_id, patch={"internal_name": "something_else"}
    )
    assert db.tables["doc_template_variables"][0]["internal_name"] == "joining_date"


@pytest.mark.asyncio
async def test_deleting_a_variable_leaves_its_slots_visible(db):
    """ON DELETE SET NULL in the schema; the slot must not vanish silently."""
    await _save([_variable()], [_slot()])
    variable_id = db.tables["doc_template_variables"][0]["id"]

    await schema_service.delete_variable(org_id=ORG, variable_id=variable_id)

    assert db.tables["doc_template_variables"] == []
    assert len(db.tables["doc_template_slots"]) == 1


@pytest.mark.asyncio
async def test_deleting_a_confirmed_variable_stands_its_slots_down_first(db):
    """`doc_template_slots_confirmed_var_check` forbids a confirmed slot with no
    variable, so the delete would fail on the constraint otherwise."""
    await _save([_variable()], [_slot()])
    variable_id = db.tables["doc_template_variables"][0]["id"]
    await schema_service.update_variable(
        org_id=ORG, variable_id=variable_id, patch={"status": STATUS_CONFIRMED}
    )

    await schema_service.delete_variable(org_id=ORG, variable_id=variable_id)

    assert db.tables["doc_template_slots"][0]["status"] == STATUS_PROPOSED


# ── Audit ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_manual_variable_creation_is_audited(db):
    await schema_service.create_variable(
        org_id=ORG,
        version_id=VERSION,
        internal_name="employee_id",
        display_name="Employee ID",
        actor_user_id="user-1",
    )
    actions = [r["action"] for r in db.tables["document_audit_logs"]]
    assert "variable.created" in actions
