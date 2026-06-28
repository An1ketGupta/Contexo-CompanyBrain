"""OnboardingV2Agent — pre-join HR pipeline (LOI → BGV → Appointment + NDA →
Policies → Induction).

Distinct from the existing OnboardingAgent (`onboarding_agent.py`), which
runs *after* invite acceptance to generate a 90-day plan. This agent runs
*before* that — driven by HR clicking "Mark Hired & Start Onboarding" on a
recruiting candidate.
"""
from app.services.agents.onboarding_v2.agent import OnboardingV2Agent

__all__ = ["OnboardingV2Agent"]
