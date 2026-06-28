"""Shared pytest setup.

We import `app.main` at collection time so the heavier app modules (Inngest
fan-out, agent registrations, email dispatcher) initialise in the same
order they would in production. Without this, tests that import a
submodule like `app.services.agents.onboarding_v2.agent` directly can hit
a circular-import edge between `email/dispatcher` and the legacy
`onboarding_agent` — production avoids this because `create_app()`
sequences things explicitly.
"""
from __future__ import annotations


def pytest_configure(config) -> None:
    # Side-effect import — establishes module load order. The returned
    # FastAPI instance is unused; we only need the side effects.
    import app.main  # noqa: F401
