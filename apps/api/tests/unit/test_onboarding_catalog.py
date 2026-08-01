"""The onboarding step catalog and its per-run snapshot.

Two halves that fail independently. The pure helpers — bundle grouping, finding
the next actionable step, mapping a legacy status onto the catalog — are the
logic the dispatcher will eventually be built on, so they are tested directly
against lists of dicts. The catalog reads and writes are tested against the
in-memory Supabase fake, because what matters there is the snapshot rule: a run
is pinned to the pipeline as it was configured when it started.
"""
from __future__ import annotations

import pytest

import app.inngest  # noqa: F401  (import-order cycle; see test_onboarding_step_toggles)
from app.services.agents.onboarding_v2 import catalog
from tests.unit.fake_supabase import FakeSupabase

ORG = "org-1"
RUN = "run-1"


def _steps(*specs: tuple[str, int, str | None, str]) -> list[dict[str, object]]:
    """Build run-step rows: (step_key, position, bundle_key, status)."""
    return [
        {
            "id": f"rs-{key}",
            "step_key": key,
            "position": position,
            "bundle_key": bundle,
            "status": status,
        }
        for key, position, bundle, status in specs
    ]


PIPELINE = _steps(
    ("loi", 0, None, catalog.STATUS_DONE),
    ("bgv", 10, None, catalog.STATUS_ACTIVE),
    ("appointment_letter", 20, "appointment_bundle", catalog.STATUS_PENDING),
    ("nda", 21, "appointment_bundle", catalog.STATUS_PENDING),
    ("policies", 30, None, catalog.STATUS_PENDING),
    ("induction", 40, None, catalog.STATUS_PENDING),
)


# ── Bundle grouping ────────────────────────────────────────────────────────


def test_bundle_members_group_together():
    step = next(s for s in PIPELINE if s["step_key"] == "appointment_letter")
    assert [s["step_key"] for s in catalog.bundle_siblings(PIPELINE, step)] == [
        "appointment_letter",
        "nda",
    ]


def test_bundle_is_ordered_by_position_regardless_of_which_member_asks():
    """Generation order has to be stable — the NDA must not lead the bundle
    just because the NDA row is what re-kicked the agent."""
    step = next(s for s in PIPELINE if s["step_key"] == "nda")
    assert [s["step_key"] for s in catalog.bundle_siblings(PIPELINE, step)] == [
        "appointment_letter",
        "nda",
    ]


def test_an_unbundled_step_is_a_bundle_of_one():
    """So callers never branch on None — the bundled and solo paths are the
    same code."""
    step = next(s for s in PIPELINE if s["step_key"] == "policies")
    assert catalog.bundle_siblings(PIPELINE, step) == [step]


# ── Next actionable ────────────────────────────────────────────────────────


def test_next_actionable_skips_done_and_skipped():
    steps = _steps(
        ("loi", 0, None, catalog.STATUS_DONE),
        ("bgv", 10, None, catalog.STATUS_SKIPPED),
        ("policies", 30, None, catalog.STATUS_PENDING),
    )
    assert catalog.next_actionable(steps)["step_key"] == "policies"


def test_next_actionable_reads_position_not_list_order():
    """Rows come back from PostgREST ordered, but a caller that built the list
    itself must not be able to change what runs next."""
    steps = _steps(
        ("induction", 40, None, catalog.STATUS_PENDING),
        ("loi", 0, None, catalog.STATUS_PENDING),
    )
    assert catalog.next_actionable(steps)["step_key"] == "loi"


def test_no_actionable_step_means_the_run_is_finished():
    steps = _steps(
        ("loi", 0, None, catalog.STATUS_DONE),
        ("bgv", 10, None, catalog.STATUS_SKIPPED),
    )
    assert catalog.next_actionable(steps) is None


# ── Legacy placement ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("loi_pending_hr_review", ("loi", catalog.STATUS_PENDING_HR_REVIEW)),
        ("bgv_pending", ("bgv", catalog.STATUS_ACTIVE)),
        (
            "appointment_pending_hr_review",
            ("appointment_letter", catalog.STATUS_PENDING_HR_REVIEW),
        ),
        ("completed", ("induction", catalog.STATUS_DONE)),
    ],
)
def test_legacy_statuses_map_onto_the_catalog(status, expected):
    assert (
        catalog.resolve_legacy_placement(status=status, blocked_template_kind=None)
        == expected
    )


def test_a_blocked_run_is_placed_by_the_template_that_stalled_it():
    """The ladder cannot say where a blocked run stopped — only
    blocked_template_kind can."""
    assert catalog.resolve_legacy_placement(
        status="blocked_missing_template", blocked_template_kind="induction"
    ) == ("induction", catalog.STATUS_BLOCKED_MISSING_TEMPLATE)


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        # Terminal: the status says nothing about how far the run got.
        ("cancelled", None),
        ("failed", None),
        # Blocked, but nothing recorded which template — refuse to guess.
        ("blocked_missing_template", None),
        ("blocked_missing_template", "  "),
    ],
)
def test_unplaceable_runs_are_left_alone(status, kind):
    assert catalog.resolve_legacy_placement(
        status=status, blocked_template_kind=kind
    ) is None


def test_placement_marks_earlier_steps_done_and_later_ones_pending():
    changes = catalog.apply_legacy_placement(
        PIPELINE, step_key="policies", step_status=catalog.STATUS_ACTIVE
    )
    assert changes["rs-bgv"] == catalog.STATUS_DONE
    assert changes["rs-appointment_letter"] == catalog.STATUS_DONE
    assert changes["rs-policies"] == catalog.STATUS_ACTIVE
    # Only what actually moved: `loi` is already done and `induction` is
    # already pending, so neither is worth a write.
    assert "rs-loi" not in changes
    assert "rs-induction" not in changes


def test_placement_rewinds_a_step_that_is_ahead_of_the_run():
    """A retry that re-enters an earlier step has to pull the later ones back,
    or the run shows work as complete that is about to be redone."""
    steps = _steps(
        ("loi", 0, None, catalog.STATUS_DONE),
        ("bgv", 10, None, catalog.STATUS_DONE),
        ("policies", 30, None, catalog.STATUS_ACTIVE),
    )
    changes = catalog.apply_legacy_placement(
        steps, step_key="bgv", step_status=catalog.STATUS_ACTIVE
    )
    assert changes["rs-bgv"] == catalog.STATUS_ACTIVE
    assert changes["rs-policies"] == catalog.STATUS_PENDING


def test_both_bundle_members_advance_when_the_ladder_names_only_one():
    """The legacy ladder has one status for the whole appointment bundle. Both
    documents are in HR's review queue, so both rows have to say so."""
    changes = catalog.apply_legacy_placement(
        PIPELINE,
        step_key="appointment_letter",
        step_status=catalog.STATUS_PENDING_HR_REVIEW,
    )
    assert changes["rs-appointment_letter"] == catalog.STATUS_PENDING_HR_REVIEW
    assert changes["rs-nda"] == catalog.STATUS_PENDING_HR_REVIEW


def test_a_skipped_step_stays_skipped_when_the_run_moves_past_it():
    """A step the org turned off must not be resurrected as `done` — the run
    genuinely did not do it."""
    steps = _steps(
        ("loi", 0, None, catalog.STATUS_DONE),
        ("bgv", 10, None, catalog.STATUS_SKIPPED),
        ("policies", 30, None, catalog.STATUS_PENDING),
    )
    changes = catalog.apply_legacy_placement(
        steps, step_key="policies", step_status=catalog.STATUS_ACTIVE
    )
    assert "rs-bgv" not in changes


def test_placement_on_an_unknown_step_changes_nothing():
    """An org that removed a step from its catalog still has runs quoting the
    old key. Better a no-op than a half-applied pipeline."""
    assert catalog.apply_legacy_placement(
        PIPELINE, step_key="does_not_exist", step_status=catalog.STATUS_ACTIVE
    ) == {}


# ── Catalog + snapshot against the fake ────────────────────────────────────


@pytest.fixture
def fake(monkeypatch):
    store = FakeSupabase({})
    monkeypatch.setattr(catalog, "get_service_client", lambda: store)
    return store


async def test_a_cold_org_is_seeded_on_first_read(fake):
    defs = await catalog.get_step_defs(ORG)
    assert [d["step_key"] for d in defs] == [
        "loi",
        "bgv",
        "appointment_letter",
        "nda",
        "policies",
        "induction",
    ]
    assert defs[0]["locked"] is True, "the LOI must not be removable"


async def test_seeding_is_idempotent(fake):
    await catalog.get_step_defs(ORG)
    await catalog.seed_default_step_defs(ORG)
    assert len(fake.tables["onboarding_step_defs"]) == len(catalog.DEFAULT_STEP_DEFS)


async def test_materialize_copies_the_catalog_onto_the_run(fake):
    steps = await catalog.materialize_run_steps(org_id=ORG, run_id=RUN)
    assert [s["step_key"] for s in steps] == [d["step_key"] for d in catalog.DEFAULT_STEP_DEFS]
    assert all(s["status"] == catalog.STATUS_PENDING for s in steps)
    appointment = next(s for s in steps if s["step_key"] == "appointment_letter")
    assert appointment["bundle_key"] == "appointment_bundle"
    assert appointment["document_type_key"] == "appointment_letter"


async def test_materialize_runs_once_per_run(fake):
    first = await catalog.materialize_run_steps(org_id=ORG, run_id=RUN)
    second = await catalog.materialize_run_steps(org_id=ORG, run_id=RUN)
    assert [s["id"] for s in first] == [s["id"] for s in second]
    assert len(fake.tables["onboarding_run_steps"]) == len(catalog.DEFAULT_STEP_DEFS)


async def test_a_disabled_step_is_recorded_as_skipped_not_dropped(fake):
    """The run should be able to show what it did not do — and a step
    re-enabled mid-run must not appear in a pipeline that is already past it."""
    await catalog.get_step_defs(ORG)
    for row in fake.tables["onboarding_step_defs"]:
        if row["step_key"] == "bgv":
            row["enabled"] = False

    steps = await catalog.materialize_run_steps(org_id=ORG, run_id=RUN)
    bgv = next(s for s in steps if s["step_key"] == "bgv")
    assert bgv["status"] == catalog.STATUS_SKIPPED
    assert len(steps) == len(catalog.DEFAULT_STEP_DEFS)


async def test_a_run_is_pinned_to_the_catalog_it_started_with(fake):
    """The whole reason the snapshot exists: editing the catalog must not
    reorder a run that is already underway."""
    await catalog.materialize_run_steps(org_id=ORG, run_id=RUN)
    for row in fake.tables["onboarding_step_defs"]:
        if row["step_key"] == "induction":
            row["enabled"] = False
            row["position"] = 5

    steps = await catalog.materialize_run_steps(org_id=ORG, run_id=RUN)
    induction = next(s for s in steps if s["step_key"] == "induction")
    assert induction["position"] == 40
    assert induction["status"] == catalog.STATUS_PENDING


async def test_a_collect_step_snapshots_its_checklist(fake):
    """Same rule for the checklist: the candidate answers the list as it stood
    when they were asked."""
    await catalog.get_step_defs(ORG)
    step_def = {
        "id": "sd-docs",
        "org_id": ORG,
        "step_key": "candidate_documents",
        "kind": catalog.KIND_COLLECT,
        "label": "Documents",
        "description": None,
        "document_type_key": None,
        "bundle_key": None,
        "bundle_label": None,
        "position": 5,
        "enabled": True,
        "signer_roles": [],
        "locked": False,
        "config": {},
    }
    fake.tables["onboarding_step_defs"].append(step_def)
    fake.tables["onboarding_collect_items"] = [
        {
            "id": "ci-1",
            "org_id": ORG,
            "step_def_id": "sd-docs",
            "item_key": "marksheet_10th",
            "label": "10th marksheet",
            "help_text": None,
            "required": True,
            "accepted_formats": ["pdf", "jpg"],
            "position": 0,
        },
        {
            "id": "ci-2",
            "org_id": ORG,
            "step_def_id": "sd-docs",
            "item_key": "pan_card",
            "label": "PAN card",
            "help_text": None,
            "required": False,
            "accepted_formats": ["pdf"],
            "position": 1,
        },
    ]

    steps = await catalog.materialize_run_steps(org_id=ORG, run_id=RUN)
    collect = next(s for s in steps if s["step_key"] == "candidate_documents")
    items = collect["config"]["items"]
    assert [i["item_key"] for i in items] == ["marksheet_10th", "pan_card"]
    assert items[0]["required"] is True
    assert items[1]["required"] is False


# ── Shadow sync ────────────────────────────────────────────────────────────


async def test_shadow_sync_tracks_the_legacy_ladder(fake):
    await catalog.sync_from_legacy_status(
        org_id=ORG, run_id=RUN, status="appointment_pending_hr_review"
    )
    steps = {s["step_key"]: s for s in await catalog.get_run_steps(RUN)}
    assert steps["loi"]["status"] == catalog.STATUS_DONE
    assert steps["bgv"]["status"] == catalog.STATUS_DONE
    assert steps["appointment_letter"]["status"] == catalog.STATUS_PENDING_HR_REVIEW
    assert steps["nda"]["status"] == catalog.STATUS_PENDING_HR_REVIEW
    assert steps["induction"]["status"] == catalog.STATUS_PENDING


async def test_shadow_sync_records_why_a_step_blocked(fake):
    await catalog.sync_from_legacy_status(
        org_id=ORG,
        run_id=RUN,
        status="blocked_missing_template",
        blocked_template_kind="induction",
        blocked_reason="No induction template is set as the default.",
    )
    steps = {s["step_key"]: s for s in await catalog.get_run_steps(RUN)}
    assert steps["induction"]["status"] == catalog.STATUS_BLOCKED_MISSING_TEMPLATE
    assert "induction template" in steps["induction"]["blocked_reason"]


async def test_shadow_sync_never_raises(monkeypatch):
    """It is bookkeeping running alongside a live hiring pipeline. A run must
    not fail because its shadow copy could not be written."""
    def _boom():
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(catalog, "get_service_client", _boom)
    await catalog.sync_from_legacy_status(
        org_id=ORG, run_id=RUN, status="loi_pending_hr_review"
    )


async def test_shadow_sync_ignores_terminal_runs(fake):
    """A cancelled run's status says nothing about how far it got, so writing
    a reconstructed history would be inventing one."""
    await catalog.sync_from_legacy_status(org_id=ORG, run_id=RUN, status="cancelled")
    assert fake.tables.get("onboarding_run_steps", []) == []


# ── Collection gates ───────────────────────────────────────────────────────


def _collect_step(position: int, *, status=catalog.STATUS_ACTIVE, required=("pan",)):
    return {
        "id": "rs-docs",
        "step_key": "candidate_documents",
        "kind": catalog.KIND_COLLECT,
        "position": position,
        "bundle_key": None,
        "status": status,
        "config": {
            "items": [
                {"item_key": key, "label": key, "required": key in required}
                for key in ("pan", "marksheet_10th")
            ]
        },
    }


def _submission(item_key: str, *, step_id="rs-docs"):
    return {"run_step_id": step_id, "item_key": item_key}


def test_only_required_items_gate_the_run():
    """An optional document the candidate skipped must not park a hire."""
    step = _collect_step(5, required=("pan",))
    assert catalog.is_collect_satisfied(step, [_submission("pan")]) is True


def test_a_missing_required_item_holds_the_run():
    step = _collect_step(5, required=("pan", "marksheet_10th"))
    assert catalog.is_collect_satisfied(step, [_submission("pan")]) is False


def test_submission_satisfies_the_gate_without_hr_approval():
    """The whole point of gating on submission: the pipeline must not stall
    behind a review queue nobody opened."""
    step = _collect_step(5, required=("pan",))
    submissions = [{**_submission("pan"), "review_status": "pending"}]
    assert catalog.is_collect_satisfied(step, submissions) is True


def test_a_step_the_run_has_not_reached_yet_does_not_gate():
    step = _collect_step(35)
    assert catalog.pending_collect_steps([step], [], reached_position=10) == []


def test_a_step_the_run_has_passed_gates_until_it_is_filed():
    step = _collect_step(5)
    assert [
        s["step_key"]
        for s in catalog.pending_collect_steps([step], [], reached_position=10)
    ] == ["candidate_documents"]


def test_a_finished_collect_step_stops_gating():
    step = _collect_step(5, status=catalog.STATUS_DONE)
    assert catalog.pending_collect_steps([step], [], reached_position=40) == []


def test_the_legacy_ladder_never_moves_a_collect_step():
    """The ladder knows nothing about collection, so walking past a collect
    step's position must not mark it done — only the candidate can."""
    steps = [
        *_steps(("loi", 0, None, catalog.STATUS_DONE)),
        _collect_step(5),
        *_steps(("policies", 30, None, catalog.STATUS_PENDING)),
    ]
    changes = catalog.apply_legacy_placement(
        steps, step_key="policies", step_status=catalog.STATUS_ACTIVE
    )
    assert "rs-docs" not in changes


# ── Catalog edits ──────────────────────────────────────────────────────────


def test_step_keys_are_slugified_and_deduplicated():
    assert catalog.slugify_step_key("10th Marksheet", taken=set()) == "s_10th_marksheet"
    assert catalog.slugify_step_key("PAN card", taken={"pan_card"}) == "pan_card_2"


async def test_a_collect_step_lands_where_it_was_asked_for(fake):
    await catalog.create_collect_step(
        org_id=ORG,
        label="Joining documents",
        after_step_key="loi",
        items=[{"label": "PAN card"}],
    )
    defs = await catalog.get_step_defs(ORG)
    assert [d["step_key"] for d in defs][:3] == [
        "loi",
        "joining_documents",
        "bgv",
    ]


async def test_an_unanchored_collect_step_never_displaces_the_loi(fake):
    """The pipeline requires the LOI to run first — everything downstream
    reads state it writes."""
    await catalog.create_collect_step(
        org_id=ORG,
        label="Joining documents",
        after_step_key=None,
        items=[{"label": "PAN card"}],
    )
    defs = await catalog.get_step_defs(ORG)
    assert defs[0]["step_key"] == "loi"
    assert defs[1]["step_key"] == "joining_documents"


async def test_the_loi_cannot_be_turned_off(fake):
    with pytest.raises(catalog.CatalogError, match="can't be turned off"):
        await catalog.update_step(org_id=ORG, step_key="loi", enabled=False)


async def test_a_built_in_step_cannot_be_deleted(fake):
    with pytest.raises(catalog.CatalogError, match="not removed"):
        await catalog.delete_step(org_id=ORG, step_key="bgv")


async def test_a_collect_step_can_be_deleted(fake):
    await catalog.create_collect_step(
        org_id=ORG, label="Extras", after_step_key="loi", items=[{"label": "PAN"}]
    )
    await catalog.delete_step(org_id=ORG, step_key="extras")
    assert all(d["step_key"] != "extras" for d in await catalog.get_step_defs(ORG))


async def test_bundle_members_are_enabled_as_one_unit(fake):
    """A bundle with half its documents turned off has no meaningful
    behaviour — HR reviews and sends it as one thing."""
    await catalog.update_step(org_id=ORG, step_key="appointment_letter", enabled=False)
    defs = {d["step_key"]: d for d in await catalog.get_step_defs(ORG)}
    assert defs["appointment_letter"]["enabled"] is False
    assert defs["nda"]["enabled"] is False


async def test_renaming_a_document_keeps_its_key(fake):
    """Keys address submissions and storage paths. A rename that minted a new
    key would orphan whatever the candidate already uploaded."""
    step = await catalog.create_collect_step(
        org_id=ORG, label="Docs", after_step_key="loi", items=[{"label": "PAN card"}]
    )
    before = (await catalog.get_collect_items(step["id"]))[0]
    after = await catalog.replace_collect_items(
        org_id=ORG,
        step_def_id=step["id"],
        items=[{"item_key": before["item_key"], "label": "PAN card (self-attested)"}],
    )
    assert after[0]["item_key"] == before["item_key"]
    assert after[0]["label"] == "PAN card (self-attested)"


async def test_disabling_a_step_writes_through_to_the_legacy_toggles(fake, monkeypatch):
    """Until the step engine drives dispatch, the agent still reads the four
    booleans. Editing the catalog has to move both or HR sees a change the
    pipeline does not make."""
    captured: dict[str, object] = {}

    async def _fake_update(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(catalog.org_config, "update_onboarding_steps", _fake_update)
    await catalog.update_step(org_id=ORG, step_key="bgv", enabled=False)
    assert captured["bgv"] is False
    assert captured["policies"] is True
