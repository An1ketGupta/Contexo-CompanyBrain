"""Per-org onboarding step toggles.

Covers the two halves that can go wrong independently: parsing whatever is
sitting in `organizations.metadata`, and the agent's routing when a step is
turned off. The routing tests stub out everything that touches the network —
what's under test is which step runs next and what status the run is left in,
not the paperwork each step produces.
"""
from __future__ import annotations

import pytest

import app.inngest  # noqa: F401  (see below)
from app.services import org_config
from app.services.agents.onboarding_v2.agent import OnboardingV2Agent

# `app.inngest` has to be imported first. There is a cycle between it and
# `app.services.email`, and only that entry order resolves — importing the
# agent first fails on a partially-initialised `app.services.email`. The app
# itself gets this for free via `app.main`.

ALL_OFF = org_config.OnboardingStepConfig(
    bgv=False, appointment_bundle=False, policies=False, induction=False
)


# ── Parsing ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "meta",
    [
        None,
        {},
        {"onboarding_v2_steps": None},
        {"onboarding_v2_steps": "nonsense"},
        {"onboarding_v2_steps": {}},
        {"onboarding_v2_steps": {"bgv": "yes"}},
        {"onboarding_v2_steps": {"unknown_step": False}},
    ],
)
def test_missing_or_corrupt_metadata_runs_every_step(meta):
    """Fail open. The alternative reading of a bad row is silently dropping a
    step of someone's hiring paperwork."""
    steps = org_config._parse_steps(meta)
    assert all(getattr(steps, key) is True for key in org_config.STEP_KEYS)


@pytest.mark.parametrize(
    "meta",
    [None, {}, {"onboarding_v2_steps": None}, {"onboarding_v2_steps": "nonsense"}],
)
def test_org_is_unconfigured_until_it_saves_a_choice(meta):
    """Drives the first-run setup screen. An org with no stored dict has never
    decided, which is a different thing from deciding to run everything."""
    assert org_config._parse_steps(meta).configured is False


@pytest.mark.parametrize(
    "meta",
    [
        {"onboarding_v2_steps": {"bgv": True, "policies": True}},
        {"onboarding_v2_steps": {"bgv": False}},
        # A dict we can't read still means somebody wrote one, so don't send
        # them back through setup — they'd only re-save the same defaults.
        {"onboarding_v2_steps": {"bgv": "yes"}},
        {"onboarding_v2_steps": {}},
    ],
)
def test_a_stored_dict_marks_the_org_configured(meta):
    assert org_config._parse_steps(meta).configured is True


def test_only_explicit_false_disables_a_step():
    steps = org_config._parse_steps(
        {"onboarding_v2_steps": {"bgv": False, "induction": True}}
    )
    assert steps.bgv is False
    assert steps.induction is True
    # Absent keys stay on rather than inheriting the disabled sibling.
    assert steps.appointment_bundle is True
    assert steps.policies is True


def test_other_metadata_keys_are_ignored():
    steps = org_config._parse_steps(
        {"archive": {"threshold_days": 45}, "onboarding_v2_steps": {"policies": False}}
    )
    assert steps.policies is False
    assert steps.bgv is True


# ── Agent routing ─────────────────────────────────────────────────────────


@pytest.fixture
def agent(monkeypatch):
    """An agent whose side effects are recorded instead of performed.

    `statuses` accumulates every `_set_status` call so a test can assert the
    run walked through the states a real completion would have written.
    """
    a = OnboardingV2Agent(org_id="org-1", run_id="run-1")
    a.statuses: list[str] = []

    async def fake_set_status(status, *, extra=None):
        a.statuses.append(status)

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(a, "_set_status", fake_set_status)
    monkeypatch.setattr(a, "log_step", noop)
    monkeypatch.setattr(
        "app.services.agents.onboarding_v2.agent.ob_storage.log_onboarding_event", noop
    )
    return a


def set_steps(agent, steps):
    agent._steps = steps


@pytest.mark.asyncio
async def test_bundle_enabled_generates_it(agent, monkeypatch):
    set_steps(agent, org_config.OnboardingStepConfig.default())

    async def fake_bundle():
        return {"status": "appointment_pending_hr_review"}

    monkeypatch.setattr(agent, "_step_generate_offer_bundle", fake_bundle)

    assert await agent._step_after_bgv() == {"status": "appointment_pending_hr_review"}
    assert agent.statuses == []


@pytest.mark.asyncio
async def test_bundle_disabled_hands_to_policies(agent, monkeypatch):
    set_steps(
        agent, org_config.OnboardingStepConfig(appointment_bundle=False)
    )
    called = []

    async def fake_policies():
        called.append(True)
        return {"status": "policies_assigned"}

    monkeypatch.setattr(agent, "_step_assign_policies", fake_policies)

    await agent._step_after_bgv()
    # The status a real HR approval would have written, so a re-kick from here
    # dispatches into the same next step.
    assert agent.statuses == ["appointment_sent_to_candidate"]
    assert called


@pytest.mark.asyncio
async def test_policies_disabled_hands_to_induction(agent, monkeypatch):
    set_steps(agent, org_config.OnboardingStepConfig(policies=False))
    called = []

    async def fake_induction():
        called.append(True)
        return {"status": "induction_sent"}

    monkeypatch.setattr(agent, "_step_generate_induction", fake_induction)

    await agent._step_assign_policies()
    assert agent.statuses == ["policies_acknowledged"]
    assert called


@pytest.mark.asyncio
async def test_induction_disabled_finalises(agent):
    set_steps(agent, org_config.OnboardingStepConfig(induction=False))

    result = await agent._step_generate_induction()

    assert result == {"status": "completed"}
    assert agent.statuses == ["completed"]


@pytest.mark.asyncio
async def test_everything_after_loi_disabled_runs_straight_to_completed(agent):
    """The whole chain composes: one entry call, no parking, no re-kick."""
    set_steps(agent, ALL_OFF)

    result = await agent._step_after_bgv()

    assert result == {"status": "completed"}
    assert agent.statuses == [
        "appointment_sent_to_candidate",
        "policies_acknowledged",
        "completed",
    ]


@pytest.mark.asyncio
async def test_bgv_complete_dispatch_respects_the_bundle_toggle(agent, monkeypatch):
    """A run parked on `bgv_complete` must route through the toggle check, not
    straight at bundle generation."""
    set_steps(agent, ALL_OFF)

    async def fake_load_run():
        return {"status": "bgv_complete"}

    monkeypatch.setattr(agent, "_load_run", fake_load_run)

    result = await agent.run()

    assert result == {"status": "completed"}
    assert "appointment_sent_to_candidate" in agent.statuses


@pytest.mark.asyncio
async def test_disabling_a_step_unblocks_a_run_stuck_on_its_template(agent, monkeypatch):
    """Turning off the appointment bundle is a way out of
    `blocked_missing_template`, not just a setting for future runs."""
    set_steps(agent, ALL_OFF)

    async def fake_load_run():
        return {
            "status": "blocked_missing_template",
            "blocked_template_kind": "appointment_letter",
        }

    monkeypatch.setattr(agent, "_load_run", fake_load_run)

    assert await agent.run() == {"status": "completed"}
