"""Server-side guard rails for outbound writes (Agent Day 3-4 hardening).

The Day 3-4 write endpoints (Slack post, Notion create-page, Gdocs export,
Gmail send) all share the same set of "is this safe to publish?" questions:

  1. Has the source `messages` row's confidence dropped below the org's
     configured `block` cosine cutoff?
  2. Did the org / user watchlist match a competitor name in the content,
     and did the user acknowledge the warning?
  3. Did the moderation filter flag PII / profanity in the content?
  4. Is this user (or org) over their per-channel rate limit?

Each individual router used to either skip these checks entirely or do them
inconsistently. This module centralizes them so a new outbound destination
only has to call `enforce_outbound_write_guards(...)` once.

Failures raise typed exceptions that map cleanly to HTTP status codes in
the routers — the router decides whether to surface a 4xx or queue anyway.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Literal

from app.database import get_service_client
from app.services.competitor_detector import (
    detect_competitors,
    get_competitor_terms,
)
from app.services.org_config import get_confidence_thresholds
from app.services.rate_limit import _sliding_window_check

log = logging.getLogger(__name__)


OutboundChannel = Literal["slack", "gmail", "notion", "gdocs"]


# Per-user-per-channel rate windows. Calibrated against typical human cadence
# (a few sends per hour) with enough headroom that a copy-paste batch of 5-10
# pushes stays within bounds. The org-wide caps below catch scripted abuse.
RATE_LIMITS: dict[OutboundChannel, dict[str, int]] = {
    "slack": {"per_user_hour": 30, "per_org_day": 500},
    "gmail": {"per_user_hour": 20, "per_org_day": 300},
    "notion": {"per_user_hour": 20, "per_org_day": 200},
    "gdocs": {"per_user_hour": 20, "per_org_day": 200},
}


class OutboundGateError(Exception):
    """Base — carries an HTTP status code + a stable machine-readable detail.

    Routers translate this into HTTPException(status, detail=code). The
    frontend pattern-matches on `detail` to render the right inline message.
    """

    status_code: int = 400
    code: str = "outbound_blocked"

    def __init__(self, message: str | None = None, **extra: Any) -> None:
        super().__init__(message or self.code)
        self.extra = extra


class ConfidenceBelowBlock(OutboundGateError):
    status_code = 403
    code = "confidence_below_block"


class CompetitorMatchNotAcknowledged(OutboundGateError):
    status_code = 422
    code = "competitor_match_unacknowledged"


class WriteRateLimited(OutboundGateError):
    status_code = 429
    code = "outbound_rate_limited"


@dataclass(frozen=True)
class GuardOutcome:
    """What the gate observed — useful for the audit trail row."""

    competitor_terms_matched: tuple[str, ...]
    confidence_score: float | None
    confidence_block_threshold: float


async def _load_message_confidence(
    *, message_id: str, org_id: str
) -> tuple[float | None, str | None]:
    """Return (cosine_average, level) from messages.metadata.confidence.

    score is stored as cosine*10 (see chat.py `_persist_assistant_message`),
    so we divide back here. Returning None for either is a no-op signal —
    the caller treats "unknown confidence" as "don't block on confidence",
    which preserves the v1 behavior for messages that pre-date the feature.
    """
    svc = get_service_client()
    try:
        result = await asyncio.to_thread(
            lambda: svc.table("messages")
            .select("metadata")
            .eq("id", message_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        log.warning("outbound_gate_msg_load_failed id=%s err=%s", message_id, exc)
        return None, None
    if not result or not result.data:
        return None, None
    metadata = result.data.get("metadata") or {}
    confidence = metadata.get("confidence") or {}
    score = confidence.get("score")
    level = confidence.get("level")
    if score is None:
        return None, level if isinstance(level, str) else None
    try:
        cosine_avg = float(score) / 10.0
    except (TypeError, ValueError):
        return None, level if isinstance(level, str) else None
    return cosine_avg, level if isinstance(level, str) else None


async def _check_rate_limit(
    *, channel: OutboundChannel, user_id: str, org_id: str
) -> None:
    limits = RATE_LIMITS[channel]
    # Per-user-per-hour first — the smaller bucket fails fastest and protects
    # us from a runaway client more than the wider org cap does.
    user_window = await _sliding_window_check(
        namespace=f"outbound:{channel}:user_hour",
        identifier=f"{org_id}:{user_id}",
        limit=limits["per_user_hour"],
        window_seconds=3600,
    )
    if not user_window.allowed:
        raise WriteRateLimited(
            f"{channel}_user_hour_limit",
            retry_after=user_window.reset_seconds,
            scope="user_hour",
        )

    org_window = await _sliding_window_check(
        namespace=f"outbound:{channel}:org_day",
        identifier=org_id,
        limit=limits["per_org_day"],
        window_seconds=86_400,
    )
    if not org_window.allowed:
        raise WriteRateLimited(
            f"{channel}_org_day_limit",
            retry_after=org_window.reset_seconds,
            scope="org_day",
        )


async def _check_confidence(
    *, message_id: str, org_id: str
) -> tuple[float | None, float]:
    """Returns (avg_cosine, block_threshold). Raises if avg < block."""
    thresholds = await get_confidence_thresholds(org_id)
    if thresholds.block <= 0.0:
        # Block tier disabled — admin hasn't opted in.
        return None, 0.0

    cosine_avg, _level = await _load_message_confidence(
        message_id=message_id, org_id=org_id
    )
    if cosine_avg is None:
        # Confidence unknown (pre-feature message, etc). Be conservative AND
        # not surprising: only block if the org has actively raised the
        # threshold above 0. Since we already returned above when block<=0,
        # we're here only when block > 0 — treat "unknown" as "let through"
        # rather than break old conversations. Document this clearly.
        return None, thresholds.block

    if cosine_avg < thresholds.block:
        raise ConfidenceBelowBlock(
            "message_confidence_below_block",
            cosine_avg=cosine_avg,
            block_threshold=thresholds.block,
        )
    return cosine_avg, thresholds.block


async def _check_competitor(
    *, content: str, org_id: str, user_id: str | None, acknowledged: bool
) -> tuple[str, ...]:
    """Scan content for competitor mentions; raise if matches AND not acked."""
    try:
        terms = await get_competitor_terms(org_id=org_id, user_id=user_id)
    except Exception as exc:
        log.warning("outbound_gate_competitor_terms_failed: %s", exc)
        return ()
    if not terms.has_any:
        return ()
    matches = detect_competitors(
        content,
        org_terms=terms.org_terms,
        user_terms=terms.user_terms,
    )
    matched_words = tuple(sorted({m.term for m in matches}))
    if matched_words and not acknowledged:
        raise CompetitorMatchNotAcknowledged(
            "competitor_terms_matched",
            terms=list(matched_words),
        )
    return matched_words


async def enforce_outbound_write_guards(
    *,
    channel: OutboundChannel,
    org_id: str,
    user_id: str,
    message_id: str,
    content: str,
    competitor_acknowledged: bool,
) -> GuardOutcome:
    """One-stop gate for the four outbound destinations.

    Order matters: rate-limit check first (cheapest, drops bad actors before
    we touch Redis-heavier paths), then confidence (one DB read), then the
    competitor watchlist. Each raises on failure; routers catch
    OutboundGateError and emit the appropriate HTTP status.

    Note: we deliberately do NOT run `moderation.moderate_input` here. That
    module catches *prompt injection in user queries*, not unsafe outbound
    content — its patterns would false-positive on legitimate assistant
    answers that happen to discuss the topic. If we ever want true outbound
    PII/profanity scanning, it belongs in a new module with different rules.
    """
    await _check_rate_limit(channel=channel, user_id=user_id, org_id=org_id)
    cosine_avg, block_threshold = await _check_confidence(
        message_id=message_id, org_id=org_id
    )
    matched_terms = await _check_competitor(
        content=content,
        org_id=org_id,
        user_id=user_id,
        acknowledged=competitor_acknowledged,
    )
    return GuardOutcome(
        competitor_terms_matched=matched_terms,
        confidence_score=cosine_avg,
        confidence_block_threshold=block_threshold,
    )
