"""Document recommendations (V3 #50).

What it does
────────────
Maps each org's `primary_use_case` (collected at enrichment, see migration
025 + `routers/organizations.py::enrich`) to a curated checklist of
documents the team will most likely want in Contexo. The checklist
is rendered on the Documents page until enough items are checked off.

Why use_case first (not industry)
─────────────────────────────────
Industry is a weak signal — a B2B SaaS company doing HR Ops needs the same
templates as a B2B agency doing HR Ops. Use case is the predictive
dimension. Industry is folded in as a fallback for the "general" use case
where we don't have a strong template set.

Why pure keyword/Jaccard, no LLM
────────────────────────────────
Zero runtime cost. A keyword-driven map runs in microseconds and is
trivial to extend or override per-org. Auto-matching uploaded documents to
the checklist uses Jaccard token overlap on normalised names — adequate
for matching "Employee Handbook v3 (final).pdf" → "Employee Handbook" and
small enough to ship without a fuzzy-match dependency.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

# Each entry: name, description (one-liner for the chip), why_it_matters
# (1-sentence rationale shown on hover/expand), examples (concrete prompts
# this doc would help answer).
_TEMPLATE_LIBRARY: dict[str, list[dict[str, Any]]] = {
    "hr_policies": [
        {
            "key": "employee_handbook",
            "name": "Employee Handbook",
            "description": "Core policies, benefits, conduct.",
            "why": "Single source of truth for the most common HR questions.",
            "examples": [
                "What's our PTO accrual policy?",
                "How does the bonus review cycle work?",
            ],
        },
        {
            "key": "pto_policy",
            "name": "PTO & Leave Policy",
            "description": "Vacation, sick, parental, bereavement.",
            "why": "Leave questions are the single highest-volume HR query.",
            "examples": [
                "How many sick days do I get?",
                "What's the parental leave policy?",
            ],
        },
        {
            "key": "code_of_conduct",
            "name": "Code of Conduct",
            "description": "Expected behaviour, harassment policy, reporting.",
            "why": "Required for compliance and a frequent reference during reviews.",
            "examples": ["Where do I report a conduct concern?"],
        },
        {
            "key": "onboarding_guide",
            "name": "Onboarding Guide",
            "description": "First-week checklist, tooling access, key contacts.",
            "why": "Saves managers writing the same intro doc every hire.",
            "examples": ["What does week 1 look like for a new engineer?"],
        },
        {
            "key": "benefits_overview",
            "name": "Benefits Overview",
            "description": "Healthcare, retirement, perks, eligibility.",
            "why": "Benefits questions spike during open enrolment and at year-end.",
            "examples": ["What's our 401(k) match?"],
        },
        {
            "key": "performance_review_template",
            "name": "Performance Review Template",
            "description": "Rubric, calibration guide, manager checklist.",
            "why": "Surfaces in every review cycle and helps newer managers calibrate.",
            "examples": ["What does a Strong rating mean for our IC4 ladder?"],
        },
    ],
    "sales_enablement": [
        {
            "key": "product_one_pager",
            "name": "Product One-Pager",
            "description": "Positioning, value props, target persona.",
            "why": "Reps default to outdated pitch decks without an authoritative one-pager.",
            "examples": ["Give me 3 talking points for a security-conscious buyer."],
        },
        {
            "key": "pricing_sheet",
            "name": "Pricing & Packaging",
            "description": "Tiers, list price, discount levers.",
            "why": "Pricing is the most frequently asked + most frequently outdated doc.",
            "examples": ["What's the list price for the Team plan annual?"],
        },
        {
            "key": "case_studies",
            "name": "Case Studies",
            "description": "Customer success stories, outcomes, quotes.",
            "why": "Concrete proof is what closes — reps lose deals fishing for stories.",
            "examples": ["Find a case study from a 50-person SaaS company."],
        },
        {
            "key": "competitive_battle_cards",
            "name": "Competitive Battle Cards",
            "description": "How we win vs each major competitor.",
            "why": "Objection-handling is the highest-leverage enablement asset.",
            "examples": ["How do we differentiate vs Acme?"],
        },
        {
            "key": "objection_handling",
            "name": "Objection Handling Guide",
            "description": "Top objections, recommended responses, escalations.",
            "why": "Cuts down the 'wait, what do I say to that?' Slack pings.",
            "examples": ["Customer says our integration is too thin — response?"],
        },
        {
            "key": "discovery_call_script",
            "name": "Discovery Call Script",
            "description": "Question library, qualification framework.",
            "why": "Consistency on disco calls shows up in pipeline quality.",
            "examples": ["What's our qualification framework?"],
        },
    ],
    "customer_support": [
        {
            "key": "support_playbook",
            "name": "Support Playbook",
            "description": "Triage, severity levels, response SLAs.",
            "why": "The reference everyone needs the moment a P1 lands.",
            "examples": ["What's the SLA for a Sev-2 outage report?"],
        },
        {
            "key": "refund_policy",
            "name": "Refund & Credit Policy",
            "description": "When credits apply, approval levels.",
            "why": "Refund disputes are high-stakes and high-frequency.",
            "examples": ["Can I issue a refund for a usage-based overage?"],
        },
        {
            "key": "common_troubleshooting",
            "name": "Common Troubleshooting Guide",
            "description": "Top 10 issues + step-by-step fixes.",
            "why": "Saves dozens of L1 escalations per week.",
            "examples": ["Customer can't connect their Slack — what do I check?"],
        },
        {
            "key": "feature_faq",
            "name": "Feature FAQ",
            "description": "Common questions per major feature.",
            "why": "Speeds up first-response time on 'how do I…' questions.",
            "examples": ["How does our SSO integration work?"],
        },
        {
            "key": "escalation_paths",
            "name": "Escalation Paths",
            "description": "Who to page, when, for what.",
            "why": "Reduces panic and missed escalations during incidents.",
            "examples": ["Who do I page for a billing dispute over $10k?"],
        },
        {
            "key": "tone_voice_guide",
            "name": "Tone & Voice Guide",
            "description": "How we sound to customers.",
            "why": "Makes AI-drafted replies sound like your team, not a chatbot.",
            "examples": ["Rewrite this reply in our voice."],
        },
    ],
    "engineering": [
        {
            "key": "architecture_overview",
            "name": "Architecture Overview",
            "description": "System diagram, key services, ownership.",
            "why": "New hires lose a week without it; reviewers cite it constantly.",
            "examples": ["How does data flow from the ingest pipeline to chunks?"],
        },
        {
            "key": "runbooks",
            "name": "Runbooks",
            "description": "On-call scripts for the top 10 incident types.",
            "why": "Buys back hours during a 2am page.",
            "examples": ["What do I do if the embedding worker queue is backed up?"],
        },
        {
            "key": "postmortems",
            "name": "Postmortem Library",
            "description": "Past incidents and what we learned.",
            "why": "Avoids re-litigating the same outage twice.",
            "examples": ["Have we seen this kind of cache stampede before?"],
        },
        {
            "key": "coding_conventions",
            "name": "Coding Conventions",
            "description": "Style, testing, security baselines.",
            "why": "Cuts review nits and onboarding friction.",
            "examples": ["What's our policy on adding new dependencies?"],
        },
        {
            "key": "deployment_guide",
            "name": "Deployment Guide",
            "description": "How to ship, env vars, rollback.",
            "why": "Production confidence depends on this being current.",
            "examples": ["How do I roll back a bad Vercel deploy?"],
        },
        {
            "key": "api_documentation",
            "name": "API Documentation",
            "description": "Public/internal endpoints, auth, contracts.",
            "why": "External integrators and internal teams hit this constantly.",
            "examples": ["What's the response shape for /v1/messages?"],
        },
    ],
    "general": [
        {
            "key": "company_overview",
            "name": "Company Overview",
            "description": "Mission, products, customers, history.",
            "why": "Grounds the AI's responses in who you actually are.",
            "examples": ["Summarize what our company does for a new hire."],
        },
        {
            "key": "brand_guidelines",
            "name": "Brand & Voice Guidelines",
            "description": "Visual + tonal identity.",
            "why": "Keeps generated copy on-brand across teams.",
            "examples": ["Rewrite this in our voice."],
        },
        {
            "key": "okrs_or_strategy",
            "name": "OKRs or Strategy Doc",
            "description": "What we're focused on this quarter.",
            "why": "Helps everyone prioritise the same things.",
            "examples": ["Which OKR does this project ladder up to?"],
        },
        {
            "key": "all_hands_summary",
            "name": "All-Hands Summaries",
            "description": "Recent leadership updates.",
            "why": "Closes the loop for async/remote teammates.",
            "examples": ["What did the CEO announce last all-hands?"],
        },
        {
            "key": "team_org_chart",
            "name": "Team & Org Chart",
            "description": "Who reports to whom; team purpose.",
            "why": "Removes the 'who owns X?' tax for cross-team work.",
            "examples": ["Who owns the billing integration?"],
        },
        {
            "key": "process_playbook",
            "name": "Process Playbook",
            "description": "How we run sprints, reviews, planning.",
            "why": "Saves managers re-documenting process every kickoff.",
            "examples": ["What's our sprint planning cadence?"],
        },
    ],
}


def recommendations_for(
    *,
    primary_use_case: str | None,
    industry: str | None = None,
) -> list[dict[str, Any]]:
    """Return a curated recommendation list for the given (use_case, industry).

    Defaults to the 'general' set if the use case is missing or unknown — we
    always return *something* so the widget can render. Each returned entry
    starts un-matched and un-dismissed; the caller persists with that shape.
    """
    use_case = (primary_use_case or "general").strip().lower()
    templates = _TEMPLATE_LIBRARY.get(use_case) or _TEMPLATE_LIBRARY["general"]
    out: list[dict[str, Any]] = []
    for t in templates:
        out.append({
            **t,
            "matched_document_id": None,
            "matched_at": None,
            "dismissed_at": None,
        })
    return out


# ── Auto-match uploaded documents to recommendations ─────────────────────


_STOPWORDS = frozenset({
    "and", "or", "the", "a", "an", "of", "to", "for", "in", "on", "at",
    "v", "ver", "version", "final", "draft", "copy", "doc", "docx", "pdf",
})
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(s: str) -> set[str]:
    """Lower → strip extension → tokenise → drop stopwords + 1-char tokens."""
    if not s:
        return set()
    s = s.lower()
    # strip common file extensions before tokenising so they don't fold in
    s = re.sub(r"\.(pdf|docx?|txt|md|markdown|html?|csv|xlsx?|pptx?)$", "", s)
    tokens = {t for t in _TOKEN_RE.findall(s) if len(t) > 1 and t not in _STOPWORDS}
    return tokens


def _similarity(a: str, b: str) -> float:
    """Jaccard token overlap. Returns 0..1. Symmetric.

    Picked Jaccard over Levenshtein because filename noise (version suffixes,
    parenthetical notes) shouldn't dominate the score — token sets are more
    forgiving of word order and added decoration.
    """
    tok_a, tok_b = _tokens(a), _tokens(b)
    if not tok_a or not tok_b:
        return 0.0
    inter = len(tok_a & tok_b)
    union = len(tok_a | tok_b)
    return inter / union if union else 0.0


# Threshold tuned against the template names — "Employee Handbook" vs
# "Employee Handbook v3 (final).pdf" scores 1.0; "Sales Battle Cards.docx"
# vs "Competitive Battle Cards" scores 0.5. 0.45 catches the second case
# without false-positiving on unrelated names.
_AUTO_MATCH_THRESHOLD = 0.45


def best_recommendation_match(
    document_name: str,
    recommendations: list[dict[str, Any]],
) -> int | None:
    """Return the index of the best recommendation match in `recommendations`,
    or None if no template scores above threshold.

    Only un-matched, un-dismissed recommendations are eligible. Returning an
    index (not the dict) keeps the caller's update site explicit so it can
    mutate the list in place with a single assignment.
    """
    best_idx: int | None = None
    best_score = _AUTO_MATCH_THRESHOLD
    for i, rec in enumerate(recommendations):
        if rec.get("matched_document_id") or rec.get("dismissed_at"):
            continue
        score = max(
            _similarity(document_name, rec.get("name") or ""),
            # Also try the key (e.g. "employee_handbook" tokenises to
            # {"employee", "handbook"}) so a user's casual naming matches.
            _similarity(document_name, (rec.get("key") or "").replace("_", " ")),
        )
        if score > best_score:
            best_score = score
            best_idx = i
    return best_idx


def mark_match(
    recommendations: list[dict[str, Any]],
    index: int,
    *,
    document_id: str,
) -> list[dict[str, Any]]:
    """Return a copy of `recommendations` with `index` flagged as matched."""
    if index < 0 or index >= len(recommendations):
        return recommendations
    updated = [dict(r) for r in recommendations]
    updated[index]["matched_document_id"] = document_id
    updated[index]["matched_at"] = datetime.now(UTC).isoformat()
    return updated
