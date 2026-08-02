"""Parsing the legacy per-org onboarding step booleans.

`organizations.metadata.onboarding_v2_steps` no longer drives the pipeline —
the catalog does, and a disabled step is written as `skipped` when a run
snapshots it. The four booleans survive as the record of whether an org has
ever configured anything (which gates the first-run setup screen) and are kept
in step by `catalog.sync_legacy_toggles`.

Parsing them still has to fail open, which is what this covers. The dispatcher
tests that used to live here moved to test_onboarding_dispatcher.py, which
exercises the step engine that replaced the status ladder.
"""
from __future__ import annotations

import pytest

import app.inngest  # noqa: F401  (see below)
from app.services import org_config

# `app.inngest` has to be imported first. There is a cycle between it and
# `app.services.email`, and only that entry order resolves — importing the
# agent first fails on a partially-initialised `app.services.email`. The app
# itself gets this for free via `app.main`.


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
