"""State-machine tests for OnboardingV2Agent.

We exercise the dispatcher directly without spinning up Supabase. Each test
stubs `_load_run` to return a fake row in a specific status and asserts
the agent dispatches to the right step (or no-ops for terminal states).

Network-dependent side effects (PDF generation, Inngest events, Supabase
mutations) are stubbed at module boundary.
"""
from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture
def fake_agent(monkeypatch):
    """Return a partially-stubbed OnboardingV2Agent. Caller sets `_run_row`
    to drive the dispatcher."""
    from app.services.agents.onboarding_v2.agent import OnboardingV2Agent

    agent = OnboardingV2Agent.__new__(OnboardingV2Agent)
    agent.onboarding_run_id = "run-1"
    agent.org_id = "org-1"
    agent.resume_from = None
    agent._run_row = None
    # BaseAgent.log_step writes to agent_runs; stub it out.
    agent.log_step = _async_noop  # type: ignore[assignment]
    # Bound logger noop.
    class _NoLog:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, *a, **k): pass
        def bind(self, **k): return self
    agent.log = _NoLog()
    return agent


async def _async_noop(*args: Any, **kwargs: Any) -> Any:
    return None


@pytest.mark.asyncio
async def test_terminal_cancelled_does_not_dispatch(monkeypatch, fake_agent) -> None:
    """A late event for a cancelled run must not resurrect it."""
    fake_agent._run_row = {"id": "run-1", "status": "cancelled"}

    called = {"step_generate_loi": False}

    async def _spy(*a, **k):
        called["step_generate_loi"] = True
        return {}

    monkeypatch.setattr(fake_agent, "_step_generate_loi", _spy)
    result = await fake_agent.run()

    assert result["status"] == "cancelled"
    assert result.get("terminal") is True
    assert called["step_generate_loi"] is False


@pytest.mark.asyncio
async def test_terminal_completed_does_not_dispatch(monkeypatch, fake_agent) -> None:
    fake_agent._run_row = {"id": "run-1", "status": "completed"}

    async def _fail(*a, **k):
        raise AssertionError("dispatcher must not call any step")

    monkeypatch.setattr(fake_agent, "_step_finalise", _fail)
    monkeypatch.setattr(fake_agent, "_step_generate_loi", _fail)
    result = await fake_agent.run()
    assert result == {"status": "completed", "terminal": True}


@pytest.mark.asyncio
async def test_draft_dispatches_to_generate_loi(monkeypatch, fake_agent) -> None:
    fake_agent._run_row = {"id": "run-1", "status": "draft"}

    called = {"hit": False}

    async def _spy(*a, **k):
        called["hit"] = True
        return {"status": "loi_pending_hr_sign"}

    monkeypatch.setattr(fake_agent, "_step_generate_loi", _spy)
    await fake_agent.run()
    assert called["hit"] is True


@pytest.mark.asyncio
async def test_loi_pending_hr_sign_waits(monkeypatch, fake_agent) -> None:
    fake_agent._run_row = {"id": "run-1", "status": "loi_pending_hr_sign"}
    result = await fake_agent.run()
    assert result == {
        "status": "loi_pending_hr_sign",
        "waiting_for": "hr_to_upload_signed_loi",
    }


@pytest.mark.asyncio
async def test_appointment_pending_hr_review_waits(monkeypatch, fake_agent) -> None:
    fake_agent._run_row = {"id": "run-1", "status": "appointment_pending_hr_review"}
    result = await fake_agent.run()
    assert result == {
        "status": "appointment_pending_hr_review",
        "waiting_for": "hr_to_approve_bundle",
    }


@pytest.mark.asyncio
async def test_blocked_missing_template_loi_retries_loi_step(
    monkeypatch, fake_agent,
) -> None:
    fake_agent._run_row = {
        "id": "run-1",
        "status": "blocked_missing_template",
        "blocked_template_kind": "loi",
    }
    called = {"hit": False}

    async def _spy(*a, **k):
        called["hit"] = True
        return {}

    monkeypatch.setattr(fake_agent, "_step_generate_loi", _spy)
    await fake_agent.run()
    assert called["hit"] is True


@pytest.mark.asyncio
async def test_blocked_missing_template_nda_retries_bundle_step(
    monkeypatch, fake_agent,
) -> None:
    fake_agent._run_row = {
        "id": "run-1",
        "status": "blocked_missing_template",
        "blocked_template_kind": "nda",
    }
    called = {"hit": False}

    async def _spy(*a, **k):
        called["hit"] = True
        return {}

    monkeypatch.setattr(fake_agent, "_step_generate_offer_bundle", _spy)
    await fake_agent.run()
    assert called["hit"] is True
