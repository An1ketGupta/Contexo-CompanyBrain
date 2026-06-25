"""V3 Day 4 #51 — keyword-driven query intent classification.

Four intents power the system-prompt switch in `execute_task`:

  * task_generation — "write/draft/create/compose" outputs the user will paste somewhere.
  * analysis        — "analyze/compare/summarize" structured breakdowns.
  * search          — "find/where is/which document" surface-the-fact requests.
  * factual_qa      — the default; what/who/when/how interrogatives and policy lookups.

Why keyword matching and not an LLM call?
  * Latency: this runs on every chat turn before any retrieval; an extra LLM
    hop would add 200-400ms to the first byte.
  * Cost: classifying every turn doubles our Gemini bill for ~0 quality win.
  * Auditability: a regex either matches or doesn't — easy to debug a
    misclassification by reading the same patterns the prod code uses.

The classifier returns the intent label *and* the patterns that matched, so
the orchestrator can attach the matched-keywords as Langfuse metadata for
later quality review.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class QueryIntent(str, Enum):
    FACTUAL_QA = "factual_qa"
    TASK_GENERATION = "task_generation"
    ANALYSIS = "analysis"
    SEARCH = "search"
    # Production Roadmap 1.9 — Time-boxed Quick Answer. A specialization of
    # FACTUAL_QA for short, single-fact lookups ("what's our holiday policy",
    # "who owns the API runbook"). Drives the orchestrator to cap tool rounds
    # and search count for sub-2s perceived latency.
    QUICK_ANSWER = "quick_answer"


# Order matters — task_generation wins over analysis if both match, because
# "summarize and write" is fundamentally a write task. QUICK_ANSWER runs
# AFTER task_generation/analysis (a "write a summary of vacation policy" is
# still a task, not a lookup) but BEFORE the broader factual_qa so a short
# policy question maps to a bounded turn instead of full-depth retrieval.
_INTENT_PATTERNS: dict[QueryIntent, tuple[re.Pattern[str], ...]] = {
    QueryIntent.TASK_GENERATION: tuple(
        re.compile(p, re.IGNORECASE)
        for p in (
            r"\b(write|draft|compose|craft|prepare|generate|produce|format)\b",
            # "make a/an X" where X is something we'd actually generate.
            r"\bmake\s+(a|an|the)?\s*(email|message|doc(ument)?|template|jd|"
            r"description|announcement|post|reply|response|memo|brief)\b",
            r"\b(create|build|put together)\s+(a|an|the)?\s*(email|message|"
            r"doc(ument)?|template|jd|description|announcement|post|reply|"
            r"response|memo|brief|outline)\b",
            r"\b(rewrite|reword|paraphrase|polish|edit)\b",
        )
    ),
    QueryIntent.ANALYSIS: tuple(
        re.compile(p, re.IGNORECASE)
        for p in (
            r"\b(analyz|analys)e\b",
            r"\bcompare\b",
            r"\b(summari[sz]e|summary of|tl;?dr)\b",
            r"\b(break ?down|breakdown)\b",
            r"\b(evaluate|assess|review|critique)\b",
            r"\b(overview|outline) (of|for)\b",
            r"\b(list|enumerate) (all|every|the key|the main)\b",
            r"\b(give me|provide) (a|an) (summary|overview|breakdown|"
            r"analysis|comparison)\b",
        )
    ),
    QueryIntent.SEARCH: tuple(
        re.compile(p, re.IGNORECASE)
        for p in (
            r"\b(find|look ?up|locate|search for|search the)\b",
            r"\b(show me|pull up)\b",
            r"\bwhere (is|are|can i find|do i find)\b",
            r"\bwhich (doc(ument)?s?|files?)\b",
            r"\b(any|do we have) (doc(ument)?s?|files?|info)\b",
        )
    ),
    # Production Roadmap 1.9 — bounded "give me one fact" lookups. The
    # heuristic is shape-driven, not topic-driven: a short interrogative
    # under ~14 words is almost always answerable from a single chunk, and
    # full-depth retrieval just adds latency. We deliberately gate this
    # behind a max-length predicate (checked in classify_intent) so a long
    # discursive "what does our policy say about ..." question still goes
    # through the regular FACTUAL_QA path with full search depth.
    #
    # These patterns ALSO match FACTUAL_QA; the priority ordering ensures
    # the bounded variant wins when the message is short enough.
    QueryIntent.QUICK_ANSWER: tuple(
        re.compile(p, re.IGNORECASE)
        for p in (
            # Short "what is / what's the X" forms.
            r"^\s*(what(?:'s| is| are)|who(?:'s| is| are)|when(?:'s| is)|where(?:'s| is))\b",
            # Single-word factual nouns commonly used as fragments.
            r"^\s*(holiday|vacation|pto|wfh|salary|payday|expense|reimbursement|"
            r"benefits|insurance|onboarding|password|wifi|office)\s+policy\b",
            # Looks-up that don't need synthesis.
            r"^\s*(define|definition of)\b",
        )
    ),
    QueryIntent.FACTUAL_QA: tuple(
        re.compile(p, re.IGNORECASE)
        for p in (
            r"^\s*(what|who|when|where|how|why|is|are|does|do|can|should|will|did|has|have)\b",
            r"\b(policy|rule|procedure|process|guideline|requirement)\b",
            r"\?\s*$",
        )
    ),
}

# Word-count cap on QUICK_ANSWER. Above this, even a question-shape message
# is probably worth full-depth retrieval (the user is providing context, not
# just looking up a fact). Conservatively low — we'd rather miss a quick
# answer than ship a slow one as quick.
_QUICK_ANSWER_MAX_WORDS = 14


@dataclass(frozen=True)
class IntentClassification:
    intent: QueryIntent
    matched_patterns: tuple[str, ...]


def classify_intent(query: str) -> IntentClassification:
    """Classify a query in priority order. Returns FACTUAL_QA when nothing matches."""
    q = (query or "").strip()
    if not q:
        return IntentClassification(intent=QueryIntent.FACTUAL_QA, matched_patterns=())

    # QUICK_ANSWER is short-circuited above FACTUAL_QA by a length gate so
    # long discursive question-shape messages still get full retrieval.
    word_count = len(q.split())

    for intent in (
        QueryIntent.TASK_GENERATION,
        QueryIntent.ANALYSIS,
        QueryIntent.SEARCH,
        QueryIntent.QUICK_ANSWER,
        QueryIntent.FACTUAL_QA,
    ):
        if intent == QueryIntent.QUICK_ANSWER and word_count > _QUICK_ANSWER_MAX_WORDS:
            continue
        hits: list[str] = []
        for pat in _INTENT_PATTERNS[intent]:
            if pat.search(q):
                hits.append(pat.pattern)
        if hits:
            return IntentClassification(intent=intent, matched_patterns=tuple(hits))

    return IntentClassification(intent=QueryIntent.FACTUAL_QA, matched_patterns=())


# ── Per-intent system-prompt overlays ────────────────────────────────────────
# These get *appended* below the org instructions and our base SYSTEM_PROMPT —
# they don't replace either. Keep them short: every additional token rides on
# every chat turn for the matched intent.

INTENT_SYSTEM_OVERLAYS: dict[QueryIntent, str] = {
    QueryIntent.FACTUAL_QA: (
        "INTENT: factual question. Answer concisely and directly. Cite the "
        "specific source the answer comes from. If the retrieved context "
        "doesn't contain the answer, say so plainly — do not extrapolate. "
        "Cap responses at ~200 words unless the question requires depth."
    ),
    QueryIntent.TASK_GENERATION: (
        "INTENT: produce a deliverable. Output the email / JD / message / "
        "announcement directly — no meta-commentary like 'here is the email'. "
        "Match the company's voice as observed in retrieved documents. "
        "Use clear structure (subject lines, headers, bullets) where it helps. "
        "End with a clear next step or call to action when the format expects one."
    ),
    QueryIntent.ANALYSIS: (
        "INTENT: structured analysis. Use markdown headings (##) to organize "
        "the response. Surface key findings, patterns, and risks. Cite "
        "sources inline. Depth is valued over brevity — but every claim must "
        "be tied to retrieved context."
    ),
    QueryIntent.SEARCH: (
        "INTENT: surface relevant material. List the most relevant documents "
        "with short excerpts that answer the user's lookup. If multiple "
        "documents are relevant, show all of them. Be factual — let the "
        "retrieved content speak; minimize editorial framing."
    ),
    QueryIntent.QUICK_ANSWER: (
        "INTENT: quick factual lookup. One or two sentences max. Lead with "
        "the answer; no preamble like 'According to our policy'. Cite the "
        "source. If retrieval didn't surface the fact, say so in one line — "
        "do not pad the answer to fill space."
    ),
}


def overlay_for(intent: QueryIntent) -> str:
    return INTENT_SYSTEM_OVERLAYS[intent]
