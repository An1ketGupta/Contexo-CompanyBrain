"""MarketingAgent.

Pipeline (5 LLM calls — 1 sequential + 4 parallel, all KB-grounded):

  1. extract_positioning      — sequential. Pulls positioning/ICP/brand-voice
     facets and synthesizes the source-of-truth frame for downstream steps.
  2. generate_messaging_pillars — parallel. 3–5 weighted pillars + proof points.
  3. generate_competitive_angle — parallel. Per-competitor counter + wins.
     SKIPPED if no competitors were supplied (the JSONB column stays []).
  4. generate_channel_plan      — parallel. 2–3 drafts per selected channel.
  5. generate_content_brief     — parallel. Long-form outline + keywords.

Status machine:
    draft → generating → ready → published
                        ↘ failed

Mirrors InterviewKitAgent's shape: BaseAgent for audit + token tracking,
service-role storage helpers, and 4-step parallel fan-out after the first
synthesis. Output is one editable document the marketer reviews as a whole.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from app.observability import get_logger
from app.services.agents.base_agent import BaseAgent
from app.services.agents.kb_synthesis import (
    collect_sources,
    search_facets_concurrent,
    synthesize_json,
)
from app.services.agents.marketing_agent import prompts, storage

log = get_logger(__name__)


# Defensive cap on the marketer's objective + audience_hint blobs so a
# pasted-novel doesn't blow LLM context.
_OBJECTIVE_CAP = 4_000
_AUDIENCE_CAP = 1_500


class MarketingAgent(BaseAgent):
    agent_type = "marketing_brief"

    def __init__(
        self,
        *,
        org_id: str,
        brief_id: str,
        objective: str,
        audience_hint: str | None = None,
        channels: list[str] | None = None,
        competitors: list[str] | None = None,
        collection_id: str | None = None,
        triggered_by_user_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        super().__init__(
            org_id=org_id,
            input_data={
                "brief_id": brief_id,
                "objective": objective[:_OBJECTIVE_CAP],
                "audience_hint": (audience_hint or "")[:_AUDIENCE_CAP],
                "channels": channels or [],
                "competitors": competitors or [],
                "collection_id": collection_id,
            },
            triggered_by="user",
            triggered_by_user_id=triggered_by_user_id,
            run_id=run_id,
        )
        self.brief_id = brief_id
        self.objective = objective[:_OBJECTIVE_CAP]
        self.audience_hint = (audience_hint or "")[:_AUDIENCE_CAP]
        self.channels = channels or []
        self.competitors = [c for c in (competitors or []) if c.strip()]
        self.collection_id = collection_id
        self.log = log.bind(
            brief_id=brief_id, org_id=org_id, agent="marketing_brief"
        )

    async def run(self) -> dict[str, Any]:
        try:
            return await self._run_inner()
        except Exception as exc:
            # Mirror failure onto the brief row so the marketer UI shows it
            # inline; BaseAgent's run_safely() already stamps agent_runs.
            await storage.mark_brief_failed(brief_id=self.brief_id, error=str(exc))
            raise

    async def _run_inner(self) -> dict[str, Any]:
        # ── KB grounding facets ────────────────────────────────────────────
        await self.log_step("kb_search", "started")
        facets_q = {
            "positioning": "positioning ideal customer profile target audience differentiation",
            "icp": f"ideal customer profile buyer persona {self.audience_hint}".strip(),
            "brand_voice": "brand voice tone of voice writing style content guidelines",
            "customer_research": "customer interviews case studies wins outcomes ROI",
            "competitor_battlecards": (
                "competitor battlecard objection handler comparison "
                + " ".join(self.competitors)
            ).strip(),
            "seo_keywords": "SEO keywords topic clusters search intent content strategy",
            "prior_campaigns": "prior campaign launch announcement marketing collateral",
        }
        facets = await search_facets_concurrent(
            org_id=self.org_id, facets=facets_q, k=5, char_budget_per_facet=3500
        )
        sources = collect_sources(facets)
        await self.log_step(
            "kb_search",
            "completed",
            {
                "facets": {k: len(v.hits) for k, v in facets.items()},
                "unique_docs": len(sources),
            },
        )

        positioning_ctx = (
            facets["positioning"].packed_context
            or "(no positioning doc in KB — infer from objective + best practices)"
        )
        icp_ctx = facets["icp"].packed_context or ""
        if icp_ctx:
            positioning_ctx = f"{positioning_ctx}\n\n--- ICP research ---\n{icp_ctx}"

        # ── 1. Positioning (sequential — downstream depends on it) ─────────
        await self.log_step("extract_positioning", "started")
        positioning_payload = await synthesize_json(
            system_prompt=prompts.POSITIONING_SYSTEM,
            user_prompt=prompts.POSITIONING_USER_TEMPLATE.format(
                objective=self.objective,
                audience_hint=self.audience_hint or "(not provided)",
                competitors=", ".join(self.competitors) or "(none)",
                positioning_context=positioning_ctx,
            ),
            temperature=0.3,
        )
        positioning = _ensure_dict(positioning_payload)
        if not positioning.get("audience") and not positioning.get("problem"):
            raise RuntimeError("llm_produced_empty_positioning")
        positioning_json_for_prompt = json.dumps(
            positioning, ensure_ascii=False, indent=2
        )
        await self.log_step(
            "extract_positioning",
            "completed",
            {
                "audience": (positioning.get("audience") or "")[:80],
                "category": (positioning.get("category") or "")[:80],
                "value_props": len(positioning.get("value_props") or []),
                "taglines": len(positioning.get("taglines") or []),
            },
        )

        # ── 2–5. Parallel generation ───────────────────────────────────────
        await self.log_step("generate_artifacts", "started")

        evidence_ctx = (
            facets["customer_research"].packed_context
            or "(no customer-research docs in KB)"
        )
        voice_ctx = (
            facets["brand_voice"].packed_context
            or facets["prior_campaigns"].packed_context
            or "(no brand-voice / prior-campaign docs in KB — use the positioning's voice)"
        )
        competitor_ctx = (
            facets["competitor_battlecards"].packed_context
            or "(no battlecards in KB)"
        )
        seo_ctx = (
            facets["seo_keywords"].packed_context
            or "(no SEO / topic-cluster docs in KB)"
        )

        pillars_task = synthesize_json(
            system_prompt=prompts.PILLARS_SYSTEM,
            user_prompt=prompts.PILLARS_USER_TEMPLATE.format(
                positioning_json=positioning_json_for_prompt,
                objective=self.objective,
                evidence_context=evidence_ctx,
            ),
            temperature=0.3,
        )

        # Competitive angle is conditional — skipping the LLM call when no
        # competitors are named saves a round-trip and ensures the JSONB
        # column stays [] (not garbage-LLM-fabricated rows).
        if self.competitors:
            competitive_task = synthesize_json(
                system_prompt=prompts.COMPETITIVE_SYSTEM,
                user_prompt=prompts.COMPETITIVE_USER_TEMPLATE.format(
                    competitors_list=", ".join(self.competitors),
                    positioning_json=positioning_json_for_prompt,
                    competitor_context=competitor_ctx,
                ),
                temperature=0.3,
            )
        else:
            competitive_task = _noop_competitive()

        # Channel plan depends on pillars; we run it in parallel because the
        # marginal quality lift from waiting for pillars is small and the
        # latency win from parallel is large. The prompt already has the
        # full positioning, which is the bigger driver of channel voice.
        channel_task = synthesize_json(
            system_prompt=prompts.CHANNEL_PLAN_SYSTEM,
            user_prompt=prompts.CHANNEL_PLAN_USER_TEMPLATE.format(
                objective=self.objective,
                channels_list=", ".join(self.channels) or "blog, linkedin, email",
                positioning_json=positioning_json_for_prompt,
                pillars_json="(see positioning for primary themes)",
                voice_context=voice_ctx,
            ),
            temperature=0.5,
        )

        content_brief_task = synthesize_json(
            system_prompt=prompts.CONTENT_BRIEF_SYSTEM,
            user_prompt=prompts.CONTENT_BRIEF_USER_TEMPLATE.format(
                objective=self.objective,
                positioning_json=positioning_json_for_prompt,
                pillars_json="(see positioning for primary themes)",
                seo_context=seo_ctx,
            ),
            temperature=0.3,
        )

        pillars_payload, competitive_payload, channel_payload, content_payload = (
            await asyncio.gather(
                pillars_task, competitive_task, channel_task, content_brief_task
            )
        )

        pillars = _ensure_list(_ensure_dict(pillars_payload).get("pillars"))
        competitive = _ensure_list(
            _ensure_dict(competitive_payload).get("competitive_angle")
        )
        channel_plan = _ensure_list(
            _ensure_dict(channel_payload).get("channel_plan")
        )
        content_brief = _ensure_dict(content_payload)

        if not pillars:
            raise RuntimeError("llm_produced_no_pillars")
        if not channel_plan and self.channels:
            raise RuntimeError("llm_produced_no_channel_plan")

        await self.log_step(
            "generate_artifacts",
            "completed",
            {
                "pillars": len(pillars),
                "competitive_rows": len(competitive),
                "channels": len(channel_plan),
                "content_brief_outline_sections": len(
                    content_brief.get("outline") or []
                ),
            },
        )

        # ── Persist ────────────────────────────────────────────────────────
        await self.log_step("persist_brief", "started")
        await storage.mark_brief_ready(
            brief_id=self.brief_id,
            positioning=positioning,
            messaging_pillars=pillars,
            competitive_angle=competitive,
            channel_plan=channel_plan,
            content_brief=content_brief,
            sources=sources,
        )
        await self.log_step("persist_brief", "completed", {"brief_id": self.brief_id})

        return {
            "brief_id": self.brief_id,
            "pillars": len(pillars),
            "competitive_rows": len(competitive),
            "channels": len(channel_plan),
            "sources": len(sources),
            "status": "ready",
        }


async def _noop_competitive() -> dict[str, Any]:
    """No-op coroutine returned when no competitors are named. Lets the
    asyncio.gather() call stay symmetric (4 awaitables always)."""
    return {"competitive_angle": []}


def _ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _ensure_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
