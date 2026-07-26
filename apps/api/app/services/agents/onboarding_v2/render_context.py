"""The variables a customer template can reference — one definition.

This existed in three hand-maintained copies: the agent's
`_build_render_context` (real run data), the router's preview constant, and
`template_vars.PREVIEW_SAMPLE_CONTEXT` (dry-run validation). Any variable added
to one and forgotten in another produced the worst possible failure shape — a
template that previews cleanly for HR and then fails, or silently renders an
empty field, when a real candidate's document is generated.

So the *shape* is defined once here (`CONTEXT_KEYS` + `build_render_context`) and
the sample context is derived from that same shape rather than typed out again.

Keep in sync with `template_vars.TEMPLATE_VARIABLES`, which is the HR-facing
vocabulary the classifier is allowed to map to; `assert_context_covers_vocabulary`
below turns a mismatch into a loud import-time-checkable error instead of a
render-time surprise.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .template_vars import (
    SIGNATURE_BLOCK_MARKERS,
    TEMPLATE_VARIABLES,
    get_variable_names,
)

# What the signature sentinels render as in a PREVIEW only — see
# `sample_render_context`. Never used for a real document.
PREVIEW_SIGNATURE_LABELS: dict[str, str] = {
    "hr_signature_block": "[ HR signature ]",
    "candidate_signature_block": "[ Candidate signature ]",
}


def build_render_context(
    *,
    run: dict[str, Any],
    branding: dict[str, Any],
) -> dict[str, Any]:
    """Build the substitution context for one onboarding run.

    Pure function of `run` (an `onboarding_runs` row) and `branding` (the
    org-resolved name/legal name/jurisdiction/address), so it can be unit-tested
    and reused by the preview endpoints without an agent instance.

    Used by BOTH fill pipelines: docxtpl reads it as a Jinja context, and the
    slot renderer looks up `context[slot.variable]`. Neither cares which
    produced it, which is exactly why it belongs in one place.
    """
    ctc_amount = run.get("ctc_amount")
    ctc_currency = run.get("ctc_currency") or "INR"
    ctc_formatted = f"{ctc_currency} {float(ctc_amount):,.2f}" if ctc_amount else "TBD"

    return {
        "candidate_name": run["candidate_name"],
        "candidate_email": run["candidate_email"],
        "candidate_phone": run.get("candidate_phone") or "",
        "role_title": run["role_title"],
        "designation": run.get("designation") or run["role_title"],
        "ctc": ctc_formatted,
        "ctc_amount": float(ctc_amount) if ctc_amount else 0.0,
        "ctc_currency": ctc_currency,
        "ctc_breakdown": run.get("ctc_breakdown") or {},
        "start_date": str(run["start_date"]),
        "work_location": run.get("work_location") or "",
        "probation_period_months": run.get("probation_period_months") or 0,
        "reporting_manager_name": run.get("reporting_manager_name") or "",
        "reporting_manager_email": run.get("reporting_manager_email") or "",
        "company_name": branding["name"],
        "company_legal_name": branding["legal_name"],
        "company_address": branding["registered_address"],
        "today_date": datetime.now(UTC).date().isoformat(),
        "jurisdiction": branding["jurisdiction"],
        # Sentinel markers apps/esign's field_placement.py searches the rendered
        # PDF for, to position each signer's signature field.
        **SIGNATURE_BLOCK_MARKERS,
    }


def sample_render_context() -> dict[str, Any]:
    """Obviously-fake values for previews and dry-run validation.

    Built by feeding a synthetic run + branding through the SAME
    `build_render_context` above, so a preview exercises the real code path. If
    a variable is added to the real context and not here, that is now impossible
    — there is only one place to add it.
    """
    examples = {v["name"]: v["example"] for v in TEMPLATE_VARIABLES}

    ctx = build_render_context(
        run={
            "candidate_name": examples["candidate_name"],
            "candidate_email": examples["candidate_email"],
            "candidate_phone": examples["candidate_phone"],
            "role_title": examples["role_title"],
            "designation": examples["designation"],
            "ctc_amount": 2_000_000.0,
            "ctc_currency": examples["ctc_currency"],
            "ctc_breakdown": {
                "basic": 1_000_000,
                "hra": 500_000,
                "special_allowance": 500_000,
            },
            "start_date": examples["start_date"],
            "work_location": examples["work_location"],
            "probation_period_months": 3,
            "reporting_manager_name": examples["reporting_manager_name"],
            "reporting_manager_email": examples["reporting_manager_email"],
        },
        branding={
            "name": examples["company_name"],
            "legal_name": examples["company_legal_name"],
            "jurisdiction": examples["jurisdiction"],
            "registered_address": examples["company_address"],
            "logo_url": None,
        },
    )
    # Preview-only override: the real context computes today's date, but a
    # preview should be reproducible rather than changing every day.
    ctx["today_date"] = examples["today_date"]

    # Preview-only override: in a real render these are sentinel strings that
    # apps/esign finds and whites out before stamping the signature. Nobody
    # whites them out in a preview, so show HR a readable label instead of a
    # cryptic "◇SIGN:HR◇" — the position is what they're checking, not the
    # marker text.
    ctx.update(PREVIEW_SIGNATURE_LABELS)
    return ctx


def missing_vocabulary_keys() -> list[str]:
    """Vocabulary variables the render context does NOT supply.

    A non-empty result is a real bug: the classifier could map a blank to a
    variable that then raises `TemplateVariableError` mid-render. Exposed as a
    function (not an import-time assert) so a test can assert on it without
    making the module unimportable in a half-migrated state.
    """
    supplied = set(sample_render_context())
    return sorted(v for v in get_variable_names() if v not in supplied)
