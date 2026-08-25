"""Registry of every posting destination — ATS or job board — the
Recruiting Agent can publish to.

Why this exists
---------------
The publish form, audit log, and requisition detail page all want to
group destinations by kind ("ATS Platforms" vs "Job Boards") and emit
slightly different copy / icons / connect dialogs per kind. Without a
registry the kind-tag would be re-derived inline at every call site by an
a provider-specific ladder.

Source of truth lives here. Add a new destination by appending a row.

Each entry is intentionally a plain dataclass — Pydantic isn't worth it for
something this static, and the registry is imported from places that should
not pull in heavy dependencies (e.g. config validation at boot).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DestinationType = Literal["ats", "job_board"]


@dataclass(frozen=True)
class PostingDestination:
    """One destination the requisition can publish to."""

    # Stable identifier used in DB columns and JSON keys. Never rename — these
    # values are persisted on ats_postings[].platform and there is no
    # back-fill story.
    key: str
    # User-visible label.
    name: str
    # Coarse kind. Drives UI grouping + which connect dialog to render.
    kind: DestinationType
    # Short one-liner used in the Settings → Integrations card subtitle.
    blurb: str
    # Whether the destination is enterprise-sales-gated (no self-serve
    # developer tier). When true, the UI displays a "Bring your own
    # contract" note in the connect dialog so the recruiter knows the API
    # key has to come from the provider's sales team.
    requires_contract: bool = True


_DESTINATIONS: tuple[PostingDestination, ...] = (
    PostingDestination(
        key="greenhouse",
        name="Greenhouse",
        kind="ats",
        blurb=(
            "Publish AI-drafted JDs to Greenhouse Harvest. Requires a Harvest "
            "API key with job:write scope."
        ),
    ),
    PostingDestination(
        key="lever",
        name="Lever",
        kind="ats",
        blurb=(
            "Publish to Lever's Postings API. Production keys only — sandbox "
            "keys hit a different host."
        ),
    ),
    PostingDestination(
        key="ashby",
        name="Ashby",
        kind="ats",
        blurb=(
            "Publish to Ashby's Public API. Requires apiKey:read + "
            "jobOpening:create scopes."
        ),
    ),
)


_BY_KEY: dict[str, PostingDestination] = {d.key: d for d in _DESTINATIONS}


def all_destinations() -> tuple[PostingDestination, ...]:
    """Stable iteration order — never depend on dict ordering for this."""
    return _DESTINATIONS


def get(key: str) -> PostingDestination:
    """Look up a destination. Raises KeyError on unknown — callers should
    treat that as a programmer error, not user input. Validate the input
    string upstream against the Pydantic Literal."""
    return _BY_KEY[key]


def kind_of(key: str) -> DestinationType:
    """Cheap lookup that the publish flow uses to tag ats_postings entries."""
    return _BY_KEY[key].kind


def is_ats(key: str) -> bool:
    return _BY_KEY.get(key) is not None and _BY_KEY[key].kind == "ats"


def is_job_board(key: str) -> bool:
    return _BY_KEY.get(key) is not None and _BY_KEY[key].kind == "job_board"


def keys_by_kind(kind: DestinationType) -> tuple[str, ...]:
    return tuple(d.key for d in _DESTINATIONS if d.kind == kind)
