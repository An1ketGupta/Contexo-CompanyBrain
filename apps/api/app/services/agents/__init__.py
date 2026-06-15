"""Autonomous-agent framework (Agent Roadmap Day 7+).

`BaseAgent` is the shared lifecycle wrapper every concrete agent inherits
from. It owns the `agent_runs` row, step logging, error handling, and
token / confidence accounting.

Concrete agents (Phase 2) drop into `app.services.agents.<name>_agent` and
override `BaseAgent.run`.
"""
from .base_agent import BaseAgent

__all__ = ["BaseAgent"]
