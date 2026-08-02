"""The step-driven dispatcher that replaced the status ladder.

What's under test is which step runs next and what the run is left in — not the
paperwork each step produces, so every handler that would touch the network is
stubbed. The behaviours here are the ones the old if/elif chain guaranteed and
that a configurable pipeline still has to: a disabled step is walked past, a
pipeline of nothing but disabled steps completes, and several steps that finish
instantly are all advanced in one invocation rather than one Inngest kick each.
"""
from __future__ import annotations

import pytest

import app.inngest  # noqa: F401  (import-order cycle; see test_onboarding_step_toggles)
from app.services.agents.onboarding_v2 import catalog
from app.services.agents.onboarding_v2.agent import OnboardingV2Agent

ORG = "org-1"
RUN = "run-1"


def _step(step_key, position, status=catalog.STATUS_PENDING, **extra):
    return {
        "id": f"rs-{step_key}",
        "run_id": RUN,
        "org_id": ORG,
        "step_key": step_key,
        "kind": extra.pop("kind", catalog.KIND_GENERATE),
        "label": step_key.replace("_", " ").title(),
        "document_type_key": step_key,
        "bundle_key": extra.pop("bundle_key", None),
        "bundle_label": None,
        "position": position,
        "signer_roles": extra.pop("signer_roles", []),
        "system_action": extra.pop("system_action", None),
        "config": extra.pop("config", {}),
        "status": status,
        **extra,
    }


@pytest.fixture
def agent(monkeypatch):
    """An agent whose side effects are recorded instead of performed."""
    a = OnboardingV2Agent(org_id=ORG, run_id=RUN)
    a.statuses: list[str] = []
    a.steps: list[dict] = []

    async def fake_set_status(status, *, extra=None):
        a.statuses.append(status)

    async def fake_load_run():
        return {"id": RUN, "status": a.statuses[-1] if a.statuses else "draft"}

    async def fake_set_step_status(run_step_id, status, *, blocked_reason=None):
        for s in a.steps:
            if s["id"] == run_step_id:
                s["status"] = status

    async def fake_materialize(*, org_id, run_id):
        return a.steps

    async def fake_get_run_steps(run_id):
        return a.steps

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(a, "_set_status", fake_set_status)
    monkeypatch.setattr(a, "_load_run", fake_load_run)
    monkeypatch.setattr(a, "_refresh_run", fake_load_run)
    monkeypatch.setattr(a, "log_step", noop)
    monkeypatch.setattr(catalog, "set_step_status", fake_set_step_status)
    monkeypatch.setattr(catalog, "materialize_run_steps", fake_materialize)
    monkeypatch.setattr(catalog, "get_run_steps", fake_get_run_steps)
    monkeypatch.setattr(
        "app.services.agents.onboarding_v2.agent.ob_storage.log_onboarding_event", noop
    )
    return a


@pytest.mark.asyncio
async def test_a_run_with_no_steps_parks_rather_than_completing(agent):
    """An org with no catalog has not finished onboarding anybody. Reporting
    'done' would close a hire that never started."""
    agent.steps = []
    result = await agent.run()
    assert result["no_steps"] is True
    assert "completed" not in agent.statuses


@pytest.mark.asyncio
async def test_every_step_skipped_completes_the_run(agent):
    agent.steps = [
        _step("loi", 0, catalog.STATUS_SKIPPED),
        _step("bgv", 10, catalog.STATUS_SKIPPED, kind=catalog.KIND_SYSTEM),
        _step("induction", 20, catalog.STATUS_SKIPPED),
    ]
    assert (await agent.run())["status"] == "completed"
    assert agent.statuses == ["completed"]


@pytest.mark.asyncio
async def test_a_disabled_step_is_walked_past(agent, monkeypatch):
    """The toggle behaviour the four booleans used to provide, now expressed as
    a step the run snapshotted as `skipped`."""
    agent.steps = [
        _step("loi", 0, catalog.STATUS_DONE),
        _step("bgv", 10, catalog.STATUS_SKIPPED, kind=catalog.KIND_SYSTEM),
        _step("induction", 20),
    ]
    reached: list[str] = []

    async def fake_generate(steps, step):
        reached.append(step["step_key"])
        return {"status": "parked"}

    monkeypatch.setattr(agent, "_advance_generate", fake_generate)
    await agent.run()
    assert reached == ["induction"]


@pytest.mark.asyncio
async def test_several_instant_steps_advance_in_one_invocation(agent, monkeypatch):
    """A pipeline where nothing needs a human should not need one Inngest kick
    per step to get through."""
    agent.steps = [_step(f"s{i}", i * 10) for i in range(4)]

    async def instant(steps, step):
        await catalog.set_step_status(step["id"], catalog.STATUS_DONE)
        return {"_continue": True}

    monkeypatch.setattr(agent, "_advance_generate", instant)
    assert (await agent.run())["status"] == "completed"
    assert all(s["status"] == catalog.STATUS_DONE for s in agent.steps)


@pytest.mark.asyncio
async def test_a_step_that_never_moves_parks_instead_of_spinning(agent, monkeypatch):
    """A handler that claims to have advanced but didn't would otherwise loop
    forever. The ceiling turns that bug into one wasted invocation."""
    agent.steps = [_step("loi", 0)]
    calls = []

    async def never_advances(steps, step):
        calls.append(1)
        return {"_continue": True}

    monkeypatch.setattr(agent, "_advance_generate", never_advances)
    result = await agent.run()
    assert result["throttled"] is True
    assert len(calls) == 50


@pytest.mark.asyncio
async def test_terminal_runs_are_never_re_driven(agent, monkeypatch):
    """A late event — a BGV reply that lands after HR cancelled — must not
    resurrect the run or fire any side effect."""
    async def cancelled():
        return {"id": RUN, "status": "cancelled"}

    monkeypatch.setattr(agent, "_load_run", cancelled)
    agent.steps = [_step("loi", 0)]
    assert await agent.run() == {"status": "cancelled", "terminal": True}


@pytest.mark.asyncio
async def test_system_steps_dispatch_on_action_not_key(agent, monkeypatch):
    """An org may rename "Background verification" or run two of them."""
    agent.steps = [
        _step(
            "reference_checks",
            0,
            kind=catalog.KIND_SYSTEM,
            system_action=catalog.SYSTEM_ACTION_BGV,
        )
    ]
    seen: list[str] = []

    async def fake_bgv(step):
        seen.append(step["step_key"])
        return {"status": "parked"}

    monkeypatch.setattr(agent, "_advance_bgv", fake_bgv)
    await agent.run()
    assert seen == ["reference_checks"]


@pytest.mark.asyncio
async def test_a_system_step_with_no_action_is_skipped(agent):
    """Rather than parking the run forever on a step nothing knows how to run."""
    agent.steps = [_step("mystery", 0, kind=catalog.KIND_SYSTEM, system_action=None)]
    assert (await agent.run())["status"] == "completed"
    assert agent.steps[0]["status"] == catalog.STATUS_SKIPPED


@pytest.mark.asyncio
async def test_a_bundle_is_generated_as_one_unit(agent, monkeypatch):
    agent.steps = [
        _step("appointment_letter", 0, bundle_key="offer"),
        _step("nda", 1, bundle_key="offer"),
    ]
    generated: list[list[str]] = []

    async def fake_generate(bundle):
        generated.append([m["step_key"] for m in bundle])
        return {"status": "parked"}

    monkeypatch.setattr(agent, "_generate_bundle", fake_generate)
    await agent.run()
    assert generated == [["appointment_letter", "nda"]]


@pytest.mark.asyncio
async def test_a_collect_step_completes_once_required_items_are_in(agent, monkeypatch):
    agent.steps = [
        _step(
            "joining_docs",
            0,
            catalog.STATUS_ACTIVE,
            kind=catalog.KIND_COLLECT,
            config={
                "items": [
                    {"item_key": "pan", "label": "PAN", "required": True},
                    {"item_key": "photo", "label": "Photo", "required": False},
                ]
            },
        )
    ]

    async def submissions(run_id):
        return [{"run_step_id": "rs-joining_docs", "item_key": "pan"}]

    monkeypatch.setattr(catalog, "get_submissions", submissions)
    assert (await agent.run())["status"] == "completed"
    assert agent.steps[0]["status"] == catalog.STATUS_DONE


@pytest.mark.asyncio
async def test_a_collect_step_holds_the_run_until_they_are(agent, monkeypatch):
    agent.steps = [
        _step(
            "joining_docs",
            0,
            catalog.STATUS_ACTIVE,
            kind=catalog.KIND_COLLECT,
            config={"items": [{"item_key": "pan", "label": "PAN", "required": True}]},
        ),
        _step("induction", 10),
    ]

    async def submissions(run_id):
        return []

    reached: list[str] = []

    async def fake_generate(steps, step):
        reached.append(step["step_key"])
        return {"status": "parked"}

    monkeypatch.setattr(catalog, "get_submissions", submissions)
    monkeypatch.setattr(agent, "_advance_generate", fake_generate)

    result = await agent.run()
    assert result["waiting_for"] == "candidate_documents"
    assert result["outstanding"] == ["pan"]
    assert reached == [], "the pipeline must not run past an unfinished ask"
