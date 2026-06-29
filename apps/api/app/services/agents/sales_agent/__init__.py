"""SalesAgent — autonomous deal pipeline.

Re-entrant state machine that walks a single opportunity (deal_runs row)
from `lead_entered` through to `closed_won` / `closed_lost`. Human gates
on outreach approval, proposal approval, and the next-call-vs-propose
decision after each call.

Modelled after OnboardingV2Agent (migration 071+) but bespoke — no shared
base pipeline class. See Sales_Agent.md for the architectural decision.

Imported lazily so storage / service helpers can be loaded without
constructing the full agent class.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.agents.sales_agent.agent import SalesAgent

__all__ = ["SalesAgent"]


def __getattr__(name: str):
    if name == "SalesAgent":
        from app.services.agents.sales_agent.agent import SalesAgent
        return SalesAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
