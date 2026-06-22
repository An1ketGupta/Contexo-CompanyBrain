"""Content moderation (V4 #79).

Layered defence against prompt injection and obvious off-policy abuse. Runs
as STEP 0 in the chat pipeline — BEFORE any retrieval or LLM call — so a
blocked query never spends a token.

Design choices:
  * Pure-regex catalog, no LLM judge call. Keyword + regex is fast (sub-ms),
    deterministic, free, and tunable without a deploy if we ever surface the
    catalog as config.
  * Two severity levels:
        BLOCKED  — definite injection attempt; refuse and log.
        FLAGGED  — suspicious but plausible legitimate use; log AND proceed.
  * `query_text` in the moderation_logs row is truncated to 500 chars — long
    enough to triage, short enough to keep the table tiny.
  * Never raises from `log_moderation_event` — moderation is a guard, not a
    SPOF.

The pattern list is *intentionally* not exhaustive. The goal is to catch
the obvious "ignore all previous instructions" / "you are now DAN" attacks
without turning into a brittle wack-a-mole. Genuine misuse goes through
LLM-level safety on top.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from enum import Enum
from re import Pattern

from app.database import get_service_client

log = logging.getLogger(__name__)

QUERY_TEXT_LOG_CAP = 500


class ModerationResult(str, Enum):
    CLEAN = "clean"
    FLAGGED = "flagged"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ModerationDecision:
    result: ModerationResult
    reason: str | None
    matched_pattern: str | None


# ── Injection patterns — BLOCK ───────────────────────────────────────────────
# Each entry is (label, regex source). The label is what we record in
# moderation_logs.reason; users never see it.
_INJECTION_SOURCES: list[tuple[str, str]] = [
    (
        "ignore_previous_instructions",
        r"\bignore\s+(all\s+|the\s+|your\s+|any\s+)?"
        r"(previous|prior|above|earlier|preceding)\s+"
        r"(instructions?|prompts?|rules?|context|messages?)",
    ),
    (
        "disregard_instructions",
        r"\b(disregard|forget|override)\s+(your|the|all|any)\s+"
        r"(instructions?|system\s+prompt|rules?|guidelines?)",
    ),
    (
        "roleplay_jailbreak",
        r"\b(you\s+are\s+(now\s+)?|act\s+as|pretend\s+to\s+be|"
        r"roleplay\s+as)\s+(a\s+|an\s+)?"
        r"(different|evil|unrestricted|jailbroken|unfiltered|"
        r"uncensored|amoral|unethical)\b",
    ),
    (
        "developer_mode",
        r"\b(developer|admin|root|sudo|god)\s+mode\b",
    ),
    (
        "dan_jailbreak",
        r"\bDAN\s+(mode|prompt|jailbreak)\b",
    ),
    (
        "new_system_prompt",
        r"\b(new|updated|revised|replace(?:d|ment)?)\s+"
        r"(system\s+prompt|system\s+message|instructions?|rules?)\s*[:=]",
    ),
    (
        "bypass_safety",
        r"\bbypass\s+(your|all|the)?\s*"
        r"(filter|restriction|safety|guideline|policy)",
    ),
    (
        "reveal_system_prompt",
        r"\b(print|output|reveal|show|repeat|expose|leak)\s+"
        r"(your|the)\s+(system\s+prompt|instructions?|training\s+data|"
        r"hidden\s+(prompt|instructions?))",
    ),
    (
        "translation_evasion",
        r"\btranslate.*\b(to|into)\b.*\b(then|and)\b.*\bback\b",
    ),
    (
        "base64_evasion",
        r"\bbase64.{0,30}\b(decode|encode)\b",
    ),
]

# ── Off-policy heuristics — FLAG only ────────────────────────────────────────
# Plausible legitimate intent so we don't block, but we log so admins can
# spot abuse patterns building up.
_SUSPICIOUS_SOURCES: list[tuple[str, str]] = [
    (
        "hacking_howto",
        r"\b(how\s+to|steps?\s+to|guide\s+to)\b.{0,40}"
        r"\b(hack|exploit|break\s+into|bypass|crack)\b",
    ),
    (
        "malware_request",
        r"\b(generate|write|create|produce|build)\b.{0,40}"
        r"\b(malware|virus|exploit|ransomware|backdoor|rootkit|keylogger)\b",
    ),
    (
        "personal_data_request",
        r"\b(personal|private|confidential|sensitive)\s+"
        r"(data|information|details)\b.{0,40}"
        r"\b(of|about|on)\b.{0,40}"
        r"\b(users?|employees?|customers?|members?)\b",
    ),
]


def _compile(items: list[tuple[str, str]]) -> list[tuple[str, Pattern[str]]]:
    return [(name, re.compile(src, re.IGNORECASE | re.UNICODE)) for name, src in items]


_INJECTION_PATTERNS = _compile(_INJECTION_SOURCES)
_SUSPICIOUS_PATTERNS = _compile(_SUSPICIOUS_SOURCES)


def moderate_input(query: str) -> ModerationDecision:
    """Run the rule catalog against `query`. Pure; never raises.

    BLOCKED wins over FLAGGED if both would match — the injection signal is
    stronger.
    """
    q = (query or "").strip()
    if not q:
        return ModerationDecision(ModerationResult.CLEAN, None, None)

    for name, pattern in _INJECTION_PATTERNS:
        if pattern.search(q):
            return ModerationDecision(
                ModerationResult.BLOCKED,
                f"Prompt injection: {name}",
                name,
            )

    for name, pattern in _SUSPICIOUS_PATTERNS:
        if pattern.search(q):
            return ModerationDecision(
                ModerationResult.FLAGGED,
                f"Suspicious pattern: {name}",
                name,
            )

    return ModerationDecision(ModerationResult.CLEAN, None, None)


async def log_moderation_event(
    *,
    org_id: str,
    user_id: str | None,
    query: str,
    decision: ModerationDecision,
    action_taken: str,
) -> None:
    """Insert one moderation_logs row. Never raises."""
    if decision.result not in (ModerationResult.BLOCKED, ModerationResult.FLAGGED):
        return
    try:
        svc = get_service_client()
        truncated = (query or "")[:QUERY_TEXT_LOG_CAP]
        await asyncio.to_thread(
            lambda: svc.table("moderation_logs")
            .insert({
                "org_id": org_id,
                "user_id": user_id,
                "query_text": truncated,
                "result": decision.result.value,
                "reason": decision.reason,
                "action_taken": action_taken,
            })
            .execute()
        )
    except Exception as exc:
        log.warning("log_moderation_event failed: %s", exc)
