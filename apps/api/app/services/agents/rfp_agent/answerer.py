"""Per-requirement multi-tool LLM loop.

For each RFP requirement we run a bounded agent loop:

  1. Send the requirement + a focused system prompt to Gemini, with the
     `search_company_knowledge` tool available.
  2. The LLM issues 1–3 targeted searches (synonym coverage). Example: for
     "Do you support SSO?" it may search "SSO", "SAML", "OIDC authentication".
  3. After each search, the chunks come back and the LLM decides whether to
     search more or compose the final answer.
  4. We cap at 3 tool calls per requirement to bound LLM cost.

Output shape: AnswerResult with answer_text, sources (deduped), confidence
band (high/medium/low/none), search_queries used, and a gap flag.

The retrieval scope is determined upstream: when an RFP has a collection_id
or the org has rfp_approved_collection_id set, the caller resolves it to a
document_ids list and passes it in here.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from app.database import get_service_client
from app.services.llm.client import (
    SEARCH_TOOL,
    SEARCH_TOOL_NAME,
    get_llm_client,
)
from app.services.llm.types import Message, ToolResult
from app.services.retrieval import SearchHit
from app.services.retrieval.search_cache import hybrid_search_cached

log = logging.getLogger(__name__)


# ── Tunables ────────────────────────────────────────────────────────────────

DEFAULT_MAX_TOOL_CALLS = 3
DEFAULT_SEARCH_K = 6
# Confidence bands from top-source similarity. Tuned to align with the
# existing rfp_response.py's 0.55 gap threshold + add nuance.
_HIGH_CONFIDENCE = 0.70
_MEDIUM_CONFIDENCE = 0.55


# ── Public data shape ──────────────────────────────────────────────────────


@dataclass
class AnswerSource:
    document_id: str
    document_name: str
    chunk_id: str
    similarity: float
    snippet: str | None = None


@dataclass
class AnswerResult:
    answer_text: str
    sources: list[AnswerSource] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    confidence: float = 0.0
    confidence_band: str = "none"   # high|medium|low|none
    is_gap: bool = False
    flag_message: str | None = None
    llm_tokens: int = 0


# ── System prompt ──────────────────────────────────────────────────────────

ANSWERER_SYSTEM = """You are drafting a single response to a Request for Proposal (RFP) requirement on behalf of a vendor. Your output will be sent to a paying customer.

# How you work

You have ONE tool: `search_company_knowledge(query)`. Use it to retrieve company-specific context BEFORE producing any output.

Rules for tool use:
- Issue 1 to 3 searches. Cover different facets / synonyms of the requirement.
  Example for "Do you support SSO?": call with "SSO", then "SAML authentication", then "OIDC identity provider integration" if needed.
- Do not search for the requirement verbatim. Pick 2-6 word concrete topics.
- Stop searching once the retrieved context is sufficient. Do not pad searches just to use the budget.

# Rules for your final output

- Output a SINGLE customer-facing paragraph, 40-160 words. No headers. No bullets unless the requirement asks for a list. No JSON.
- Use ONLY information retrieved from the tool. Do not invent capabilities, certifications, numbers, dates, or processes.
- Use the vendor's voice ("we", "our platform"), not the customer's.
- If the retrieved context is genuinely insufficient to answer, say so plainly in one sentence. Start that sentence with "GAP:" so the legal-review pipeline can flag it. Do not fabricate.
- Do not say "Based on our documentation" or "According to our records." Just answer.
- If the requirement is a yes/no question (compliance, certifications), lead with the direct answer ("Yes — we hold SOC 2 Type II ...") then justify.
"""


# ── Entry point ────────────────────────────────────────────────────────────


async def answer_requirement(
    *,
    org_id: str,
    requirement_text: str,
    category: str | None = None,
    notes_hint: str | None = None,
    scope_document_ids: list[str] | None = None,
    legal_rejection_feedback: str | None = None,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
) -> AnswerResult:
    """Run the multi-tool loop for one requirement. Never raises — returns an
    AnswerResult with is_gap=True on any failure path so the agent can mark
    the row for human review instead of crashing the whole run."""
    if scope_document_ids is not None and len(scope_document_ids) == 0:
        # Collection scope resolved to zero docs. Don't even ask Gemini.
        return AnswerResult(
            answer_text="GAP: no documents in the configured RFP collection.",
            is_gap=True,
            flag_message="Org's RFP-approved collection is empty. Configure it in Settings → Integrations.",
        )

    client = get_llm_client()
    svc = get_service_client()
    user_prompt = _build_user_prompt(
        requirement_text=requirement_text,
        category=category,
        notes_hint=notes_hint,
        legal_feedback=legal_rejection_feedback,
    )
    messages: list[Message] = [Message(role="user", content=user_prompt)]

    sources_by_chunk: dict[str, tuple[SearchHit, float]] = {}
    queries: list[str] = []
    tool_calls_made = 0
    final_text = ""
    total_tokens = 0

    for _round in range(max_tool_calls + 1):
        try:
            response = await client.complete(
                messages=messages,
                tools=(SEARCH_TOOL,),
                temperature=0.25,
                timeout=75.0,
                system_extra=ANSWERER_SYSTEM,
                replace_system_prompt=True,
            )
        except Exception as exc:
            log.warning("rfp.answerer.llm_failed req=%r err=%s", requirement_text[:80], exc)
            return AnswerResult(
                answer_text="",
                is_gap=True,
                flag_message=f"AI call failed: {exc.__class__.__name__}. Review manually.",
                queries=queries,
            )

        if not response.tool_calls:
            final_text = (response.text or "").strip()
            break

        # Append the assistant turn with its tool calls
        messages = [
            *messages,
            Message(
                role="assistant",
                content=response.text or "",
                tool_calls=response.tool_calls,
            ),
        ]

        # Execute each tool call, capped against the per-requirement budget.
        # Run them in parallel — they're independent reads.
        call_budget = max_tool_calls - tool_calls_made
        callable_tool_calls = list(response.tool_calls)[:call_budget]

        if not callable_tool_calls:
            # Budget exhausted; force a final response next round by removing tools.
            try:
                forced = await client.complete(
                    messages=messages,
                    tools=(),
                    temperature=0.25,
                    timeout=60.0,
                    system_extra=ANSWERER_SYSTEM + "\n\nNo more searches. Write the final answer now using the context already retrieved.",
                    replace_system_prompt=True,
                )
                final_text = (forced.text or "").strip()
            except Exception:
                final_text = ""
            break

        tasks = []
        for tc in callable_tool_calls:
            if tc.name != SEARCH_TOOL_NAME:
                continue
            q = (tc.args.get("query") or "").strip()
            if not q:
                continue
            queries.append(q)
            tool_calls_made += 1
            tasks.append(
                _execute_search(
                    query=q,
                    org_id=org_id,
                    client=svc,
                    scope_document_ids=scope_document_ids,
                )
            )

        if not tasks:
            # LLM called the tool but with empty args. Treat as "done searching."
            final_text = (response.text or "").strip()
            break

        results: list[list[SearchHit]] = await asyncio.gather(
            *tasks, return_exceptions=False
        )

        # Build tool-result messages 1:1 with the calls we ran.
        tool_msgs: list[Message] = []
        for tc, hits in zip(callable_tool_calls, results, strict=False):
            for h in hits:
                # Keep highest similarity per chunk across multiple queries.
                prev = sources_by_chunk.get(h.chunk_id)
                sim = float(h.similarity or 0.0)
                if prev is None or sim > prev[1]:
                    sources_by_chunk[h.chunk_id] = (h, sim)
            tool_msgs.append(
                Message(
                    role="tool",
                    tool_result=ToolResult(
                        call_id=tc.id,
                        name=tc.name,
                        content=_format_tool_content(hits),
                    ),
                )
            )
        messages = [*messages, *tool_msgs]

        if tool_calls_made >= max_tool_calls:
            # Force final on the next iteration via empty tools.
            try:
                forced = await client.complete(
                    messages=messages,
                    tools=(),
                    temperature=0.25,
                    timeout=60.0,
                    system_extra=ANSWERER_SYSTEM + "\n\nNo more searches. Write the final answer now using the context already retrieved.",
                    replace_system_prompt=True,
                )
                final_text = (forced.text or "").strip()
            except Exception as exc:
                log.warning("rfp.answerer.force_final_failed: %s", exc)
                final_text = ""
            break

    # Assemble result
    ranked = sorted(sources_by_chunk.values(), key=lambda x: x[1], reverse=True)
    top_sim = ranked[0][1] if ranked else 0.0
    sources = [
        AnswerSource(
            document_id=h.document_id,
            document_name=h.document_name,
            chunk_id=h.chunk_id,
            similarity=sim,
            snippet=(h.content or "")[:300] if h.content else None,
        )
        for h, sim in ranked[:6]
    ]
    band, is_gap = _band(top_sim, final_text)
    flag = None
    if is_gap:
        if not sources:
            flag = "No supporting documentation found. Flagged for legal/SME review."
        elif top_sim < _MEDIUM_CONFIDENCE:
            flag = (
                f"Low retrieval confidence ({top_sim:.2f}). Source coverage is weak — "
                "consider uploading more documentation or rewriting manually."
            )
        elif final_text.upper().startswith("GAP:"):
            flag = final_text[4:].strip() or "AI flagged this as a gap."

    return AnswerResult(
        answer_text=final_text,
        sources=sources,
        queries=queries,
        confidence=top_sim,
        confidence_band=band,
        is_gap=is_gap,
        flag_message=flag,
        llm_tokens=total_tokens,
    )


# ── Internals ──────────────────────────────────────────────────────────────


def _build_user_prompt(
    *,
    requirement_text: str,
    category: str | None,
    notes_hint: str | None,
    legal_feedback: str | None,
) -> str:
    parts = [f"## Customer requirement\n{requirement_text.strip()}"]
    if category:
        parts.append(f"\nCategory: {category}")
    if notes_hint:
        parts.append(f"\nBuyer notes attached to this row: {notes_hint.strip()}")
    if legal_feedback:
        parts.append(
            f"\n## Prior attempt was rejected by legal\nReason: {legal_feedback.strip()}\n"
            "Address the rejection in this draft."
        )
    parts.append(
        "\n## Task\nWrite the customer-facing response. Use search_company_knowledge to "
        "ground every factual claim. If documentation is insufficient, start your sentence with 'GAP:'."
    )
    return "\n".join(parts)


async def _execute_search(
    *,
    query: str,
    org_id: str,
    client,
    scope_document_ids: list[str] | None,
) -> list[SearchHit]:
    try:
        return await hybrid_search_cached(
            query=query,
            org_id=org_id,
            client=client,
            k=DEFAULT_SEARCH_K,
            document_ids=scope_document_ids,
        )
    except Exception as exc:
        log.warning("rfp.answerer.search_failed q=%r err=%s", query[:60], exc)
        return []


def _format_tool_content(hits: list[SearchHit]) -> str:
    if not hits:
        return "(no matching documents)"
    parts: list[str] = []
    for i, h in enumerate(hits, start=1):
        sim = float(h.similarity or 0.0)
        content = (h.content or "").strip()
        if len(content) > 1500:
            content = content[:1500] + "…"
        parts.append(
            f"[#{i} | {h.document_name} | sim={sim:.2f}]\n{content}"
        )
    return "\n\n---\n\n".join(parts)


def _band(top_sim: float, text: str) -> tuple[str, bool]:
    """Map (top similarity, AI text) → (band, is_gap)."""
    if (text or "").upper().startswith("GAP:"):
        return "low", True
    if not text:
        return "none", True
    if top_sim >= _HIGH_CONFIDENCE:
        return "high", False
    if top_sim >= _MEDIUM_CONFIDENCE:
        return "medium", False
    return "low", True
