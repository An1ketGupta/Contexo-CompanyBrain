"""VersionDiffAgent — generic per-version "what changed" summary (Day 15).

Mirrors the diff step inside PolicyPropagationAgent but for *non-policy*
documents — i.e. any versioned upload where we still want a "What changed
since v(n-1)" summary on the doc panel without firing Slack broadcasts or
acknowledgement fan-out.

Triggered by `doc/version-diff` from the document-processing pipeline,
fired AFTER `auto-tag-document` so we can read the final tag set. The
ingest function skips the trigger when:
  * No prior version exists (first upload).
  * The document is policy-tagged or requires_acknowledgement → handled
    by PolicyPropagationAgent already.
  * Re-uploading identical content (hash collision).

Reuses `document_diffs` so the frontend has one place to read from.
"""
from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from app.database import get_service_client
from app.observability import get_logger
from app.services.agents.base_agent import BaseAgent
from app.services.llm.task_chain import execute_task_blocking

log = get_logger(__name__)


_DIFF_CONTENT_CAP = 3_000
_SUMMARY_CONTENT_CAP = 12_000


class VersionDiffAgent(BaseAgent):
    agent_type = "version_diff"

    def __init__(
        self,
        *,
        org_id: str,
        document_id: str,
        version_id: str | None = None,
    ) -> None:
        super().__init__(
            org_id=org_id,
            input_data={"document_id": document_id, "version_id": version_id},
            triggered_by="document_ready",
        )
        self.document_id = document_id
        self._explicit_version_id = version_id

    async def run(self) -> dict[str, Any]:
        svc = get_service_client()

        await self.log_step("load_document", "started")
        doc = await self._load_document()
        if not doc:
            await self.log_step("load_document", "failed", error="document_not_found")
            raise RuntimeError("document_not_found")

        active_version = await self._resolve_active_version()
        if not active_version:
            await self.log_step("load_document", "skipped", {"reason": "no_version_row"})
            return {"diffed": False, "reason": "no_version_row"}

        previous_version = await self._resolve_previous_version(active_version)
        if not previous_version:
            await self.log_step("load_document", "skipped", {"reason": "no_previous_version"})
            return {"diffed": False, "reason": "first_version"}

        content = await self._extract_active_content()
        content_hash = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()

        if previous_version.get("content_hash") == content_hash:
            await self.log_step("diff", "skipped", {"reason": "content_unchanged"})
            await self._stamp_active_hash(active_version["id"], content, content_hash)
            return {"diffed": False, "reason": "content_unchanged"}

        # Step 2 (lightweight summarise — same cost trade-off as PolicyPropagation:
        # keeping a chunk_summary on each version means the NEXT diff prompt
        # doesn't have to re-read the previous full text).
        await self.log_step("summarise", "started")
        try:
            summary = await self._generate_summary(content)
        except Exception as exc:
            await self.log_step("summarise", "failed", error=str(exc))
            summary = ""
        if summary:
            await asyncio.to_thread(
                lambda: svc.table("document_versions")
                .update({"chunk_summary": summary, "content_hash": content_hash})
                .eq("id", active_version["id"])
                .execute()
            )
            await self.log_step("summarise", "completed", {"chars": len(summary)})

        # Step 3: actual diff. Reuse the previous version's chunk_summary if
        # set; otherwise diff against a truncated tail of its current content.
        prev_summary = (previous_version.get("chunk_summary") or "").strip()
        if not prev_summary:
            prev_content = await self._extract_previous_content(previous_version["id"])
            prev_summary = prev_content[:_DIFF_CONTENT_CAP]

        await self.log_step("diff", "started")
        try:
            diff_text = await self._generate_diff(
                doc_name=doc.get("name") or "this document",
                previous=prev_summary,
                new_content=content,
            )
        except Exception as exc:
            await self.log_step("diff", "failed", error=str(exc))
            return {"diffed": False, "reason": "diff_generation_failed"}

        diff_id = await self._persist_diff(
            previous_version=previous_version,
            active_version=active_version,
            diff_summary=diff_text,
        )
        await self.log_step("diff", "completed", {"diff_id": diff_id, "chars": len(diff_text)})

        return {
            "diffed": True,
            "diff_id": diff_id,
            "from_version": previous_version["version_number"],
            "to_version": active_version["version_number"],
        }

    # ── Data loaders ───────────────────────────────────────────────────

    async def _load_document(self) -> dict[str, Any] | None:
        svc = get_service_client()
        res = await asyncio.to_thread(
            lambda: svc.table("documents")
            .select("id, org_id, name, tags, requires_acknowledgement, current_version_id")
            .eq("id", self.document_id)
            .eq("org_id", self.org_id)
            .maybe_single()
            .execute()
        )
        return (res.data or None) if res else None

    async def _resolve_active_version(self) -> dict[str, Any] | None:
        svc = get_service_client()
        if self._explicit_version_id:
            res = await asyncio.to_thread(
                lambda: svc.table("document_versions")
                .select("id, version_number, content_hash, chunk_summary")
                .eq("id", self._explicit_version_id)
                .eq("document_id", self.document_id)
                .maybe_single()
                .execute()
            )
            if res and res.data:
                return res.data
        current = await asyncio.to_thread(
            lambda: svc.table("document_versions")
            .select("id, version_number, content_hash, chunk_summary")
            .eq("document_id", self.document_id)
            .eq("is_current", True)
            .maybe_single()
            .execute()
        )
        if current and current.data:
            return current.data
        latest = await asyncio.to_thread(
            lambda: svc.table("document_versions")
            .select("id, version_number, content_hash, chunk_summary")
            .eq("document_id", self.document_id)
            .order("version_number", desc=True)
            .limit(1)
            .execute()
        )
        rows = latest.data if latest else []
        return rows[0] if rows else None

    async def _resolve_previous_version(
        self, active_version: dict[str, Any]
    ) -> dict[str, Any] | None:
        svc = get_service_client()
        res = await asyncio.to_thread(
            lambda: svc.table("document_versions")
            .select("id, version_number, content_hash, chunk_summary")
            .eq("document_id", self.document_id)
            .lt("version_number", active_version["version_number"])
            .order("version_number", desc=True)
            .limit(1)
            .execute()
        )
        rows = res.data if res else []
        return rows[0] if rows else None

    async def _extract_active_content(self) -> str:
        svc = get_service_client()
        res = await asyncio.to_thread(
            lambda: svc.table("chunks")
            .select("content, chunk_index")
            .eq("document_id", self.document_id)
            .eq("is_archived", False)
            .order("chunk_index", desc=False)
            .limit(2000)
            .execute()
        )
        out: list[str] = []
        total = 0
        for r in res.data or []:
            piece = (r.get("content") or "").strip()
            if not piece:
                continue
            out.append(piece)
            total += len(piece)
            if total >= _SUMMARY_CONTENT_CAP * 2:
                break
        return "\n\n".join(out)

    async def _extract_previous_content(self, version_id: str) -> str:
        """Load an archived previous version's chunks (only used when we
        don't already have a chunk_summary cached on the prior version)."""
        svc = get_service_client()
        res = await asyncio.to_thread(
            lambda: svc.table("chunks")
            .select("content, chunk_index")
            .eq("document_id", self.document_id)
            .eq("document_version_id", version_id)
            .order("chunk_index", desc=False)
            .limit(1500)
            .execute()
        )
        out: list[str] = []
        total = 0
        for r in res.data or []:
            piece = (r.get("content") or "").strip()
            if not piece:
                continue
            out.append(piece)
            total += len(piece)
            if total >= _DIFF_CONTENT_CAP * 2:
                break
        return "\n\n".join(out)

    async def _stamp_active_hash(
        self, version_id: str, content: str, content_hash: str
    ) -> None:
        svc = get_service_client()
        await asyncio.to_thread(
            lambda: svc.table("document_versions")
            .update({"content_hash": content_hash})
            .eq("id", version_id)
            .execute()
        )

    # ── LLM ────────────────────────────────────────────────────────────

    async def _generate_summary(self, content: str) -> str:
        prompt = (
            "Summarise this document in 3–5 sentences. Focus on what it "
            "covers, who it's for, and the most consequential details. Plain "
            "prose, no bullets, no preamble.\n\n---\n"
            f"{content[:_SUMMARY_CONTENT_CAP]}\n---"
        )
        result = await execute_task_blocking(
            user_message=prompt,
            org_id=self.org_id,
            db_client=get_service_client(),
        )
        text = (result.text or "").strip()
        if result.error or not text:
            raise RuntimeError(result.error or "empty_summary")
        return text[:1500]

    async def _generate_diff(
        self, *, doc_name: str, previous: str, new_content: str,
    ) -> str:
        prompt = (
            f"Compare two versions of '{doc_name}' and list what changed.\n\n"
            "PREVIOUS VERSION:\n"
            f"{previous[:1500]}\n\n"
            "NEW VERSION (excerpt):\n"
            f"{new_content[:_DIFF_CONTENT_CAP]}\n\n"
            "List 3–5 bullets describing material changes. Start each bullet "
            "with an action verb (Added, Removed, Changed, Updated, Clarified). "
            "Reference section names, dates, or numbers wherever possible. If "
            "nothing material changed, output the single line "
            "'No material changes.'"
        )
        result = await execute_task_blocking(
            user_message=prompt,
            org_id=self.org_id,
            db_client=get_service_client(),
        )
        text = (result.text or "").strip()
        if result.error or not text:
            raise RuntimeError(result.error or "empty_diff")
        return text[:2000]

    async def _persist_diff(
        self,
        *,
        previous_version: dict[str, Any],
        active_version: dict[str, Any],
        diff_summary: str,
    ) -> str | None:
        svc = get_service_client()
        row = {
            "org_id": self.org_id,
            "document_id": self.document_id,
            "from_version_id": previous_version["id"],
            "to_version_id": active_version["id"],
            "from_version": previous_version["version_number"],
            "to_version": active_version["version_number"],
            "diff_summary": diff_summary,
            "agent_run_id": self.run_id,
        }
        try:
            res = await asyncio.to_thread(
                lambda: svc.table("document_diffs").insert(row).execute()
            )
            return ((res.data or [{}])[0]).get("id")
        except Exception as exc:
            # Likely UNIQUE(document_id, to_version_id) clash from a retry —
            # fetch the existing row id.
            if "duplicate key" in str(exc).lower():
                existing = await asyncio.to_thread(
                    lambda: svc.table("document_diffs")
                    .select("id")
                    .eq("document_id", self.document_id)
                    .eq("to_version_id", active_version["id"])
                    .maybe_single()
                    .execute()
                )
                return (existing.data or {}).get("id") if existing else None
            raise


async def should_run_diff(*, document_id: str, org_id: str) -> bool:
    """Cheap pre-trigger gate: skip if first version or if PolicyPropagation
    will handle it. Mirrors the no-op short-circuit in PolicyPropagationAgent
    so we don't pay for two LLM diff runs on the same upload."""
    svc = get_service_client()
    # Skip if there's no prior version.
    versions = await asyncio.to_thread(
        lambda: svc.table("document_versions")
        .select("id", count="exact")
        .eq("document_id", document_id)
        .execute()
    )
    if (versions.count or 0) < 2:
        return False

    # Skip if policy-tagged / ack-required (PolicyPropagationAgent owns it).
    doc = await asyncio.to_thread(
        lambda: svc.table("documents")
        .select("tags, requires_acknowledgement")
        .eq("id", document_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not doc or not doc.data:
        return False
    if doc.data.get("requires_acknowledgement"):
        return False
    tags = doc.data.get("tags") or []
    if isinstance(tags, list) and "policy" in [str(t).lower() for t in tags]:
        return False

    return True
