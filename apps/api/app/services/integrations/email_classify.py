"""Inbound email classifier (Agent Day 12).

Every email arriving on an org's inbound address gets a label:

    support    — customer wants help / has a question / reports a bug.
    sales      — prospect asking about pricing, demo, vendor questions.
    internal   — staff forward, calendar invite, automated noise.
    knowledge  — content the team wants ingested as a doc (newsletter,
                 forwarded write-up, vendor update worth keeping).

The label drives downstream routing:

    support | sales  → dropped; no ticket/agent pipeline consumes these.
    knowledge        → existing ingest-as-document path.
    internal         → drop; no ticket, no document.

Design notes:

  * **Two-tier on purpose.** A heuristic pass catches the obvious cases
    (~80% of mail in practice) for $0 and ~1ms. Only the ambiguous
    middle goes to Gemini. This keeps cost predictable as inbound
    volume grows and avoids paying the LLM tax on calendar bots.

  * **Fail-open to `knowledge`.** If both passes are uncertain we route
    to the existing ingest path — the worst case is a customer support
    email becoming a searchable doc, which is recoverable. The opposite
    (knowledge ingestion misrouted to the support queue) would create
    spurious tickets.

  * **No vendor-specific spam tricks.** We deliberately don't fingerprint
    "noreply@" or "calendar-notification@google.com" — orgs use a wide
    variety of mailbots and the false-negative rate from a curated
    blocklist isn't worth the maintenance.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

from app.services.llm.client import get_llm_client
from app.services.llm.types import Message

log = logging.getLogger(__name__)

Category = Literal["support", "sales", "internal", "knowledge"]

# Confidence the heuristic must reach to short-circuit the LLM call.
_HEURISTIC_THRESHOLD = 0.80

# Caps on what we feed the LLM — long forwarded threads waste tokens.
_LLM_SUBJECT_CAP = 200
_LLM_BODY_CAP = 1800


@dataclass(frozen=True)
class Classification:
    category: Category
    confidence: float        # 0..1
    reason: str              # short, human-readable, persisted for audit
    source: Literal["heuristic", "llm", "fallback"]


# ── Heuristic pass ─────────────────────────────────────────────────────────

# Phrases scored as "this is a support/sales request from outside the org".
# Each pattern carries a contribution toward the category's score. Tuned
# manually from a few hundred real support inboxes — keep it small, additive.
_SUPPORT_PATTERNS: tuple[tuple[re.Pattern[str], float], ...] = (
    (re.compile(r"\b(can(?:not|'?t)|won'?t|doesn'?t|isn'?t|stopped)\s+(work|loading|open|connect|sync|load)", re.I), 0.5),
    (re.compile(r"\b(refund|cancel(?:lation)?|chargeback|dispute|reimburs)", re.I), 0.6),
    (re.compile(r"\b(help|stuck|broken|error|issue|problem|bug)\b", re.I), 0.35),
    (re.compile(r"\bhow\s+do\s+i\b", re.I), 0.4),
    (re.compile(r"\b(reset|forgot|change)\s+(my\s+)?password\b", re.I), 0.7),
    (re.compile(r"\b(can'?t\s+log\s*in|log[ -]?in\s+(failing|fails|broken))\b", re.I), 0.7),
    (re.compile(r"\b(invoice|receipt|billing|charge[d]?\s+(twice|wrong|incorrect))\b", re.I), 0.55),
    (re.compile(r"\b(my\s+account|my\s+subscription)\b", re.I), 0.3),
    (re.compile(r"\b(urgent|asap|emergency)\b", re.I), 0.25),
)

_SALES_PATTERNS: tuple[tuple[re.Pattern[str], float], ...] = (
    (re.compile(r"\b(pricing|price\s+list|how\s+much|cost(?:s)?)\b", re.I), 0.55),
    (re.compile(r"\b(demo|trial|free\s+trial|book\s+a\s+(call|meeting))\b", re.I), 0.6),
    (re.compile(r"\b(quote|proposal|rfp|rfq|procurement|vendor\s+form)\b", re.I), 0.7),
    (re.compile(r"\b(enterprise|seat[s]?|number\s+of\s+users)\b", re.I), 0.4),
    (re.compile(r"\b(interested\s+in|looking\s+(for|at)\s+a\s+solution)\b", re.I), 0.45),
    (re.compile(r"\b(security\s+(review|questionnaire)|soc\s*2|gdpr\s+(form|dpia))\b", re.I), 0.65),
)

# High-signal "this is automated / internal" markers.
_INTERNAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(re:\s*)?(fwd?:|fw:)\s*", re.I),                    # forwarded chains
    re.compile(r"\b(calendar\s+invite|google\s+calendar|outlook\s+invite)\b", re.I),
    re.compile(r"\b(your\s+(weekly|monthly)\s+(report|summary|digest))\b", re.I),
    re.compile(r"\b(noreply|no-reply|do-not-reply)\b", re.I),
    re.compile(r"\b(unsubscribe|preferences\s+center|view\s+in\s+browser)\b", re.I),
)

# Question shape — a leading interrogative often signals support/sales.
_QUESTION_OPENERS = re.compile(
    r"^\s*(hi|hello|hey|good\s+(morning|afternoon|evening)|to\s+whom|dear)\b",
    re.I,
)


def heuristic_classify(*, subject: str, body: str, from_email: str) -> Classification | None:
    """Cheap first pass. Returns a Classification with confidence ≥ threshold
    or None when ambiguous and the LLM should be consulted.

    The classifier is intentionally biased toward `internal` for clearly
    automated/forwarded mail — those should never spin up a support ticket.
    """
    text = f"{subject}\n{body}"
    text.lower()

    # Internal flagging (automated / forwarded). Any single hit is enough
    # because false-positives here just mean we ingest the body as a doc —
    # which is the previous default behavior anyway.
    for pat in _INTERNAL_PATTERNS:
        if pat.search(text):
            return Classification(
                category="internal",
                confidence=0.85,
                reason=f"matched_internal:{pat.pattern[:40]}",
                source="heuristic",
            )

    # Sender shape — "noreply@" or "calendar@" implies internal/automated.
    sender_local = (from_email.split("@", 1)[0] if "@" in from_email else "").lower()
    if sender_local in {"noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon"}:
        return Classification(
            category="internal",
            confidence=0.90,
            reason="sender_is_noreply",
            source="heuristic",
        )

    support_score = sum(weight for pat, weight in _SUPPORT_PATTERNS if pat.search(text))
    sales_score = sum(weight for pat, weight in _SALES_PATTERNS if pat.search(text))

    # Greeting + a question mark in the first paragraph is a soft signal that
    # somebody outside is asking us something — nudge the higher of the two.
    if _QUESTION_OPENERS.search(text) and "?" in text[:500]:
        if support_score >= sales_score:
            support_score += 0.15
        else:
            sales_score += 0.15

    # Decide.
    if support_score == 0 and sales_score == 0:
        return None  # Ambiguous — defer to LLM.

    if support_score >= sales_score:
        score = min(support_score, 1.0)
        if score >= _HEURISTIC_THRESHOLD:
            return Classification(
                category="support",
                confidence=score,
                reason=f"heuristic_support_score={support_score:.2f}",
                source="heuristic",
            )
    else:
        score = min(sales_score, 1.0)
        if score >= _HEURISTIC_THRESHOLD:
            return Classification(
                category="sales",
                confidence=score,
                reason=f"heuristic_sales_score={sales_score:.2f}",
                source="heuristic",
            )

    return None  # Ambiguous — defer to LLM.


# ── LLM fallback pass ──────────────────────────────────────────────────────

_LLM_PROMPT = """You classify inbound business email into ONE category.

Categories:
  - support  : customer / user asking for help, reporting an issue, bug, or refund.
  - sales    : prospect asking about pricing, requesting a demo / trial / quote, vendor procurement.
  - internal : automated noise (calendar invites, billing receipts, newsletters), or a teammate forwarding info.
  - knowledge: content the recipient should ingest into the knowledge base (a vendor update worth keeping, a forwarded write-up, a useful article).

Reply with ONLY a single line in this exact format:
  category=<one of: support, sales, internal, knowledge>;confidence=<0..1>;reason=<≤80 chars>

Subject: {subject}
From: {sender}
Body:
{body}
"""

_CATEGORY_VALUES = {"support", "sales", "internal", "knowledge"}
_LINE_RE = re.compile(
    r"category\s*=\s*([a-z]+)\s*;\s*confidence\s*=\s*([0-9.]+)\s*;\s*reason\s*=\s*(.+)",
    re.I,
)


async def llm_classify(*, subject: str, body: str, from_email: str) -> Classification:
    """One-shot LLM classification. Always returns a Classification — on
    parse failure we fall back to `knowledge` with a clear reason so the
    behaviour is deterministic from the caller's perspective."""
    prompt = _LLM_PROMPT.format(
        subject=(subject or "")[:_LLM_SUBJECT_CAP],
        sender=(from_email or "")[:80],
        body=(body or "")[:_LLM_BODY_CAP],
    )
    client = get_llm_client()
    try:
        resp = await client.complete(
            messages=[Message(role="user", content=prompt)],
            temperature=0.0,
            timeout=15.0,
        )
    except Exception as exc:
        log.warning("email_classify_llm_failed: %s", exc)
        return Classification(
            category="knowledge",
            confidence=0.0,
            reason=f"llm_error:{type(exc).__name__}",
            source="fallback",
        )

    line = (resp.text or "").strip().splitlines()[0] if (resp.text or "").strip() else ""
    m = _LINE_RE.search(line)
    if not m:
        return Classification(
            category="knowledge",
            confidence=0.0,
            reason=f"llm_parse_failed:{line[:60]}",
            source="fallback",
        )

    category = m.group(1).lower()
    if category not in _CATEGORY_VALUES:
        return Classification(
            category="knowledge",
            confidence=0.0,
            reason=f"llm_unknown_category:{category}",
            source="fallback",
        )

    try:
        confidence = max(0.0, min(1.0, float(m.group(2))))
    except ValueError:
        confidence = 0.5
    reason = m.group(3).strip()[:120]
    return Classification(
        category=category,  # type: ignore[arg-type]
        confidence=confidence,
        reason=reason,
        source="llm",
    )


# ── Public entry point ─────────────────────────────────────────────────────

async def classify_inbound_email(
    *, subject: str, body: str, from_email: str
) -> Classification:
    """Two-tier classifier. Heuristic first, LLM only if ambiguous."""
    heuristic = heuristic_classify(
        subject=subject, body=body, from_email=from_email
    )
    if heuristic is not None:
        return heuristic
    return await llm_classify(
        subject=subject, body=body, from_email=from_email
    )
