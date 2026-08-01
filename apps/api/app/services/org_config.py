"""Per-org configuration cache.

What lives here:
  * `ai_instructions` (Day 9 / #67) — admin-supplied text prepended to the
    LLM system prompt on every chat turn.
  * `confidence_thresholds` (Agent Day 3) — admin-tunable cosine cutoffs for
    the high/medium bands shown on the message confidence badge. Stored in
    `organizations.metadata.confidence_thresholds`; defaults baked in so a
    brand-new org never sees a "not configured" state.
  * `onboarding_v2_steps` — which optional steps of the onboarding pipeline
    this org runs. Read by `OnboardingV2Agent` once per invocation. Stored in
    `organizations.metadata.onboarding_v2_steps`.

We expect:
  * Hot-path read on every chat call (must stay <1ms).
  * Cold-path write from settings UI (a few times per org per year).

So we keep an in-process TTL cache keyed by org_id. 60s of staleness on
edit is fine — admins know "saved" doesn't mean "instant" for an LLM
config, and any process that handled the PATCH invalidates its own entry
immediately for the writer's next turn.

Cross-process consistency is best-effort: another worker process may
still serve a stale value for up to 60s. That's acceptable for the
write rate we expect; revisit if/when an org reports a confusing lag.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from app.database import get_service_client
from app.observability import get_logger

log = get_logger(__name__)

# Tunable. Short enough that an edit propagates in human time, long enough
# that a single chatty user can't melt the DB with config reads.
ORG_CONFIG_TTL_SECONDS = 60.0

# Hard cap on instructions length — mirrored by the API validator. Acts as
# defence-in-depth against a malformed DB row leaking unbounded context into
# the system prompt.
INSTRUCTIONS_MAX_CHARS = 500

# Default confidence thresholds — calibrated on Google `text-embedding-004`
# cosine similarity. 0.75+ is "this chunk is unambiguously about the query";
# 0.45+ is "topically related"; below that is noise. These are the values
# used before Day 3 made them per-org-tunable.
DEFAULT_CONFIDENCE_HIGH = 0.75
DEFAULT_CONFIDENCE_MEDIUM = 0.45
# Default for the "block" tier is 0.0 = off. A brand-new org doesn't gate
# outbound writes on confidence; admins opt in by raising it in the settings
# UI. This preserves the v1 behavior and the existing Day 3-4 write flows.
DEFAULT_CONFIDENCE_BLOCK = 0.0

# Every optional onboarding step is on unless an admin turns it off. A brand-new
# org gets the full pipeline, which is the behaviour that existed before steps
# became per-org configurable.
DEFAULT_STEP_ENABLED = True


@dataclass(frozen=True)
class ConfidenceThresholds:
    """Three cutoffs that partition the cosine range into high/medium/low/block.

    Stored on `organizations.metadata.confidence_thresholds` as
    `{"high": 0.75, "medium": 0.45, "block": 0.0}`.
    `0 <= block <= medium <= high <= 1` is enforced at write time so the
    bands stay well-ordered (otherwise a typo could make every answer
    "low confidence" or, worse, accidentally block every outbound write).

    `block` is the cosine cutoff below which outbound writes (Slack post,
    Gmail send, Notion create page, Gdocs export) are refused server-side.
    `block == 0` disables the gate entirely.
    """

    high: float
    medium: float
    block: float = DEFAULT_CONFIDENCE_BLOCK

    @classmethod
    def default(cls) -> ConfidenceThresholds:
        return cls(
            high=DEFAULT_CONFIDENCE_HIGH,
            medium=DEFAULT_CONFIDENCE_MEDIUM,
            block=DEFAULT_CONFIDENCE_BLOCK,
        )


@dataclass(frozen=True)
class OnboardingStepConfig:
    """Which optional steps of the onboarding pipeline this org runs.

    Stored on `organizations.metadata.onboarding_v2_steps` as
    `{"bgv": true, "appointment_bundle": true, "policies": true,
    "induction": true}`.

    LOI is deliberately absent: it is the one mandatory step, and every
    later step reads state the LOI step writes. The four here can each be
    turned off independently — the agent skips straight to the next enabled
    step rather than parking on one the org doesn't run. Order is fixed;
    this is a set of on/off switches, not a workflow builder.

    `configured` is not a step. It records whether the org has ever saved a
    choice, which the UI needs to tell "we run everything because they said so"
    apart from "we run everything because nobody has decided yet" — the two
    look identical in the four flags. It gates a first-run setup screen only;
    the agent ignores it and runs the flags either way, so an org that never
    finishes setup still onboards on the defaults.
    """

    bgv: bool = DEFAULT_STEP_ENABLED
    appointment_bundle: bool = DEFAULT_STEP_ENABLED
    policies: bool = DEFAULT_STEP_ENABLED
    induction: bool = DEFAULT_STEP_ENABLED
    configured: bool = False

    @classmethod
    def default(cls) -> OnboardingStepConfig:
        return cls()


# The metadata keys, in pipeline order. Used by the parser and the writer so
# neither has to repeat the field list.
STEP_KEYS = ("bgv", "appointment_bundle", "policies", "induction")


@dataclass(frozen=True)
class OrgConfig:
    org_id: str
    ai_instructions: str | None
    confidence: ConfidenceThresholds
    steps: OnboardingStepConfig
    fetched_at: float


_cache: dict[str, OrgConfig] = {}
_lock = asyncio.Lock()


def invalidate(org_id: str) -> None:
    """Drop the cached entry for a single org (called after writes)."""
    _cache.pop(org_id, None)


def invalidate_all() -> None:
    """Test/dev hook — wipe the entire cache."""
    _cache.clear()


def _parse_confidence(meta: dict[str, Any] | None) -> ConfidenceThresholds:
    """Read thresholds out of metadata JSONB, falling back to defaults.

    Defensive against partial / corrupt rows: anything we can't parse as a
    float in [0, 1] reverts to default. The admin UI validates on write, but
    a hand-edited row shouldn't poison every chat call.
    """
    if not meta:
        return ConfidenceThresholds.default()
    raw = meta.get("confidence_thresholds")
    if not isinstance(raw, dict):
        return ConfidenceThresholds.default()
    try:
        high = float(raw.get("high"))
        medium = float(raw.get("medium"))
    except (TypeError, ValueError):
        return ConfidenceThresholds.default()
    if not (0.0 <= medium <= high <= 1.0):
        return ConfidenceThresholds.default()
    # `block` is new (Day 3-4 hardening) — older rows simply don't have it.
    # Missing / bad value → keep the default (0.0 = off) rather than poison
    # the row's parse; the live write gate handles this as "no block".
    block_raw = raw.get("block")
    block = DEFAULT_CONFIDENCE_BLOCK
    if block_raw is not None:
        try:
            block_candidate = float(block_raw)
        except (TypeError, ValueError):
            block_candidate = None
        if block_candidate is not None and 0.0 <= block_candidate <= medium:
            block = block_candidate
    return ConfidenceThresholds(high=high, medium=medium, block=block)


def _parse_steps(meta: dict[str, Any] | None) -> OnboardingStepConfig:
    """Read the step toggles out of metadata JSONB, defaulting to all-on.

    Same defensive posture as `_parse_confidence`: an org that has never
    touched the setting, a row someone hand-edited into a non-dict, or a key
    holding a value that isn't a bool all resolve to "run this step". Failing
    open matters more here than elsewhere — the alternative reading of a
    corrupt row is silently skipping a step of someone's hiring paperwork.
    """
    if not meta:
        return OnboardingStepConfig.default()
    raw = meta.get("onboarding_v2_steps")
    if not isinstance(raw, dict):
        return OnboardingStepConfig.default()
    return OnboardingStepConfig(
        **{key: _as_bool(raw.get(key)) for key in STEP_KEYS},
        configured=True,
    )


def _as_bool(value: Any) -> bool:
    """A step is enabled unless the stored value is exactly `false`."""
    return value if isinstance(value, bool) else DEFAULT_STEP_ENABLED


async def get_org_config(org_id: str) -> OrgConfig:
    now = time.monotonic()
    cached = _cache.get(org_id)
    if cached and (now - cached.fetched_at) < ORG_CONFIG_TTL_SECONDS:
        return cached

    # Coalesce concurrent misses so a thundering-herd of chat requests on a
    # cold org_id doesn't fan out into N parallel DB calls.
    async with _lock:
        cached = _cache.get(org_id)
        if cached and (time.monotonic() - cached.fetched_at) < ORG_CONFIG_TTL_SECONDS:
            return cached

        svc = get_service_client()
        try:
            result = await asyncio.to_thread(
                lambda: svc.table("organizations")
                .select("ai_instructions, metadata")
                .eq("id", org_id)
                .maybe_single()
                .execute()
            )
            row = (result.data or {}) if result else {}
        except Exception as exc:
            # Fail open — we'd rather chat without org context than block on
            # a transient DB blip. Don't poison the cache with the failure.
            log.warning("org_config_fetch_failed", org_id=org_id, error=str(exc))
            return OrgConfig(
                org_id=org_id,
                ai_instructions=None,
                confidence=ConfidenceThresholds.default(),
                steps=OnboardingStepConfig.default(),
                fetched_at=now,
            )

        raw = (row.get("ai_instructions") or "").strip()
        instructions = raw[:INSTRUCTIONS_MAX_CHARS] or None
        confidence = _parse_confidence(row.get("metadata"))
        steps = _parse_steps(row.get("metadata"))
        cfg = OrgConfig(
            org_id=org_id,
            ai_instructions=instructions,
            confidence=confidence,
            steps=steps,
            fetched_at=time.monotonic(),
        )
        _cache[org_id] = cfg
        return cfg


async def get_org_instructions(org_id: str) -> str | None:
    """Convenience: just the instructions string (or None)."""
    cfg = await get_org_config(org_id)
    return cfg.ai_instructions


async def get_confidence_thresholds(org_id: str) -> ConfidenceThresholds:
    """Convenience: just the confidence thresholds."""
    cfg = await get_org_config(org_id)
    return cfg.confidence


async def update_confidence_thresholds(
    *,
    org_id: str,
    high: float,
    medium: float,
    block: float | None = None,
) -> ConfidenceThresholds:
    """Persist new thresholds to organizations.metadata and bust the cache.

    Validates ordering + range here as defense-in-depth even though the
    router does the same — anyone calling this directly (CLI, tests) gets
    the same guard. When `block` is omitted we preserve the existing value
    (or default 0.0) so callers that only know about high/medium continue
    to work.
    """
    if not (0.0 <= medium <= high <= 1.0):
        raise ValueError("Thresholds must satisfy 0 <= medium <= high <= 1.")
    if block is not None and not (0.0 <= block <= medium):
        raise ValueError("Block threshold must satisfy 0 <= block <= medium.")

    svc = get_service_client()

    def _run() -> dict[str, Any]:
        existing = (
            svc.table("organizations")
            .select("metadata")
            .eq("id", org_id)
            .maybe_single()
            .execute()
        )
        meta = (existing.data or {}).get("metadata") or {} if existing else {}
        prior = meta.get("confidence_thresholds") if isinstance(meta.get("confidence_thresholds"), dict) else {}
        # If the caller didn't pass `block`, keep what's already on disk so a
        # PUT that only knows about high/medium can't silently clear it.
        resolved_block = (
            block if block is not None else float(prior.get("block", DEFAULT_CONFIDENCE_BLOCK) or 0.0)
        )
        meta = {
            **meta,
            "confidence_thresholds": {
                "high": high,
                "medium": medium,
                "block": resolved_block,
            },
        }
        svc.table("organizations").update({"metadata": meta}).eq("id", org_id).execute()
        return meta

    saved_meta = await asyncio.to_thread(_run)
    invalidate(org_id)
    saved = saved_meta["confidence_thresholds"]
    return ConfidenceThresholds(
        high=saved["high"], medium=saved["medium"], block=saved["block"]
    )


async def get_onboarding_steps(org_id: str) -> OnboardingStepConfig:
    """Convenience: just the onboarding step toggles."""
    cfg = await get_org_config(org_id)
    return cfg.steps


async def update_onboarding_steps(
    *,
    org_id: str,
    bgv: bool | None = None,
    appointment_bundle: bool | None = None,
    policies: bool | None = None,
    induction: bool | None = None,
) -> OnboardingStepConfig:
    """Persist step toggles to organizations.metadata and bust the cache.

    A `None` argument leaves that step's current setting alone, so a caller
    that only knows about one toggle can't silently reset the others. Unlike
    the confidence thresholds there's nothing to validate — the four flags are
    independent booleans with no ordering between them.
    """
    updates = {
        "bgv": bgv,
        "appointment_bundle": appointment_bundle,
        "policies": policies,
        "induction": induction,
    }

    svc = get_service_client()

    def _run() -> dict[str, Any]:
        existing = (
            svc.table("organizations")
            .select("metadata")
            .eq("id", org_id)
            .maybe_single()
            .execute()
        )
        meta = (existing.data or {}).get("metadata") or {} if existing else {}
        prior = meta.get("onboarding_v2_steps")
        prior = prior if isinstance(prior, dict) else {}
        # Read-modify-write rather than a JSONB merge: `metadata` is a shared
        # bucket (archive settings, confidence thresholds, ...) and PostgREST's
        # merge operator isn't exposed through the Python client.
        steps = {
            key: (
                updates[key]
                if updates[key] is not None
                else _as_bool(prior.get(key))
            )
            for key in STEP_KEYS
        }
        meta = {**meta, "onboarding_v2_steps": steps}
        svc.table("organizations").update({"metadata": meta}).eq("id", org_id).execute()
        return meta

    saved_meta = await asyncio.to_thread(_run)
    invalidate(org_id)
    # Writing the key is what marks the org configured — there is no separate
    # flag on disk, so saving even an all-defaults choice dismisses the setup
    # screen, which is the point of letting them accept the defaults.
    return OnboardingStepConfig(**saved_meta["onboarding_v2_steps"], configured=True)
