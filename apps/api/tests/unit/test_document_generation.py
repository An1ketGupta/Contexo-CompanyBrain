"""Candidate resolution, mapping, and generation orchestration.

Three properties matter most here and each has a dedicated test:

  * The onboarding run's snapshot beats the live ATS row. A recruiter renaming
    someone in Greenhouse must not change the name on an offer letter that has
    already gone out.
  * Validation blocks. A failing report produces a row and no artifacts.
  * Regeneration never overwrites. A new row, a new generation_no, a new path.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.documents import audit
from app.services.documents.candidates import resolver as resolver_mod
from app.services.documents.candidates.resolver import (
    CandidateNotResolvable,
    flatten,
    resolve_profile,
)
from app.services.documents.generation import service as gen
from app.services.documents.mapping import engine as mapping_mod
from app.services.documents.mapping.engine import guess_source_path, resolve_values
from tests.unit.fake_supabase import FakeSupabase

ORG = "11111111-1111-1111-1111-111111111111"
RUN = "22222222-2222-2222-2222-222222222222"
CAND = "33333333-3333-3333-3333-333333333333"
REQ = "44444444-4444-4444-4444-444444444444"


def _var(name, **kw):
    base = {
        "internal_name": name,
        "display_name": name.replace("_", " ").title(),
        "data_type": "text",
        "is_required": True,
        "default_value": None,
        "validation_rules": {},
        "aliases": [],
    }
    base.update(kw)
    return base


# ── Candidate resolution ──────────────────────────────────────────────────


@pytest.fixture
def profile_db(monkeypatch):
    fake = FakeSupabase({
        "onboarding_runs": [{
            "id": RUN,
            "org_id": ORG,
            "candidate_id": CAND,
            "requisition_id": REQ,
            "candidate_name": "Rahul Verma",
            "candidate_email": "rahul@example.com",
            "candidate_phone": "+91 98765 43210",
            "role_title": "Software Engineer",
            "designation": "Senior Software Engineer",
            "ctc_amount": 1200000,
            "ctc_currency": "INR",
            "start_date": "2026-08-15",
            "work_location": "Bengaluru",
            "probation_period_months": 3,
            "reporting_manager_name": "John Smith",
            "reporting_manager_email": "john@example.com",
        }],
        "recruiting_candidates": [{
            "id": CAND,
            "org_id": ORG,
            "full_name": "R. Verma (ATS)",
            "email": "stale@ats.example.com",
            "current_company": "Acme Corp",
            "current_title": "Engineer II",
        }],
        "job_requisitions": [{
            "id": REQ,
            "org_id": ORG,
            "role_request": "Senior Backend Engineer",
            "hiring_manager_email": "hm@example.com",
        }],
        # No `org_id` column, exactly as in the real table — an organisation's
        # `id` IS the org. The fixture used to carry one, which let the resolver
        # ship a query the database answers with 42703 while these tests passed.
        "organizations": [{
            "id": ORG,
            "name": "Contexo",
            "legal_name": "Contexo Technologies Pvt. Ltd.",
            "registered_address": "WeWork, Bengaluru",
            "jurisdiction": "Karnataka, India",
        }],
    })
    monkeypatch.setattr(resolver_mod, "get_service_client", lambda: fake)
    return fake


@pytest.mark.asyncio
async def test_run_snapshot_beats_the_live_ats_row(profile_db):
    """The run was snapshotted when the offer was made. The ATS row is live and
    may have drifted — using it would let an edit rewrite history."""
    profile = await resolve_profile(org_id=ORG, onboarding_run_id=RUN)
    assert profile["candidate"]["full_name"] == "Rahul Verma"
    assert profile["candidate"]["email"] == "rahul@example.com"


@pytest.mark.asyncio
async def test_ats_fills_gaps_the_run_does_not_carry(profile_db):
    profile = await resolve_profile(org_id=ORG, onboarding_run_id=RUN)
    assert profile["candidate"]["current_company"] == "Acme Corp"


@pytest.mark.asyncio
async def test_run_supplies_requisition_and_candidate_ids(profile_db):
    """A caller should only have to know the run."""
    profile = await resolve_profile(org_id=ORG, onboarding_run_id=RUN)
    assert profile["requisition"]["role_request"] == "Senior Backend Engineer"


@pytest.mark.asyncio
async def test_company_details_come_from_the_org(profile_db):
    profile = await resolve_profile(org_id=ORG, onboarding_run_id=RUN)
    assert profile["company"]["legal_name"] == "Contexo Technologies Pvt. Ltd."
    assert profile["company"]["jurisdiction"] == "Karnataka, India"


@pytest.mark.asyncio
async def test_ctc_is_formatted_for_the_document(profile_db):
    profile = await resolve_profile(org_id=ORG, onboarding_run_id=RUN)
    assert profile["offer"]["ctc_formatted"] == "INR 1,200,000.00"


@pytest.mark.asyncio
async def test_hr_overrides_beat_every_system_of_record(profile_db):
    profile = await resolve_profile(
        org_id=ORG,
        onboarding_run_id=RUN,
        overrides={"manager.full_name": "Asha Rao"},
    )
    assert profile["manager"]["full_name"] == "Asha Rao"


@pytest.mark.asyncio
async def test_candidate_only_generation_works_without_a_run(profile_db):
    profile = await resolve_profile(org_id=ORG, candidate_id=CAND)
    assert profile["candidate"]["full_name"] == "R. Verma (ATS)"


@pytest.mark.asyncio
async def test_nothing_to_resolve_is_an_explicit_error(profile_db):
    with pytest.raises(CandidateNotResolvable):
        await resolve_profile(org_id=ORG)


@pytest.mark.asyncio
async def test_flatten_produces_dotted_paths(profile_db):
    flat = flatten(await resolve_profile(org_id=ORG, onboarding_run_id=RUN))
    assert flat["candidate.full_name"] == "Rahul Verma"
    assert flat["offer.start_date"] == "2026-08-15"
    assert flat["company.name"] == "Contexo"


# ── Mapping ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("candidate_name", "candidate.full_name"),
        ("employee_name", "candidate.full_name"),
        ("joining_date", "offer.start_date"),
        ("date_of_joining", "offer.start_date"),
        ("annual_salary", "offer.ctc_formatted"),
        ("reporting_manager", "manager.full_name"),
        ("company_legal_name", "company.legal_name"),
        ("office_address", "company.registered_address"),
    ],
)
def test_builtin_guesses(name, expected):
    assert guess_source_path(_var(name)) == expected


def test_longer_key_wins_on_substring_match():
    """`reporting_manager_email` must map to the email, not the name."""
    assert (
        guess_source_path(_var("candidate_reporting_manager_email"))
        == "manager.email"
    )


def test_aliases_are_consulted_before_substring_matching():
    variable = _var("field_seven", aliases=["Joining Date"])
    assert guess_source_path(variable) == "offer.start_date"


def test_type_is_the_last_resort():
    assert guess_source_path(_var("xyz", data_type="email")) == "candidate.email"


def test_unguessable_field_returns_none():
    """A wrong guess in a contract is worse than an empty field validation
    will flag."""
    assert guess_source_path(_var("notice_period")) is None


def test_signature_block_is_not_mapped_to_data():
    values, sources = resolve_values(
        variables=[_var("hr_sig", data_type="signature_block")],
        flat_profile={"candidate.full_name": "Rahul"},
    )
    assert values == {}
    assert sources["hr_sig"] is None


def test_configured_mapping_beats_the_guess():
    values, sources = resolve_values(
        variables=[_var("candidate_name")],
        flat_profile={
            "candidate.full_name": "Rahul",
            "candidate.current_title": "Engineer II",
        },
        configured={
            "candidate_name": {
                "source_path": "candidate.current_title",
                "transform": None,
                "scope": "org",
            }
        },
    )
    assert values["candidate_name"] == "Engineer II"
    assert sources["candidate_name"] == "candidate.current_title"


def test_transform_is_applied():
    values, _ = resolve_values(
        variables=[_var("candidate_name")],
        flat_profile={"candidate.full_name": "rahul verma"},
        configured={
            "candidate_name": {
                "source_path": "candidate.full_name",
                "transform": "title_case",
                "scope": "org",
            }
        },
    )
    assert values["candidate_name"] == "Rahul Verma"


def test_manual_value_beats_a_configured_mapping():
    """A human typing the value is the most authoritative source there is."""
    values, sources = resolve_values(
        variables=[_var("candidate_name")],
        flat_profile={"candidate.full_name": "Rahul"},
        configured={
            "candidate_name": {
                "source_path": "candidate.full_name",
                "transform": None,
                "scope": "org",
            }
        },
        manual={"candidate_name": "Rahul Iyer"},
    )
    assert values["candidate_name"] == "Rahul Iyer"
    assert sources["candidate_name"] == "manual"


def test_manual_value_fills_a_field_nothing_can_resolve():
    """The case the form exists for: no profile path supplies `notice_period`."""
    values, _ = resolve_values(
        variables=[_var("notice_period")],
        flat_profile={},
        manual={"notice_period": "60 days"},
    )
    assert values["notice_period"] == "60 days"


def test_blank_manual_value_falls_through_to_the_profile():
    values, sources = resolve_values(
        variables=[_var("candidate_name")],
        flat_profile={"candidate.full_name": "Rahul"},
        manual={"candidate_name": ""},
    )
    assert values["candidate_name"] == "Rahul"
    assert sources["candidate_name"] == "candidate.full_name"


def test_manual_value_for_an_undeclared_variable_is_ignored():
    """A key left over from an earlier version of the template must not be
    smuggled into the render context."""
    values, _ = resolve_values(
        variables=[_var("candidate_name")],
        flat_profile={"candidate.full_name": "Rahul"},
        manual={"retired_field": "junk"},
    )
    assert "retired_field" not in values


def test_manual_value_never_overwrites_a_signature_block():
    values, sources = resolve_values(
        variables=[_var("hr_sig", data_type="signature_block")],
        flat_profile={},
        manual={"hr_sig": "typed name"},
    )
    assert values == {}
    assert sources["hr_sig"] is None


def test_sources_explain_where_each_value_came_from():
    _values, sources = resolve_values(
        variables=[_var("joining_date", data_type="date")],
        flat_profile={"offer.start_date": "2026-08-15"},
    )
    assert sources["joining_date"] == "offer.start_date"


# ── Generation orchestration ──────────────────────────────────────────────


@pytest.fixture
def gen_env(monkeypatch, profile_db):
    """Wire the generation service to fakes for everything external."""
    db = FakeSupabase({
        "generated_documents": [],
        "generated_files": [],
        "document_audit_logs": [],
        "doc_variable_mappings": [],
    })
    monkeypatch.setattr(gen, "_svc", lambda: db)
    monkeypatch.setattr(audit, "get_service_client", lambda: db)
    monkeypatch.setattr(mapping_mod, "get_service_client", lambda: db)

    template = {
        "id": "tpl-1",
        "name": "Offer Letter",
        "document_type_id": "type-1",
        "current_version_id": "ver-1",
    }
    version = {"id": "ver-1", "file_sha256": "abc", "original_filename": "o.docx"}

    state: dict[str, Any] = {
        "variables": [
            _var("candidate_name"),
            _var("joining_date", data_type="date"),
        ],
        "slots": [{"variable": "candidate_name"}, {"variable": "joining_date"}],
        "uploads": [],
        "render": lambda: (b"DOCX", b"%PDF"),
    }

    async def _get_template(org_id, template_id):
        return template

    async def _get_version(org_id, version_id):
        return version

    async def _resolve_default(*, org_id, type_key):
        return template, version

    async def _load_bytes(*, org_id, version_id):
        return b"ORIGINAL", version

    async def _confirmed(*, org_id, version_id):
        return state["variables"], state["slots"]

    async def _upload(*, path, data, mime_type, overwrite=False):
        state["uploads"].append(path)
        return path

    async def _render(*, docx_bytes, slots, context, template_name=None):
        return state["render"]()

    monkeypatch.setattr(gen.templates, "get_template", _get_template)
    monkeypatch.setattr(gen.templates, "get_version", _get_version)
    monkeypatch.setattr(gen.templates, "resolve_default_version", _resolve_default)
    monkeypatch.setattr(gen.templates, "load_version_bytes", _load_bytes)
    monkeypatch.setattr(gen.schema, "confirmed_schema", _confirmed)
    monkeypatch.setattr(gen.storage, "upload", _upload)
    monkeypatch.setattr(gen, "render_to_pdf", _render)
    monkeypatch.setattr(gen, "_unfilled_warnings", lambda _b: [])

    return db, state


@pytest.mark.asyncio
async def test_happy_path_writes_a_row_and_two_files(gen_env):
    db, state = gen_env
    result = await gen.generate(org_id=ORG, type_key="offer_letter", onboarding_run_id=RUN)

    assert result.ok
    assert len(db.tables["generated_documents"]) == 1
    assert {f["format"] for f in db.tables["generated_files"]} == {"docx", "pdf"}
    assert len(state["uploads"]) == 2


@pytest.mark.asyncio
async def test_context_and_candidate_are_snapshotted(gen_env):
    db, _ = gen_env
    await gen.generate(org_id=ORG, type_key="offer_letter", onboarding_run_id=RUN)

    row = db.tables["generated_documents"][0]
    assert row["candidate_snapshot"]["candidate"]["full_name"] == "Rahul Verma"
    assert row["context_snapshot"]["values"]["candidate_name"] == "Rahul Verma"
    # The explanation of where each value came from is kept too.
    assert row["context_snapshot"]["sources"]["joining_date"] == "offer.start_date"


@pytest.mark.asyncio
async def test_validation_failure_blocks_generation_and_writes_no_files(gen_env):
    db, state = gen_env
    # A required field nothing can resolve.
    state["variables"].append(_var("notice_period"))
    state["slots"].append({"variable": "notice_period"})

    result = await gen.generate(org_id=ORG, type_key="offer_letter", onboarding_run_id=RUN)

    assert result.outcome == gen.OUTCOME_VALIDATION_FAILED
    assert db.tables["generated_files"] == []
    assert state["uploads"] == []
    row = db.tables["generated_documents"][0]
    assert row["status"] == "validation_failed"
    assert row["validation_report"]["ok"] is False


@pytest.mark.asyncio
async def test_validation_report_is_kept_for_audit(gen_env):
    db, state = gen_env
    state["variables"].append(_var("notice_period"))
    state["slots"].append({"variable": "notice_period"})

    await gen.generate(org_id=ORG, type_key="offer_letter", onboarding_run_id=RUN)

    report = db.tables["generated_documents"][0]["validation_report"]
    assert any(e["variable"] == "notice_period" for e in report["errors"])


@pytest.mark.asyncio
async def test_hr_entered_value_unblocks_a_failed_validation(gen_env):
    """What the blocked-run form buys: the same generation, minus the detour
    through whichever table the field would otherwise have to come from."""
    db, state = gen_env
    state["variables"].append(_var("notice_period"))
    state["slots"].append({"variable": "notice_period"})

    blocked = await gen.generate(org_id=ORG, type_key="offer_letter", onboarding_run_id=RUN)
    assert blocked.outcome == gen.OUTCOME_VALIDATION_FAILED

    result = await gen.generate(
        org_id=ORG,
        type_key="offer_letter",
        onboarding_run_id=RUN,
        variable_values={"notice_period": "60 days"},
    )

    assert result.ok
    assert result.context["notice_period"] == "60 days"
    row = db.tables["generated_documents"][-1]
    assert row["context_snapshot"]["sources"]["notice_period"] == "manual"


@pytest.mark.asyncio
async def test_regeneration_mints_a_new_row_not_an_overwrite(gen_env):
    db, _ = gen_env
    await gen.generate(org_id=ORG, type_key="offer_letter", onboarding_run_id=RUN)
    await gen.generate(org_id=ORG, type_key="offer_letter", onboarding_run_id=RUN)

    rows = db.tables["generated_documents"]
    assert len(rows) == 2
    assert sorted(r["generation_no"] for r in rows) == [1, 2]


@pytest.mark.asyncio
async def test_regeneration_writes_to_a_different_path(gen_env):
    _db, state = gen_env
    await gen.generate(org_id=ORG, type_key="offer_letter", onboarding_run_id=RUN)
    await gen.generate(org_id=ORG, type_key="offer_letter", onboarding_run_id=RUN)

    assert len(set(state["uploads"])) == len(state["uploads"]), "paths collided"


@pytest.mark.asyncio
async def test_pdf_failure_still_yields_the_docx(gen_env):
    db, state = gen_env
    state["render"] = lambda: (b"DOCX", None)

    result = await gen.generate(org_id=ORG, type_key="offer_letter", onboarding_run_id=RUN)

    assert result.ok
    assert {f["format"] for f in db.tables["generated_files"]} == {"docx"}
    assert any("PDF" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_template_drift_is_reported_not_raised(gen_env):
    db, state = gen_env

    def _drift():
        raise gen.SlotDriftError([3], template_name="Offer Letter")

    state["render"] = _drift

    result = await gen.generate(org_id=ORG, type_key="offer_letter", onboarding_run_id=RUN)

    assert result.outcome == gen.OUTCOME_DRIFT
    assert db.tables["generated_documents"][0]["status"] == "generation_failed"
    assert db.tables["generated_files"] == []


@pytest.mark.asyncio
async def test_no_confirmed_fields_is_reported_before_anything_else(gen_env):
    db, state = gen_env
    state["slots"] = []

    result = await gen.generate(org_id=ORG, type_key="offer_letter", onboarding_run_id=RUN)

    assert result.outcome == gen.OUTCOME_NO_FIELDS
    assert db.tables["generated_documents"] == []


@pytest.mark.asyncio
async def test_missing_default_template_is_reported(gen_env, monkeypatch):
    async def _none(*, org_id, type_key):
        return None

    monkeypatch.setattr(gen.templates, "resolve_default_version", _none)

    result = await gen.generate(org_id=ORG, type_key="nda", onboarding_run_id=RUN)
    assert result.outcome == gen.OUTCOME_MISSING_TEMPLATE
    assert "default template" in (result.error or "")


@pytest.mark.asyncio
async def test_generate_requires_a_template_or_a_type(gen_env):
    with pytest.raises(ValueError):
        await gen.generate(org_id=ORG, onboarding_run_id=RUN)


# ── Approval ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approve_stamps_who_and_when(gen_env):
    db, _ = gen_env
    result = await gen.generate(org_id=ORG, type_key="offer_letter", onboarding_run_id=RUN)

    await gen.approve(
        org_id=ORG, document_id=result.generated_document_id, actor_user_id="user-9"
    )
    row = db.tables["generated_documents"][0]
    assert row["status"] == "approved"
    assert row["approved_by"] == "user-9"


@pytest.mark.asyncio
async def test_reject_records_the_reason(gen_env):
    db, _ = gen_env
    result = await gen.generate(org_id=ORG, type_key="offer_letter", onboarding_run_id=RUN)

    await gen.reject(
        org_id=ORG,
        document_id=result.generated_document_id,
        actor_user_id="user-9",
        reason="Wrong salary band",
    )
    row = db.tables["generated_documents"][0]
    assert row["status"] == "rejected"
    assert row["rejection_reason"] == "Wrong salary band"


@pytest.mark.asyncio
async def test_approving_a_missing_document_raises(gen_env):
    with pytest.raises(LookupError):
        await gen.approve(org_id=ORG, document_id="nope", actor_user_id="user-9")


@pytest.mark.asyncio
async def test_generation_is_audited(gen_env):
    db, _ = gen_env
    await gen.generate(org_id=ORG, type_key="offer_letter", onboarding_run_id=RUN)

    actions = [r["action"] for r in db.tables["document_audit_logs"]]
    assert "generation.started" in actions
    assert "generation.completed" in actions
