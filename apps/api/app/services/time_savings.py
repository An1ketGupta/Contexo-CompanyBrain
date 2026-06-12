"""V5 #73 — Per-query time-saved estimate, keyed on the intent classifier.

The intent classifier already runs on every chat turn (see `services/intent.py`)
and the resulting QueryIntent ends up on the assistant message's metadata. So
we get the time-saved estimate for free by mapping intent → minutes, with a
small response-length bump for task_generation outputs that produced a long
artifact.

The numbers are deliberately conservative; we'd rather under-count and let
customers say "we save more than that" than over-promise. See ARCHITECTURE.md
for the source — distilled from internal interviews:

    factual_qa       =  20 min  (policy Q&A — manual lookup, re-reading)
    task_generation  =  30 min  (default output — email, short message)
    analysis         =  45 min  (multi-source synthesis)
    search           =  10 min  (quick file find)

Task generation gets bumped to 60/120 minutes when the artifact is long
enough that it's clearly a job description, runbook, or full document:

    >  400 chars  → 30 min   (one-liner / short email)
    >  900 chars  → 60 min   (paragraph email or short doc)
    > 1500 chars  → 120 min  (job description, multi-section doc)
"""
from __future__ import annotations

import logging
from typing import Final

log = logging.getLogger(__name__)

_BASE_MINUTES: Final[dict[str, int]] = {
    "factual_qa": 20,
    "task_generation": 30,
    "analysis": 45,
    "search": 10,
}
_DEFAULT_MINUTES = 10

_TASK_GEN_LONG_DOC_CHARS = 1_500
_TASK_GEN_PARAGRAPH_CHARS = 900
_TASK_GEN_SHORT_CHARS = 400


def estimate_minutes(*, intent: str | None, response_length: int) -> int:
    """Return whole-minute time-saved estimate for one assistant turn.

    `intent` is the classifier output. `response_length` is `len(text)` of the
    assistant message. Robust to None (returns the default) so a misconfigured
    classifier never zeroes-out the org's analytics.
    """
    intent_key = (intent or "").strip()
    base = _BASE_MINUTES.get(intent_key, _DEFAULT_MINUTES)

    if intent_key == "task_generation":
        if response_length >= _TASK_GEN_LONG_DOC_CHARS:
            return 120
        if response_length >= _TASK_GEN_PARAGRAPH_CHARS:
            return 60
        if response_length >= _TASK_GEN_SHORT_CHARS:
            return base  # 30 — paragraph-sized email/announcement
        # Tiny output — likely a one-liner reply; don't credit a full email.
        return max(10, base // 2)

    return base
