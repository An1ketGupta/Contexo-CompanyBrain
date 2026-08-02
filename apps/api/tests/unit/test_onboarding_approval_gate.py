"""The HR approval gate: the run stops after the candidate acts.

Before this, every candidate action was taken on trust. They signed the
appointment letter and it was delivered; they uploaded ten documents and the
run stepped past the moment the last file landed; they named referees and
verification emails went out unread. HR's opinion was recorded afterwards and
gated nothing, which is fine for "this scan is a bit dark" and useless for "the
signature is in the wrong box".

Two halves, tested the way the rest of this pipeline is. The predicates that
decide when the gate applies are pure and tested against dicts; the dispatcher
behaviour — which state a step lands in, and whether it re-announces itself —
is tested through the agent with its side effects recorded.
"""
from __future__ import annotations

import pytest

import app.inngest  # noqa: F401  (import-order cycle; see test_onboarding_step_toggles)
from app.services.agents.onboarding_v2 import catalog
from app.services.agents.onboarding_v2.agent import OnboardingV2Agent

ORG = "org-1"
RUN = "run-1"


def _step(step_key: str, **extra) -> dict[str, object]:
    """A run step with the gate on, which is the default every org is seeded."""
    return {
        "id": f"rs-{step_key}",
        "run_id": RUN,
        "org_id": ORG,
        "step_key": step_key,
        "kind": extra.pop("kind", catalog.KIND_COLLECT),
        "label": step_key.replace("_", " ").title(),
        "document_type_key": None,
        "bundle_key": extra.pop("bundle_key", None),
        "bundle_label": None,
        "position": extra.pop("position", 0),
        "signer_roles": extra.pop("signer_roles", []),
        "system_action": extra.pop("system_action", None),
        "config": extra.pop("config", {}),
        "status": extra.pop("status", catalog.STATUS_ACTIVE),
        "requires_hr_approval": extra.pop("requires_hr_approval", True),
        "review_decision": extra.pop("review_decision", None),
        "approval_round": extra.pop("approval_round", 0),
        **extra,
    }


# ── When the gate applies ──────────────────────────────────────────────────


def test_a_collect_step_always_gates():
    """The whole step is the candidate uploading files, so there is always
    something of theirs to look at."""
    assert catalog.gate_applies(_step("joining_documents")) is True


def test_a_document_the_candidate_signs_gates():
    assert (
        catalog.gate_applies(
            _step("loi", kind=catalog.KIND_GENERATE, signer_roles=["hr", "candidate"])
        )
        is True
    )


def test_a_document_only_hr_signs_does_not_gate():
    """There is nothing of the candidate's in it. HR reviewing their own
    signature is the draft review they already did."""
    assert (
        catalog.gate_applies(
            _step("internal_memo", kind=catalog.KIND_GENERATE, signer_roles=["hr"])
        )
        is False
    )


def test_a_document_nobody_signs_does_not_gate():
    assert (
        catalog.gate_applies(
            _step("induction", kind=catalog.KIND_GENERATE, signer_roles=[])
        )
        is False
    )


def test_background_verification_gates_but_policy_sign_off_does_not():
    """The referee list is the candidate's work and worth checking before the
    emails go out. A policy acknowledgement is a tick on a record HR wrote —
    gating it would park every hire on a checkbox."""
    assert (
        catalog.gate_applies(
            _step(
                "bgv",
                kind=catalog.KIND_SYSTEM,
                system_action=catalog.SYSTEM_ACTION_BGV,
            )
        )
        is True
    )
    assert (
        catalog.gate_applies(
            _step(
                "policies",
                kind=catalog.KIND_SYSTEM,
                system_action=catalog.SYSTEM_ACTION_POLICIES,
            )
        )
        is False
    )


def test_the_org_toggle_turns_the_gate_off_entirely():
    assert (
        catalog.gate_applies(_step("docs", requires_hr_approval=False)) is False
    )


def test_a_run_that_predates_the_gate_is_not_retrofitted():
    """Migration 113 backfills `requires_hr_approval = FALSE` on every run step
    that already existed. A candidate who signed last week must not have their
    finished step reopened into a review queue."""
    legacy = _step("docs")
    del legacy["requires_hr_approval"]
    assert catalog.gate_applies(legacy) is False


# ── When the gate is cleared ───────────────────────────────────────────────


def test_an_unreviewed_step_holds():
    assert catalog.gate_cleared(_step("docs")) is False


def test_approval_clears_it():
    assert (
        catalog.gate_cleared(_step("docs", review_decision=catalog.REVIEW_APPROVED))
        is True
    )


def test_rejection_does_not_clear_it():
    """Sending something back is not a decision that lets the run past — it is
    the opposite one."""
    assert (
        catalog.gate_cleared(_step("docs", review_decision=catalog.REVIEW_REJECTED))
        is False
    )


def test_a_step_with_no_gate_is_always_cleared():
    assert catalog.gate_cleared(_step("docs", requires_hr_approval=False)) is True


# ── A rejected file stops counting as filed ────────────────────────────────


def _checklist(*item_keys: str) -> dict[str, object]:
    return _step(
        "docs",
        config={
            "items": [
                {"item_key": k, "label": k, "required": True} for k in item_keys
            ]
        },
    )


def test_a_rejected_submission_reopens_the_ask():
    """The row stays — the candidate's history is not rewritten — but it no
    longer satisfies the item, which is what re-asks for it."""
    step = _checklist("pan", "aadhaar")
    submissions = [
        {"run_step_id": "rs-docs", "item_key": "pan", "review_status": "approved"},
        {"run_step_id": "rs-docs", "item_key": "aadhaar", "review_status": "rejected"},
    ]
    assert catalog.is_collect_satisfied(step, submissions) is False


def test_an_unreviewed_submission_still_counts():
    """The checklist being answered and HR having accepted it are separate
    questions; this one is only the first."""
    step = _checklist("pan")
    submissions = [
        {"run_step_id": "rs-docs", "item_key": "pan", "review_status": "pending"}
    ]
    assert catalog.is_collect_satisfied(step, submissions) is True


def test_a_replacement_satisfies_the_item_again():
    """Re-uploading overwrites the row and resets its review status, so the
    same item that was outstanding a moment ago is filed."""
    step = _checklist("pan")
    assert (
        catalog.is_collect_satisfied(
            step,
            [{"run_step_id": "rs-docs", "item_key": "pan", "review_status": "pending"}],
        )
        is True
    )


# ── What the run status says while HR is looking ───────────────────────────


def test_a_collect_step_in_the_gate_stops_blaming_the_candidate():
    """`awaiting_candidate_documents` on a step whose documents are all in and
    sitting with HR is the wrong thing for a list view to say."""
    assert (
        catalog.run_status_for(
            _step("docs"), catalog.STATUS_PENDING_HR_APPROVAL
        )
        == "step_pending_hr_approval"
    )


def test_a_built_in_step_in_the_gate_gets_the_generic_label():
    """The legacy ladder has no value for a state it never had. Reusing
    `loi_pending_hr_review` would tell HR to review a draft that was approved
    days ago."""
    assert (
        catalog.run_status_for(
            _step("loi", kind=catalog.KIND_GENERATE), catalog.STATUS_PENDING_HR_APPROVAL
        )
        == "step_pending_hr_approval"
    )


# ── The dispatcher ─────────────────────────────────────────────────────────


@pytest.fixture
def agent(monkeypatch):
    """An agent whose side effects are recorded instead of performed."""
    a = OnboardingV2Agent(org_id=ORG, run_id=RUN)
    a.steps: list[dict] = []
    a.submissions: list[dict] = []
    a.notified: list[str] = []
    a.delivered: list[str] = []

    async def fake_set_status(status, *, extra=None):
        return None

    async def fake_load_run():
        return {
            "id": RUN,
            "status": "step_active",
            "candidate_name": "Priya Sharma",
            "candidate_email": "priya@example.com",
            "references_form_token": "tok",
            "references_submitted_at": "2026-08-01T00:00:00Z",
        }

    async def fake_set_step_status(run_step_id, status, *, blocked_reason=None, extra=None):
        for s in a.steps:
            if s["id"] == run_step_id:
                s["status"] = status
                s.update(extra or {})

    async def fake_get_submissions(run_id):
        return a.submissions

    async def fake_notify(step, *, detail):
        a.notified.append(step["step_key"])

    async def fake_deliver(bundle):
        a.delivered.append(bundle[0]["step_key"])
        return {"delivered": True}

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(a, "_set_status", fake_set_status)
    monkeypatch.setattr(a, "_load_run", fake_load_run)
    monkeypatch.setattr(a, "_refresh_run", fake_load_run)
    monkeypatch.setattr(a, "log_step", noop)
    monkeypatch.setattr(a, "_notify_hr_approval_needed", fake_notify)
    monkeypatch.setattr(a, "_notify_candidate_documents_due", noop)
    monkeypatch.setattr(a, "_deliver_bundle", fake_deliver)
    monkeypatch.setattr(catalog, "set_step_status", fake_set_step_status)
    monkeypatch.setattr(catalog, "get_submissions", fake_get_submissions)
    monkeypatch.setattr(
        "app.services.agents.onboarding_v2.agent.ob_storage.log_onboarding_event", noop
    )
    return a


@pytest.mark.asyncio
async def test_a_finished_checklist_parks_for_hr_rather_than_completing(agent):
    step = _checklist("pan")
    agent.steps = [step]
    agent.submissions = [
        {"run_step_id": "rs-docs", "item_key": "pan", "review_status": "pending"}
    ]

    result = await agent._advance_collect(step)

    assert step["status"] == catalog.STATUS_PENDING_HR_APPROVAL
    assert result["waiting_for"] == "hr_to_approve_candidate_work"
    assert agent.notified == ["docs"]


@pytest.mark.asyncio
async def test_the_same_checklist_completes_when_the_org_turned_the_gate_off(agent):
    step = _checklist("pan")
    step["requires_hr_approval"] = False
    agent.steps = [step]
    agent.submissions = [
        {"run_step_id": "rs-docs", "item_key": "pan", "review_status": "pending"}
    ]

    result = await agent._advance_collect(step)

    assert step["status"] == catalog.STATUS_DONE
    assert result == {"_continue": True}
    assert agent.notified == []


@pytest.mark.asyncio
async def test_hr_approving_lets_the_checklist_finish(agent):
    step = _checklist("pan")
    step["review_decision"] = catalog.REVIEW_APPROVED
    agent.steps = [step]
    agent.submissions = [
        {"run_step_id": "rs-docs", "item_key": "pan", "review_status": "approved"}
    ]

    assert await agent._advance_collect(step) == {"_continue": True}
    assert step["status"] == catalog.STATUS_DONE


@pytest.mark.asyncio
async def test_a_rejected_file_puts_the_step_back_in_front_of_the_candidate(agent):
    """Not merely marked — outstanding again, which is what the candidate's
    checklist reads to decide what to show them."""
    step = _checklist("pan", "aadhaar")
    step["status"] = catalog.STATUS_ACTIVE
    step["review_decision"] = catalog.REVIEW_REJECTED
    agent.steps = [step]
    agent.submissions = [
        {"run_step_id": "rs-docs", "item_key": "pan", "review_status": "approved"},
        {"run_step_id": "rs-docs", "item_key": "aadhaar", "review_status": "rejected"},
    ]

    result = await agent._advance_collect(step)

    assert result["waiting_for"] == "candidate_documents"
    assert result["outstanding"] == ["aadhaar"]
    assert step["status"] == catalog.STATUS_ACTIVE


@pytest.mark.asyncio
async def test_parking_twice_does_not_email_hr_twice(agent):
    """Any unrelated kick walks the pipeline and reaches the gate again. Daily
    cron sweeps would otherwise mail the same review request every morning."""
    step = _checklist("pan")
    agent.steps = [step]
    agent.submissions = [
        {"run_step_id": "rs-docs", "item_key": "pan", "review_status": "pending"}
    ]

    await agent._advance_collect(step)
    await agent._advance_collect(step)

    assert agent.notified == ["docs"]


@pytest.mark.asyncio
async def test_a_signed_document_is_not_delivered_until_hr_has_seen_it(
    agent, monkeypatch
):
    """The one the whole feature exists for: a signature in the wrong box must
    not reach the candidate as their final copy."""
    step = _step(
        "loi",
        kind=catalog.KIND_GENERATE,
        signer_roles=["candidate"],
        status=catalog.STATUS_PENDING_SIGNATURE,
    )
    agent.steps = [step]

    async def signed(bundle):
        return True

    monkeypatch.setattr(agent, "_signatures_complete", signed)
    result = await agent._advance_generate([step], step)

    assert agent.delivered == []
    assert step["status"] == catalog.STATUS_PENDING_HR_APPROVAL
    assert result["waiting_for"] == "hr_to_approve_candidate_work"


@pytest.mark.asyncio
async def test_hr_approving_a_signature_delivers_it(agent, monkeypatch):
    """HR's approval moves the step to `active`; the agent's job from there is
    the same as it always was."""
    step = _step(
        "loi",
        kind=catalog.KIND_GENERATE,
        signer_roles=["candidate"],
        status=catalog.STATUS_ACTIVE,
        review_decision=catalog.REVIEW_APPROVED,
    )
    agent.steps = [step]

    await agent._advance_generate([step], step)
    assert agent.delivered == ["loi"]


@pytest.mark.asyncio
async def test_a_document_nobody_signs_is_delivered_without_a_gate(agent, monkeypatch):
    """`requires_hr_approval` is on by default, so the induction pack would
    park on a review nobody is expected to do if the gate keyed off the toggle
    alone rather than off whether the candidate acted."""
    step = _step(
        "induction",
        kind=catalog.KIND_GENERATE,
        signer_roles=[],
        status=catalog.STATUS_PENDING_SIGNATURE,
    )
    agent.steps = [step]

    async def signed(bundle):
        return True

    monkeypatch.setattr(agent, "_signatures_complete", signed)
    await agent._advance_generate([step], step)

    assert agent.delivered == ["induction"]


@pytest.mark.asyncio
async def test_a_bundle_parks_as_one_unit(agent, monkeypatch):
    """One decision covers every document in it — HR does not accept the
    appointment letter and leave the NDA sitting in a queue."""
    lead = _step(
        "appointment_letter",
        kind=catalog.KIND_GENERATE,
        bundle_key="offer",
        signer_roles=["candidate"],
        position=0,
        status=catalog.STATUS_PENDING_SIGNATURE,
    )
    nda = _step(
        "nda",
        kind=catalog.KIND_GENERATE,
        bundle_key="offer",
        signer_roles=["candidate"],
        position=1,
        status=catalog.STATUS_PENDING_SIGNATURE,
    )
    agent.steps = [lead, nda]

    async def signed(bundle):
        return True

    monkeypatch.setattr(agent, "_signatures_complete", signed)
    await agent._advance_generate([lead, nda], lead)

    assert lead["status"] == catalog.STATUS_PENDING_HR_APPROVAL
    assert nda["status"] == catalog.STATUS_PENDING_HR_APPROVAL
