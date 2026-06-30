"""RfpAgent — autonomous RFP response state machine.

Re-entrant agent that walks an `rfp_responses` row from extracted requirements
through to a fully-formatted, format-preserving deliverable. Inherits BaseAgent
for run_record + step audit + competitor scan.

State machine (rfp_responses.status):

    extracting
       ↓
    awaiting_requirements_review     ← rep can edit/add/remove requirements
       ↓ (event: rfp/requirements-approved)
    drafting                         ← answerer loop, one task per requirement
       ↓
    awaiting_rep_review              ← rep reviews each answer
       ↓ (event: rfp/rep-approved)
    awaiting_legal_review (optional, if legal_required) ← legal gate
       ↓ (event: rfp/legal-approved)
    finalizing                       ← render outputs
       ↓
    ready                            ← terminal happy path

Side branches:
    legal_rejected  ← legal rejected with notes → re-draft flagged → awaiting_rep_review
    failed          ← terminal unhappy path

Concurrency: per-rfp_id key on the Inngest function. Two events for the same
RFP queue serially, so we never double-write the same rfp_responses row.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from app.database import get_service_client
from app.services.agents.base_agent import BaseAgent
from app.services.retrieval.collection_scope import resolve_org_rfp_scope

from . import answerer as answerer_svc
from . import exporter as export_svc
from . import extractor as extractor_svc
from . import storage as rfp_storage

log = logging.getLogger(__name__)


# Max concurrent answerer calls. Keep tight — each one fires up to 3 LLM
# tool calls + 3 hybrid_search round trips. A 50-requirement RFP at limit=5
# is ~250 LLM calls, which is the upper bound we can sustain without
# burning the Gemini RPM cap.
_ANSWER_CONCURRENCY = 5


class RfpAgent(BaseAgent):
    agent_type = "rfp"

    def __init__(
        self,
        *,
        org_id: str,
        rfp_id: str,
        triggered_by_user_id: str | None = None,
        resume_from: str | None = None,
        run_id: str | None = None,
    ) -> None:
        super().__init__(
            org_id=org_id,
            input_data={"rfp_id": rfp_id, "resume_from": resume_from},
            triggered_by="webhook",
            triggered_by_user_id=triggered_by_user_id,
            run_id=run_id,
        )
        self.rfp_id = rfp_id
        self.resume_from = resume_from
        self._rfp_row: dict[str, Any] | None = None

    # ── State helpers ──────────────────────────────────────────────────────

    async def _load_rfp(self) -> dict[str, Any]:
        if self._rfp_row is not None:
            return self._rfp_row
        svc = get_service_client()
        res = await asyncio.to_thread(
            lambda: svc.table("rfp_responses")
            .select("*")
            .eq("id", self.rfp_id)
            .maybe_single()
            .execute()
        )
        if not res or not res.data:
            raise RuntimeError(f"rfp_not_found:{self.rfp_id}")
        self._rfp_row = res.data
        return res.data

    async def _refresh_rfp(self) -> dict[str, Any]:
        self._rfp_row = None
        return await self._load_rfp()

    async def _set_status(
        self,
        status: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        svc = get_service_client()
        payload: dict[str, Any] = {"status": status}
        if extra:
            payload.update(extra)
        await asyncio.to_thread(
            lambda: svc.table("rfp_responses")
            .update(payload)
            .eq("id", self.rfp_id)
            .execute()
        )
        self._rfp_row = None

    async def _link_run(self) -> None:
        """Wire the agent_runs.id onto rfp_responses.run_id so the admin
        audit UI can deep-link from a run to its RFP."""
        svc = get_service_client()
        await asyncio.to_thread(
            lambda: svc.table("rfp_responses")
            .update({"run_id": self.run_id})
            .eq("id", self.rfp_id)
            .execute()
        )

    # ── Main dispatcher ────────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        await self._link_run()
        rfp = await self._load_rfp()
        current = rfp.get("status") or "extracting"

        await self.log_step(
            "dispatch",
            "started",
            {"current_status": current, "resume_from": self.resume_from},
        )

        if current in ("ready", "failed"):
            return {"status": current, "terminal": True}

        # Backwards-compat with legacy synchronous flow: if a row is in
        # `extracted`/`reviewed`, treat as awaiting_requirements_review.
        if current == "extracted":
            await self._set_status("awaiting_requirements_review")
            return {"status": "awaiting_requirements_review"}
        if current == "reviewed":
            current = "awaiting_requirements_review"

        # Explicit dispatch with resume_from override
        target = self.resume_from or current

        if target == "extracting":
            return await self._step_extract()
        if target == "awaiting_requirements_review":
            return {"status": current, "waiting_for": "rep_to_approve_requirements"}
        if target == "drafting":
            return await self._step_draft_all_answers()
        if target == "awaiting_rep_review":
            return {"status": current, "waiting_for": "rep_to_approve_answers"}
        if target == "awaiting_legal_review":
            return {"status": current, "waiting_for": "legal_to_approve"}
        if target == "legal_rejected":
            return await self._step_redraft_rejected()
        if target == "finalizing":
            return await self._step_finalize()

        return {"status": current, "no_op": True}

    # ── Stage 1: extract ───────────────────────────────────────────────────

    async def _step_extract(self) -> dict[str, Any]:
        await self.log_step("extract", "started")
        rfp = await self._refresh_rfp()
        source_path = rfp.get("source_storage_path")
        source_format = rfp.get("source_format") or "pdf"
        if not source_path:
            raise RuntimeError("rfp_source_missing — upload must populate source_storage_path")

        file_bytes = await rfp_storage.download_source(storage_path=source_path)
        parsed = await extractor_svc.extract(file_bytes, source_format)

        if not parsed.requirements:
            await self._set_status("failed", extra={"error_message": "No requirements could be extracted from the document."})
            await self.log_step("extract", "failed", error="no_requirements")
            return {"status": "failed", "reason": "no_requirements"}

        # Persist requirements (with semantic dedup → clusters)
        inserted = await extractor_svc.persist_requirements(
            rfp_id=self.rfp_id,
            org_id=self.org_id,
            parsed=parsed,
        )

        # Snapshot sheet layout (so the exporter can fill the right column).
        # Stored as JSONB on rfp_responses for simplicity; alternative would be
        # a per-sheet child table but layouts are tiny + read-once on export.
        await self._set_status(
            "awaiting_requirements_review",
            extra={
                "requirements": _legacy_requirements_snapshot(inserted),  # backward-compat for legacy UI
                "matches": [],
                "error_message": None,
                # New: persist sheet layout in source_text (re-purpose existing column
                # to avoid yet another migration). Format: '@@layout:{json}'
                "source_text": _encode_layout_payload(parsed.sheet_layout, fallback_text=parsed.full_text_fallback),
            },
        )
        await self.log_step(
            "extract",
            "completed",
            {
                "extraction_source": parsed.extraction_source,
                "requirement_count": len(inserted),
                "cluster_lead_count": sum(1 for r in inserted if r.get("is_cluster_lead")),
            },
        )
        return {
            "status": "awaiting_requirements_review",
            "requirement_count": len(inserted),
        }

    # ── Stage 2: draft answers ─────────────────────────────────────────────

    async def _step_draft_all_answers(self) -> dict[str, Any]:
        await self.log_step("draft_answers", "started")
        await self._set_status("drafting", extra={"error_message": None})

        rfp = await self._refresh_rfp()
        svc = get_service_client()

        # Resolve retrieval scope: per-RFP collection_id beats org default.
        scope_doc_ids, resolved_coll = await resolve_org_rfp_scope(
            org_id=self.org_id,
            override_collection_id=rfp.get("collection_id"),
            client=svc,
        )
        if resolved_coll and not rfp.get("collection_id"):
            await asyncio.to_thread(
                lambda: svc.table("rfp_responses")
                .update({"collection_id": resolved_coll})
                .eq("id", self.rfp_id)
                .execute()
            )

        # Fetch all CLUSTER LEAD requirements. Followers share the lead's answer.
        def _fetch_leads() -> list[dict[str, Any]]:
            res = (
                svc.table("rfp_requirements")
                .select("*")
                .eq("rfp_id", self.rfp_id)
                .eq("is_cluster_lead", True)
                .order("ordinal")
                .execute()
            )
            return res.data or []

        def _fetch_all() -> list[dict[str, Any]]:
            res = (
                svc.table("rfp_requirements")
                .select("*")
                .eq("rfp_id", self.rfp_id)
                .order("ordinal")
                .execute()
            )
            return res.data or []

        leads = await asyncio.to_thread(_fetch_leads)
        all_reqs = await asyncio.to_thread(_fetch_all)
        if not leads:
            await self._set_status(
                "failed",
                extra={"error_message": "No requirements to draft answers for."},
            )
            return {"status": "failed", "reason": "no_requirements"}

        # Bounded-parallel answerer loop
        sem = asyncio.Semaphore(_ANSWER_CONCURRENCY)

        async def _one(req: dict[str, Any]) -> tuple[str, answerer_svc.AnswerResult]:
            async with sem:
                result = await answerer_svc.answer_requirement(
                    org_id=self.org_id,
                    requirement_text=req["requirement_text"],
                    category=req.get("category"),
                    notes_hint=None,
                    scope_document_ids=scope_doc_ids,
                )
            return req["id"], result

        outcomes = await asyncio.gather(*(_one(r) for r in leads))
        answers_by_lead_id = {req_id: result for req_id, result in outcomes}

        # Fan out lead answers to cluster followers
        by_cluster: dict[str, str] = {}
        for r in all_reqs:
            if r.get("is_cluster_lead") and r.get("cluster_id"):
                by_cluster[r["cluster_id"]] = r["id"]

        # Upsert answers for ALL requirements (leads + followers).
        rows: list[dict[str, Any]] = []
        gap_count = 0
        for r in all_reqs:
            cluster_id = r.get("cluster_id")
            if r.get("is_cluster_lead"):
                lead_id = r["id"]
            else:
                lead_id = by_cluster.get(cluster_id or "") or r["id"]
            ans = answers_by_lead_id.get(lead_id)
            if ans is None:
                # Solo requirement that wasn't a "lead" — re-run answerer
                ans = await answerer_svc.answer_requirement(
                    org_id=self.org_id,
                    requirement_text=r["requirement_text"],
                    category=r.get("category"),
                    scope_document_ids=scope_doc_ids,
                )
            if ans.is_gap:
                gap_count += 1
            rows.append(
                {
                    "rfp_id": self.rfp_id,
                    "requirement_id": r["id"],
                    "org_id": self.org_id,
                    "answer_text": ans.answer_text[:8000] if ans.answer_text else None,
                    "sources": [
                        {
                            "document_id": s.document_id,
                            "document_name": s.document_name,
                            "chunk_id": s.chunk_id,
                            "similarity": float(s.similarity),
                            "snippet": s.snippet,
                        }
                        for s in ans.sources
                    ],
                    "confidence": float(ans.confidence) if ans.confidence else None,
                    "confidence_band": ans.confidence_band,
                    "search_queries": ans.queries,
                    "status": "gap" if ans.is_gap else "draft",
                    "flag_message": ans.flag_message,
                    "llm_tokens": int(ans.llm_tokens or 0),
                    "generated_at": datetime.now(UTC).isoformat(),
                }
            )
            self.add_tokens(int(ans.llm_tokens or 0))
            self.add_confidence(ans.confidence)

        def _upsert() -> None:
            svc.table("rfp_answers").upsert(
                rows, on_conflict="requirement_id"
            ).execute()

        await asyncio.to_thread(_upsert)

        await self._set_status(
            "awaiting_rep_review",
            extra={
                "gap_count": gap_count,
                "generated_at": datetime.now(UTC).isoformat(),
            },
        )
        await self.log_step(
            "draft_answers",
            "completed",
            {"total": len(rows), "gaps": gap_count, "leads": len(leads)},
        )

        # Open a rep-review approval gate (mirrors Sales Agent pattern).
        approval_id = await rfp_storage.create_rfp_approval(
            org_id=self.org_id,
            rfp_id=self.rfp_id,
            gate_type="rep",
        )
        await self.pause_for_approval(approval_id)
        return {
            "status": "awaiting_rep_review",
            "approval_id": approval_id,
            "gaps": gap_count,
        }

    # ── Stage 3: re-draft legal rejections ─────────────────────────────────

    async def _step_redraft_rejected(self) -> dict[str, Any]:
        await self.log_step("redraft_rejected", "started")
        await self._set_status("drafting")
        rfp = await self._refresh_rfp()
        svc = get_service_client()

        feedback = rfp.get("legal_rejection_notes") or ""

        # Find answers marked 'rejected' (the router does this on legal rejection).
        def _fetch_rejected() -> list[dict[str, Any]]:
            res = (
                svc.table("rfp_answers")
                .select("id, requirement_id, status")
                .eq("rfp_id", self.rfp_id)
                .eq("status", "rejected")
                .execute()
            )
            return res.data or []

        rejected = await asyncio.to_thread(_fetch_rejected)
        if not rejected:
            # Nothing to do; transition forward.
            await self._set_status("awaiting_rep_review")
            return {"status": "awaiting_rep_review", "no_op": True}

        req_ids = [r["requirement_id"] for r in rejected]

        def _fetch_reqs() -> list[dict[str, Any]]:
            res = (
                svc.table("rfp_requirements")
                .select("*")
                .in_("id", req_ids)
                .execute()
            )
            return res.data or []

        reqs = await asyncio.to_thread(_fetch_reqs)
        scope_doc_ids, _ = await resolve_org_rfp_scope(
            org_id=self.org_id,
            override_collection_id=rfp.get("collection_id"),
        )

        sem = asyncio.Semaphore(_ANSWER_CONCURRENCY)

        async def _one(r: dict[str, Any]) -> tuple[str, answerer_svc.AnswerResult]:
            async with sem:
                ans = await answerer_svc.answer_requirement(
                    org_id=self.org_id,
                    requirement_text=r["requirement_text"],
                    category=r.get("category"),
                    scope_document_ids=scope_doc_ids,
                    legal_rejection_feedback=feedback,
                )
            return r["id"], ans

        outcomes = await asyncio.gather(*(_one(r) for r in reqs))

        rows: list[dict[str, Any]] = []
        for req_id, ans in outcomes:
            rows.append(
                {
                    "requirement_id": req_id,
                    "rfp_id": self.rfp_id,
                    "org_id": self.org_id,
                    "answer_text": ans.answer_text[:8000] if ans.answer_text else None,
                    "sources": [
                        {
                            "document_id": s.document_id,
                            "document_name": s.document_name,
                            "chunk_id": s.chunk_id,
                            "similarity": float(s.similarity),
                            "snippet": s.snippet,
                        }
                        for s in ans.sources
                    ],
                    "confidence": float(ans.confidence) if ans.confidence else None,
                    "confidence_band": ans.confidence_band,
                    "search_queries": ans.queries,
                    "status": "gap" if ans.is_gap else "draft",
                    "flag_message": ans.flag_message,
                    "generated_at": datetime.now(UTC).isoformat(),
                }
            )

        def _upsert() -> None:
            svc.table("rfp_answers").upsert(
                rows, on_conflict="requirement_id"
            ).execute()

        await asyncio.to_thread(_upsert)
        await self._set_status("awaiting_rep_review")
        await self.log_step("redraft_rejected", "completed", {"count": len(rows)})
        return {"status": "awaiting_rep_review", "redrafted": len(rows)}

    # ── Stage 4: finalize ──────────────────────────────────────────────────

    async def _step_finalize(self) -> dict[str, Any]:
        await self.log_step("finalize", "started")
        await self._set_status("finalizing")
        rfp = await self._refresh_rfp()
        svc = get_service_client()

        def _fetch_reqs() -> list[dict[str, Any]]:
            res = (
                svc.table("rfp_requirements")
                .select("*")
                .eq("rfp_id", self.rfp_id)
                .order("ordinal")
                .execute()
            )
            return res.data or []

        def _fetch_answers() -> list[dict[str, Any]]:
            res = (
                svc.table("rfp_answers")
                .select("*")
                .eq("rfp_id", self.rfp_id)
                .execute()
            )
            return res.data or []

        reqs = await asyncio.to_thread(_fetch_reqs)
        answers_rows = await asyncio.to_thread(_fetch_answers)
        answers_by_req = {a["requirement_id"]: a for a in answers_rows}

        # Always emit the summary DOCX.
        company_name = await _fetch_org_name(self.org_id)
        title = rfp.get("title") or (rfp.get("source_filename") or "RFP").rsplit(".", 1)[0]
        summary_bytes = await asyncio.to_thread(
            export_svc.export_summary_docx,
            title=title,
            requirements=reqs,
            answers=answers_by_req,
            company_name=company_name,
        )
        revision = 1  # bump on regenerate (future work)
        summary_path = await rfp_storage.upload_output(
            org_id=self.org_id,
            rfp_id=self.rfp_id,
            revision=revision,
            file_bytes=summary_bytes,
            kind="summary",
        )

        # Fill original if the source format supports it.
        filled_path: str | None = None
        source_format = rfp.get("source_format")
        source_path = rfp.get("source_storage_path")
        if source_format and source_path and source_format in ("xlsx", "docx"):
            try:
                source_bytes = await rfp_storage.download_source(storage_path=source_path)
                if source_format == "xlsx":
                    answers_by_row: dict[tuple[str, int], dict[str, Any]] = {}
                    answer_column_by_sheet: dict[str, str] = {}
                    notes_column_by_sheet: dict[str, str | None] = {}
                    layout = _decode_layout_payload(rfp.get("source_text") or "")
                    for sheet_title, cfg in (layout or {}).items():
                        ac = cfg.get("answer_column_index")
                        nc = cfg.get("notes_column_index")
                        if ac is not None:
                            answer_column_by_sheet[sheet_title] = _col_letter(ac)
                        notes_column_by_sheet[sheet_title] = _col_letter(nc) if nc is not None else None
                    for r in reqs:
                        if r.get("source_sheet") and r.get("source_row_index"):
                            ans = answers_by_req.get(r["id"]) or {}
                            answers_by_row[(r["source_sheet"], int(r["source_row_index"]))] = {
                                "answer_text": ans.get("answer_text") or "",
                                "is_gap": ans.get("status") == "gap",
                                "sources": ans.get("sources") or [],
                            }
                    if answers_by_row and answer_column_by_sheet:
                        filled_bytes = await asyncio.to_thread(
                            export_svc.export_filled_xlsx,
                            source_bytes=source_bytes,
                            answers_by_row=answers_by_row,
                            answer_column_by_sheet=answer_column_by_sheet,
                            notes_column_by_sheet=notes_column_by_sheet,
                        )
                        filled_path = await rfp_storage.upload_output(
                            org_id=self.org_id,
                            rfp_id=self.rfp_id,
                            revision=revision,
                            file_bytes=filled_bytes,
                            kind="filled",
                            source_format="xlsx",
                        )
                elif source_format == "docx":
                    answers_by_ref: dict[str, dict[str, Any]] = {}
                    for r in reqs:
                        ans = answers_by_req.get(r["id"]) or {}
                        answers_by_ref[r["requirement_text"]] = {
                            "answer_text": ans.get("answer_text") or "",
                            "is_gap": ans.get("status") == "gap",
                            "sources": ans.get("sources") or [],
                        }
                    filled_bytes = await asyncio.to_thread(
                        export_svc.export_filled_docx,
                        source_bytes=source_bytes,
                        answers_by_external_ref=answers_by_ref,
                    )
                    filled_path = await rfp_storage.upload_output(
                        org_id=self.org_id,
                        rfp_id=self.rfp_id,
                        revision=revision,
                        file_bytes=filled_bytes,
                        kind="filled",
                        source_format="docx",
                    )
            except Exception as exc:
                log.warning("rfp.finalize.fill_failed rfp=%s err=%s", self.rfp_id, exc)
                await self.log_step("fill_original", "failed", error=str(exc))

        gap_count = sum(1 for a in answers_rows if a.get("status") == "gap")
        await self._set_status(
            "ready",
            extra={
                "summary_output_storage_path": summary_path,
                "filled_output_storage_path": filled_path,
                "output_file_path": summary_path,  # backwards-compat
                "gap_count": gap_count,
                "generated_at": datetime.now(UTC).isoformat(),
            },
        )
        await self.log_step(
            "finalize",
            "completed",
            {"summary_path": summary_path, "filled_path": filled_path, "gaps": gap_count},
        )
        return {
            "status": "ready",
            "summary_path": summary_path,
            "filled_path": filled_path,
            "gaps": gap_count,
        }


# ── Helpers ────────────────────────────────────────────────────────────────


def _col_letter(idx: int | None) -> str:
    """Convert 0-based column index to letter."""
    from openpyxl.utils import get_column_letter

    if idx is None:
        return ""
    return get_column_letter(idx + 1)


def _legacy_requirements_snapshot(inserted: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Populate rfp_responses.requirements JSONB for backwards compat with the
    pre-077 UI that reads from that column. Drops the format-preservation
    breadcrumbs (they live on the rfp_requirements table now)."""
    return [
        {
            "id": r.get("external_ref") or f"R{r.get('ordinal', 0) + 1}",
            "requirement_text": r.get("requirement_text", ""),
            "category": r.get("category"),
        }
        for r in inserted
    ]


def _encode_layout_payload(
    sheet_layout: dict[str, Any], *, fallback_text: str | None
) -> str:
    """We repurpose rfp_responses.source_text as a dual-purpose blob: when
    we have XLSX layout, we encode it as a prefixed JSON; otherwise the
    fallback prose stays for audit/debug. The exporter decodes the prefix."""
    import json

    if sheet_layout:
        return "@@layout:" + json.dumps(sheet_layout)
    return (fallback_text or "")[:200_000]


def _decode_layout_payload(blob: str) -> dict[str, Any] | None:
    import json

    if not blob.startswith("@@layout:"):
        return None
    try:
        return json.loads(blob[len("@@layout:") :])
    except json.JSONDecodeError:
        return None


async def _fetch_org_name(org_id: str) -> str | None:
    svc = get_service_client()

    def _q() -> dict[str, Any] | None:
        res = (
            svc.table("organizations")
            .select("name")
            .eq("id", org_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    row = await asyncio.to_thread(_q)
    return (row or {}).get("name")
