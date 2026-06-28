"""OnboardingV2Agent — pre-join HR pipeline (LOI → BGV → Appointment + NDA →
Policies → Induction).

Distinct from the existing OnboardingAgent (`onboarding_agent.py`), which
runs *after* invite acceptance to generate a 90-day plan. This agent runs
*before* that — driven by HR clicking "Mark Hired & Start Onboarding" on a
recruiting candidate.

The class is imported lazily via __getattr__ so that side-effect-free
submodules (default_templates, storage helpers) can be imported in tests
without triggering the full agent + email-dispatcher chain.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.agents.onboarding_v2.agent import OnboardingV2Agent

__all__ = ["OnboardingV2Agent"]


def __getattr__(name: str):
    if name == "OnboardingV2Agent":
        from app.services.agents.onboarding_v2.agent import OnboardingV2Agent
        return OnboardingV2Agent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
