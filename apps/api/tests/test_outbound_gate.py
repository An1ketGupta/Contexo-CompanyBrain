"""Unit tests for the outbound-write safety gate (Agent Day 3-4 hardening).

The gate composes four independent checks; we test each one in isolation
plus the happy-path passthrough. External services (Supabase, Upstash,
competitor_detector cache) are stubbed at the function boundary so these
tests stay hermetic and run in <100ms.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.services import outbound_gate
from app.services.competitor_detector import CompetitorMatch, CompetitorTerms
from app.services.org_config import ConfidenceThresholds
from app.services.rate_limit import SlidingWindowResult


ORG_ID = "00000000-0000-0000-0000-000000000001"
USER_ID = "00000000-0000-0000-0000-000000000002"
MSG_ID = "00000000-0000-0000-0000-000000000003"


@dataclass
class _Stubs:
    thresholds: ConfidenceThresholds = ConfidenceThresholds.default()
    msg_confidence: tuple[float | None, str | None] = (None, None)
    terms: CompetitorTerms = CompetitorTerms(org_terms=(), user_terms=())
    matches: tuple[CompetitorMatch, ...] = ()
    rate_user_allowed: bool = True
    rate_org_allowed: bool = True


def _install(monkeypatch: pytest.MonkeyPatch, stubs: _Stubs) -> None:
    async def _thresholds(_org_id: str) -> ConfidenceThresholds:
        return stubs.thresholds

    async def _load_conf(*, message_id: str, org_id: str):
        return stubs.msg_confidence

    async def _terms(*, org_id: str, user_id: str | None) -> CompetitorTerms:
        return stubs.terms

    def _detect(content: str, *, org_terms, user_terms):
        return list(stubs.matches)

    async def _window(*, namespace: str, identifier: str, limit: int, window_seconds: int) -> SlidingWindowResult:
        if namespace.endswith("user_hour"):
            allowed = stubs.rate_user_allowed
        else:
            allowed = stubs.rate_org_allowed
        return SlidingWindowResult(
            allowed=allowed, remaining=0 if not allowed else limit, reset_seconds=window_seconds
        )

    monkeypatch.setattr(outbound_gate, "get_confidence_thresholds", _thresholds)
    monkeypatch.setattr(outbound_gate, "_load_message_confidence", _load_conf)
    monkeypatch.setattr(outbound_gate, "get_competitor_terms", _terms)
    monkeypatch.setattr(outbound_gate, "detect_competitors", _detect)
    monkeypatch.setattr(outbound_gate, "_sliding_window_check", _window)


# ── Happy path ─────────────────────────────────────────────────────────────


async def test_happy_path_lets_message_through(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _Stubs())
    outcome = await outbound_gate.enforce_outbound_write_guards(
        channel="slack",
        org_id=ORG_ID,
        user_id=USER_ID,
        message_id=MSG_ID,
        content="hello world",
        competitor_acknowledged=False,
    )
    assert outcome.competitor_terms_matched == ()
    assert outcome.confidence_block_threshold == 0.0


# ── Confidence block tier ──────────────────────────────────────────────────


async def test_block_tier_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # Default org config has block=0; gate should pass regardless of score.
    stubs = _Stubs(msg_confidence=(0.05, "low"))
    _install(monkeypatch, stubs)
    outcome = await outbound_gate.enforce_outbound_write_guards(
        channel="slack",
        org_id=ORG_ID,
        user_id=USER_ID,
        message_id=MSG_ID,
        content="hi",
        competitor_acknowledged=False,
    )
    assert outcome.confidence_score is None  # short-circuited; block tier off


async def test_block_tier_rejects_low_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    stubs = _Stubs(
        thresholds=ConfidenceThresholds(high=0.75, medium=0.45, block=0.30),
        msg_confidence=(0.20, "low"),
    )
    _install(monkeypatch, stubs)
    with pytest.raises(outbound_gate.ConfidenceBelowBlock) as exc_info:
        await outbound_gate.enforce_outbound_write_guards(
            channel="gmail",
            org_id=ORG_ID,
            user_id=USER_ID,
            message_id=MSG_ID,
            content="hi",
            competitor_acknowledged=False,
        )
    assert exc_info.value.code == "confidence_below_block"
    assert exc_info.value.status_code == 403


async def test_block_tier_accepts_at_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    stubs = _Stubs(
        thresholds=ConfidenceThresholds(high=0.75, medium=0.45, block=0.30),
        msg_confidence=(0.30, "medium"),
    )
    _install(monkeypatch, stubs)
    # Exactly at the threshold passes (>= semantics).
    outcome = await outbound_gate.enforce_outbound_write_guards(
        channel="notion",
        org_id=ORG_ID,
        user_id=USER_ID,
        message_id=MSG_ID,
        content="hi",
        competitor_acknowledged=False,
    )
    assert outcome.confidence_score == 0.30


async def test_block_tier_passes_when_confidence_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pre-feature messages have no confidence in metadata; we let them through
    # rather than break existing conversations.
    stubs = _Stubs(
        thresholds=ConfidenceThresholds(high=0.75, medium=0.45, block=0.50),
        msg_confidence=(None, None),
    )
    _install(monkeypatch, stubs)
    outcome = await outbound_gate.enforce_outbound_write_guards(
        channel="gdocs",
        org_id=ORG_ID,
        user_id=USER_ID,
        message_id=MSG_ID,
        content="hi",
        competitor_acknowledged=False,
    )
    assert outcome.confidence_score is None


# ── Competitor watchlist ───────────────────────────────────────────────────


async def test_competitor_match_blocks_without_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    stubs = _Stubs(
        terms=CompetitorTerms(org_terms=("acme",), user_terms=()),
        matches=(CompetitorMatch(term="Acme", source="org", count=1, snippet="..."),),
    )
    _install(monkeypatch, stubs)
    with pytest.raises(outbound_gate.CompetitorMatchNotAcknowledged) as exc_info:
        await outbound_gate.enforce_outbound_write_guards(
            channel="slack",
            org_id=ORG_ID,
            user_id=USER_ID,
            message_id=MSG_ID,
            content="Acme is winning",
            competitor_acknowledged=False,
        )
    assert exc_info.value.code == "competitor_match_unacknowledged"
    assert exc_info.value.status_code == 422
    assert exc_info.value.extra.get("terms") == ["Acme"]


async def test_competitor_match_allowed_with_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    stubs = _Stubs(
        terms=CompetitorTerms(org_terms=("acme",), user_terms=()),
        matches=(CompetitorMatch(term="Acme", source="org", count=1, snippet="..."),),
    )
    _install(monkeypatch, stubs)
    outcome = await outbound_gate.enforce_outbound_write_guards(
        channel="slack",
        org_id=ORG_ID,
        user_id=USER_ID,
        message_id=MSG_ID,
        content="Acme is winning",
        competitor_acknowledged=True,
    )
    assert outcome.competitor_terms_matched == ("Acme",)


async def test_no_competitor_terms_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    # Orgs without a watchlist must never see a competitor check fire.
    stubs = _Stubs(terms=CompetitorTerms(org_terms=(), user_terms=()))
    _install(monkeypatch, stubs)
    outcome = await outbound_gate.enforce_outbound_write_guards(
        channel="slack",
        org_id=ORG_ID,
        user_id=USER_ID,
        message_id=MSG_ID,
        content="literally anything",
        competitor_acknowledged=False,
    )
    assert outcome.competitor_terms_matched == ()


# ── Rate limit ─────────────────────────────────────────────────────────────


async def test_user_hour_rate_limit_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _Stubs(rate_user_allowed=False))
    with pytest.raises(outbound_gate.WriteRateLimited) as exc_info:
        await outbound_gate.enforce_outbound_write_guards(
            channel="slack",
            org_id=ORG_ID,
            user_id=USER_ID,
            message_id=MSG_ID,
            content="x",
            competitor_acknowledged=False,
        )
    assert exc_info.value.status_code == 429
    assert exc_info.value.extra.get("scope") == "user_hour"


async def test_org_day_rate_limit_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _Stubs(rate_org_allowed=False))
    with pytest.raises(outbound_gate.WriteRateLimited) as exc_info:
        await outbound_gate.enforce_outbound_write_guards(
            channel="gmail",
            org_id=ORG_ID,
            user_id=USER_ID,
            message_id=MSG_ID,
            content="x",
            competitor_acknowledged=False,
        )
    assert exc_info.value.extra.get("scope") == "org_day"


# ── Order of operations ────────────────────────────────────────────────────


async def test_rate_limit_runs_before_db_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rate limit failures should short-circuit before we touch Postgres so
    that abusive clients can't run up our DB read budget."""
    db_called = {"value": False}

    async def _load_conf(*, message_id: str, org_id: str):
        db_called["value"] = True
        return (None, None)

    _install(monkeypatch, _Stubs(rate_user_allowed=False))
    monkeypatch.setattr(outbound_gate, "_load_message_confidence", _load_conf)

    with pytest.raises(outbound_gate.WriteRateLimited):
        await outbound_gate.enforce_outbound_write_guards(
            channel="slack",
            org_id=ORG_ID,
            user_id=USER_ID,
            message_id=MSG_ID,
            content="x",
            competitor_acknowledged=False,
        )
    assert not db_called["value"]
