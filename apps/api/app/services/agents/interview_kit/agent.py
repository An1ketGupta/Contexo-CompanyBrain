"""InterviewKitAgent.

Pipeline (4 LLM rounds, all KB-grounded):

  1. extract_competencies — pull 6-9 weighted competencies from the JD,
     grounded in company values pulled from the KB.
  2. generate_panels      — design the panel loop (3-5 stages, 4-7 questions
     each), each question mapped to a competency.
  3. generate_scorecard   — anchored 5-level rubric per competency, calibrated
     to the role's seniority level.
  4. generate_references  — reference-check question bank per relationship
     (manager / peer / report / cross_functional).

Status machine:
    draft → generating → ready → published
                        ↘ failed

The agent runs inside Inngest, persists via service-role through storage.py,
and writes BaseAgent audit steps so the admin runs page surfaces a true
execution log.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from app.database import get_service_client
from app.observability import get_logger
from app.services.agents.base_agent import BaseAgent
from app.services.agents.kb_synthesis import (
    collect_sources,
    search_facets_concurrent,
    synthesize_json,
)
from app.services.agents.interview_kit import prompts, storage

log = get_logger(__name__)


# Defensive cap so a runaway JD doesn't blow the LLM context.
_JD_TEXT_CAP = 16_000


class InterviewKitAgent(BaseAgent):
    agent_type = "interview_kit"

    def __init__(
        self,
        *,
        org_id: str,
        requisition_id: str,
        kit_id: str,
        jd_variant_index: int | None = None,
        triggered_by_user_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        super().__init__(
            org_id=org_id,
            input_data={
                "requisition_id": requisition_id,
                "kit_id": kit_id,
                "jd_variant_index": jd_variant_index,
            },
            triggered_by="user",
            triggered_by_user_id=triggered_by_user_id,
            run_id=run_id,
        )
        self.requisition_id = requisition_id
        self.kit_id = kit_id
        self.requested_variant_index = jd_variant_index
        self.log = log.bind(
            requisition_id=requisition_id, kit_id=kit_id, org_id=org_id, agent="interview_kit"
        )

    async def run(self) -> dict[str, Any]:
        try:
            return await self._run_inner()
        except Exception as exc:
            # Two-track failure: the BaseAgent run row is already getting
            # marked failed (via run_safely's wrapper). Mirror onto the kit
            # row so the recruiter UI shows the failure inline.
            await storage.mark_kit_failed(kit_id=self.kit_id, error=str(exc))
            raise

    async def _run_inner(self) -> dict[str, Any]:
        await self.log_step("load_requisition", "started")
        req = await storage.fetch_requisition(
            org_id=self.org_id, requisition_id=self.requisition_id
        )
        if not req:
            raise RuntimeError(f"requisition_not_found:{self.requisition_id}")

        variants = req.get("jd_variants") or []
        if not variants:
            raise RuntimeError("requisition_has_no_jd_variants")

        idx = self.requested_variant_index
        if idx is None:
            idx = req.get("selected_variant_index") or 0
        idx = max(0, min(idx, len(variants) - 1))
        jd_text = (variants[idx].get("text") or "")[:_JD_TEXT_CAP]

        role_request = req.get("role_request") or ""
        seniority_hint = req.get("seniority_level") or "mid"

        await self.log_step(
            "load_requisition",
            "completed",
            {"jd_variant_index": idx, "jd_chars": len(jd_text)},
        )

        # ── 1. Facet search for grounding context ───────────────────────────
        await self.log_step("kb_search", "started")
        facets_q = {
            "values": "company values culture principles",
            "interview_policy": "interview process structured interview hiring rubric",
            "leveling": f"engineering leveling framework career levels {seniority_hint}",
            "role_specific": role_request[:200],
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

        values_ctx = facets["values"].packed_context or "(no values doc in KB — use generic best practices)"
        interview_ctx = facets["interview_policy"].packed_context or ""
        leveling_ctx = facets["leveling"].packed_context or ""

        combined_values_ctx = values_ctx
        if interview_ctx:
            combined_values_ctx = f"{values_ctx}\n\n--- Interview policy ---\n{interview_ctx}"

        # ── 2. Extract competencies ────────────────────────────────────────
        await self.log_step("extract_competencies", "started")
        comp_payload = await synthesize_json(
            system_prompt=prompts.COMPETENCIES_SYSTEM,
            user_prompt=prompts.COMPETENCIES_USER_TEMPLATE.format(
                values_context=combined_values_ctx,
                jd_text=jd_text,
                role_request=role_request,
                seniority_level=seniority_hint,
                stack=req.get("stack") or "(not specified)",
                department=req.get("department") or "(not specified)",
            ),
            temperature=0.2,
        )
        comp_payload = _ensure_dict(comp_payload)
        competencies = _ensure_list(comp_payload.get("competencies"))
        if not competencies:
            raise RuntimeError("llm_produced_no_competencies")
        role_title = (comp_payload.get("role_title") or role_request)[:200]
        sen_level = (comp_payload.get("seniority_level") or seniority_hint)[:40]
        await self.log_step(
            "extract_competencies",
            "completed",
            {"count": len(competencies), "role_title": role_title, "seniority_level": sen_level},
        )

        comp_json_for_prompt = json.dumps(competencies, ensure_ascii=False, indent=2)

        # ── 3. Panels + 4. Scorecard + 5. References (parallel) ─────────────
        # All three are independent given the competencies; run them
        # concurrently to halve wall-clock.
        await self.log_step("generate_artifacts", "started")
        panels_task = synthesize_json(
            system_prompt=prompts.PANELS_SYSTEM,
            user_prompt=prompts.PANELS_USER_TEMPLATE.format(
                values_context=combined_values_ctx,
                leveling_context=leveling_ctx or "(no leveling doc in KB)",
                jd_text=jd_text,
                competencies_json=comp_json_for_prompt,
            ),
            temperature=0.4,
        )
        scorecard_task = synthesize_json(
            system_prompt=prompts.SCORECARD_SYSTEM,
            user_prompt=prompts.SCORECARD_USER_TEMPLATE.format(
                seniority_level=sen_level,
                role_title=role_title,
                competencies_json=comp_json_for_prompt,
                leveling_context=leveling_ctx or "(no leveling doc in KB)",
            ),
            temperature=0.2,
        )
        references_task = synthesize_json(
            system_prompt=prompts.REFERENCES_SYSTEM,
            user_prompt=prompts.REFERENCES_USER_TEMPLATE.format(
                role_title=role_title,
                seniority_level=sen_level,
                competencies_json=comp_json_for_prompt,
                values_context=combined_values_ctx,
            ),
            temperature=0.3,
        )

        panels_payload, scorecard_payload, references_payload = await asyncio.gather(
            panels_task, scorecard_task, references_task
        )
        panels = _ensure_list(_ensure_dict(panels_payload).get("panels"))
        scorecard = _ensure_list(_ensure_dict(scorecard_payload).get("scorecard"))
        refs = _ensure_list(_ensure_dict(references_payload).get("reference_questions"))

        if not panels:
            raise RuntimeError("llm_produced_no_panels")
        if not scorecard:
            raise RuntimeError("llm_produced_no_scorecard")

        await self.log_step(
            "generate_artifacts",
            "completed",
            {
                "panels": len(panels),
                "scorecard_competencies": len(scorecard),
                "reference_banks": len(refs),
            },
        )

        # ── Persist ─────────────────────────────────────────────────────────
        await self.log_step("persist_kit", "started")
        await storage.mark_kit_ready(
            kit_id=self.kit_id,
            role_title=role_title,
            seniority_level=sen_level,
            competencies=competencies,
            panels=panels,
            scorecard=scorecard,
            reference_questions=refs,
            sources=sources,
        )
        await self.log_step("persist_kit", "completed", {"kit_id": self.kit_id})

        return {
            "kit_id": self.kit_id,
            "requisition_id": self.requisition_id,
            "competencies": len(competencies),
            "panels": len(panels),
            "scorecard_competencies": len(scorecard),
            "reference_banks": len(refs),
            "sources": len(sources),
            "status": "ready",
        }


def _ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _ensure_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
