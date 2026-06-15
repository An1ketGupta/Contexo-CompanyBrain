"""PolicyPropagationAgent — autonomous policy-change broadcast (Agent Day 9).

Fires after a document tagged `policy` (or with `requires_acknowledgement=true`)
finishes processing. The agent:

  1. extract_content    Join the freshly-embedded chunks (ordered, archived
                        excluded) to reconstruct the version's text and hash
                        it. If the hash matches the previous version's, we
                        skip — the same file was re-uploaded.
  2. summarise          Generate a ~500-char précis of the current content
                        and persist it onto the active document_versions row.
                        The summary becomes the "previous" half of the next
                        run's diff prompt, so we never pay to summarise the
                        whole doc twice.
  3. diff               If a prior version exists, ask the LLM what changed.
                        We pass the previous version's chunk_summary plus the
                        first ~3k chars of new content — long enough for
                        narrative context, short enough to stay cheap.
                        Persisted to document_diffs.
  4. propagate_slack    Post to the configured policy channel (org_config →
                        Slack default channel). Best-effort: a Slack outage
                        doesn't fail the run.
  5. fan_out_acks       Bulk-insert one acknowledgements row per active org
                        member. Idempotent via the UNIQUE(document_id,
                        user_id, document_version_id) constraint — re-runs
                        skip existing rows.

Concurrency: Inngest pins us to one in-flight run per document_id so a fast
double-upload won't double-post. The agent also short-circuits on hash
match before any LLM call.
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.database import get_service_client
from app.observability import get_logger
from app.services.agents.base_agent import BaseAgent
from app.services.integrations import slack as slack_service
from app.services.llm.task_chain import execute_task_blocking
from app.services.org_config import get_org_config

log = get_logger(__name__)


# Cap the content shoved into LLM prompts. 12k chars is roughly 3k tokens —
# enough to summarise a policy doc, cheap enough not to hurt at scale. The
# diff prompt uses a tighter cap (3k) because the previous summary already
# carries the comparison context.
_SUMMARY_CONTENT_CAP = 12_000
_DIFF_CONTENT_CAP = 3_000


class PolicyPropagationAgent(BaseAgent):
    agent_type = "policy_propagation"

    def __init__(
        self,
        *,
        org_id: str,
        document_id: str,
        version_id: str | None = None,
        api_context: dict[str, Any] | None = None,
    ) -> None:
        input_data: dict[str, Any] = {
            "document_id": document_id,
            "version_id": version_id,
        }
        # Day 14: when fired through the public API we stash the trigger
        # context so BaseAgent's lifecycle fan-out can POST the callback.
        if api_context:
            input_data["_api_context"] = api_context
        super().__init__(
            org_id=org_id,
            input_data=input_data,
            triggered_by="api" if api_context else "document_ready",
        )
        self.document_id = document_id
        # Optional — when present, we operate strictly on that version row.
        # When absent, we resolve to the current is_current row at run time.
        self._explicit_version_id = version_id
        if api_context and api_context.get("run_id"):
            self.run_id = api_context["run_id"]

    # ── Main flow ──────────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        svc = get_service_client()

        # ── Step 1: load doc + chunks + current/previous versions ──────
        await self.log_step("load_document", "started")
        doc = await self._load_document()
        if not doc:
            await self.log_step("load_document", "failed", error="document_not_found")
            raise RuntimeError("document_not_found")

        active_version = await self._resolve_active_version()
        if not active_version:
            await self.log_step(
                "load_document",
                "skipped",
                {"reason": "no_version_row"},
            )
            # Pre-V4 docs that never had a version row created — bail.
            return {"propagated": False, "reason": "no_version_row"}

        previous_version = await self._resolve_previous_version(active_version)

        content = await self._extract_active_content()
        content_hash = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()
        await self.log_step(
            "load_document",
            "completed",
            {
                "name": doc.get("name"),
                "active_version": active_version["version_number"],
                "previous_version": (previous_version or {}).get("version_number"),
                "content_chars": len(content),
                "content_hash": content_hash[:10],
            },
        )

        # Short-circuit on no-op uploads (same file).
        if previous_version and previous_version.get("content_hash") == content_hash:
            await self.log_step(
                "summarise",
                "skipped",
                {"reason": "content_unchanged"},
            )
            await self._mark_propagated(active_version["id"])
            return {
                "propagated": False,
                "reason": "content_unchanged",
                "version_number": active_version["version_number"],
            }

        # ── Step 2: summarise current content (always — needed by the
        #            NEXT version's diff prompt) ──
        await self.log_step("summarise", "started")
        try:
            summary = await self._generate_summary(content)
        except Exception as exc:
            await self.log_step("summarise", "failed", error=str(exc))
            raise
        await asyncio.to_thread(
            lambda: svc.table("document_versions")
            .update({"chunk_summary": summary, "content_hash": content_hash})
            .eq("id", active_version["id"])
            .execute()
        )
        await self.log_step("summarise", "completed", {"summary_chars": len(summary)})

        # ── Step 3: diff against previous version (if any) ─────────────
        diff_summary: str | None = None
        diff_row_id: str | None = None
        if previous_version and (previous_version.get("chunk_summary") or "").strip():
            await self.log_step("diff", "started")
            try:
                diff_summary = await self._generate_diff(
                    doc_name=doc.get("name") or "this policy",
                    previous_summary=previous_version["chunk_summary"],
                    new_content=content,
                )
            except Exception as exc:
                await self.log_step("diff", "failed", error=str(exc))
                # A diff failure shouldn't block propagation — fall through
                # and post a "new version" notice instead of "what changed".
                diff_summary = None
            if diff_summary:
                diff_row_id = await self._persist_diff(
                    previous_version=previous_version,
                    active_version=active_version,
                    diff_summary=diff_summary,
                )
                await self.log_step(
                    "diff",
                    "completed",
                    {"diff_chars": len(diff_summary), "diff_id": diff_row_id},
                )
        else:
            await self.log_step(
                "diff",
                "skipped",
                {"reason": "no_previous_summary"},
            )

        # ── Step 4: Slack propagation (best-effort) ────────────────────
        slack_result = await self._maybe_post_slack(
            doc=doc,
            diff_summary=diff_summary,
            version_number=active_version["version_number"],
        )

        # ── Step 5: fan-out acknowledgement rows ───────────────────────
        ack_count = 0
        if doc.get("requires_acknowledgement") or self._has_policy_tag(doc):
            await self.log_step("fan_out_acks", "started")
            ack_count = await self._fan_out_acknowledgements(
                version_id=active_version["id"],
                diff_id=diff_row_id,
            )
            await self.log_step("fan_out_acks", "completed", {"count": ack_count})
        else:
            await self.log_step(
                "fan_out_acks",
                "skipped",
                {"reason": "not_marked_for_ack"},
            )

        await self._mark_propagated(active_version["id"])

        return {
            "propagated": True,
            "version_number": active_version["version_number"],
            "diff_id": diff_row_id,
            "diff_generated": diff_summary is not None,
            "slack_posted": bool(slack_result),
            "slack_channel": (slack_result or {}).get("channel"),
            "acknowledgements_created": ack_count,
        }

    # ── Data loaders ───────────────────────────────────────────────────

    async def _load_document(self) -> dict[str, Any] | None:
        svc = get_service_client()
        res = await asyncio.to_thread(
            lambda: svc.table("documents")
            .select(
                "id, org_id, name, tags, requires_acknowledgement, "
                "current_version_id, metadata, status"
            )
            .eq("id", self.document_id)
            .eq("org_id", self.org_id)
            .maybe_single()
            .execute()
        )
        return (res.data or None) if res else None

    async def _resolve_active_version(self) -> dict[str, Any] | None:
        """Pick the version row to treat as "current" for this run.

        If the trigger passed an explicit version_id we trust it (handles
        re-runs where is_current may have flipped). Otherwise we look for
        the doc's is_current row, then fall back to the highest version_number
        in case the flag hasn't been set yet (legacy V3 first uploads).
        """
        svc = get_service_client()

        if self._explicit_version_id:
            res = await asyncio.to_thread(
                lambda: svc.table("document_versions")
                .select("id, version_number, content_hash, chunk_summary, file_path")
                .eq("id", self._explicit_version_id)
                .eq("document_id", self.document_id)
                .maybe_single()
                .execute()
            )
            if res and res.data:
                return res.data

        current = await asyncio.to_thread(
            lambda: svc.table("document_versions")
            .select("id, version_number, content_hash, chunk_summary, file_path")
            .eq("document_id", self.document_id)
            .eq("is_current", True)
            .maybe_single()
            .execute()
        )
        if current and current.data:
            return current.data

        latest = await asyncio.to_thread(
            lambda: svc.table("document_versions")
            .select("id, version_number, content_hash, chunk_summary, file_path")
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
        """Join active chunks back into a flat string.

        We pull from `chunks` rather than re-downloading + re-parsing the
        source file — the chunks are the authoritative content the search
        engine sees, and they're already stripped of binary artefacts.
        Archived chunks are excluded by definition (they belong to a prior
        version).
        """
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
        rows = res.data or []
        # Soft-cap total length so a 500-page doc doesn't OOM the agent —
        # we only need the first few thousand chars for summary + diff.
        out: list[str] = []
        total = 0
        for r in rows:
            piece = (r.get("content") or "").strip()
            if not piece:
                continue
            out.append(piece)
            total += len(piece)
            if total >= _SUMMARY_CONTENT_CAP * 2:
                break
        return "\n\n".join(out)

    # ── LLM steps ──────────────────────────────────────────────────────

    async def _generate_summary(self, content: str) -> str:
        prompt = (
            "Summarise the following policy document in 4–6 sentences. "
            "Focus on what the document mandates, who it applies to, key "
            "numbers/dates, and the most consequential clauses. Plain prose, "
            "no bullets, no preamble like 'This document'.\n\n"
            "---\n"
            f"{content[:_SUMMARY_CONTENT_CAP]}\n"
            "---"
        )
        result = await execute_task_blocking(
            user_message=prompt,
            org_id=self.org_id,
            db_client=get_service_client(),
        )
        text = (result.text or "").strip()
        if result.error or not text:
            raise RuntimeError(result.error or "empty_summary")
        # Cap at 1500 chars — protects the diff prompt from runaway summaries.
        return text[:1500]

    async def _generate_diff(
        self,
        *,
        doc_name: str,
        previous_summary: str,
        new_content: str,
    ) -> str:
        prompt = (
            f"You are tracking what changed in the policy document '{doc_name}'.\n\n"
            "PREVIOUS VERSION (summary):\n"
            f"{previous_summary[:1500]}\n\n"
            "NEW VERSION (excerpt):\n"
            f"{new_content[:_DIFF_CONTENT_CAP]}\n\n"
            "List exactly 3–5 bullets describing what materially changed. "
            "Start each bullet with an action verb (Added, Removed, Changed, "
            "Updated, Clarified). Mention section names, dates, or numbers "
            "wherever possible. If nothing material changed, output the single "
            "line 'No material changes.'\n"
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
            # UNIQUE(document_id, to_version_id) clash → idempotent retry,
            # fetch the existing row id instead.
            if "duplicate key" not in str(exc).lower():
                raise
            existing = await asyncio.to_thread(
                lambda: svc.table("document_diffs")
                .select("id")
                .eq("document_id", self.document_id)
                .eq("to_version_id", active_version["id"])
                .maybe_single()
                .execute()
            )
            return ((existing.data or {}) if existing else {}).get("id")

    # ── Slack ──────────────────────────────────────────────────────────

    async def _maybe_post_slack(
        self,
        *,
        doc: dict[str, Any],
        diff_summary: str | None,
        version_number: int,
    ) -> dict[str, Any] | None:
        channel_id = await self._resolve_policy_channel()
        if not channel_id:
            await self.log_step(
                "propagate_slack",
                "skipped",
                {"reason": "no_channel_configured"},
            )
            return None

        text = self._build_slack_text(
            doc_name=doc.get("name") or "Policy document",
            diff_summary=diff_summary,
            version_number=version_number,
        )

        await self.log_step("propagate_slack", "started", {"channel": channel_id})
        try:
            result = await slack_service.post_message(
                org_id=self.org_id,
                channel_id=channel_id,
                text=text,
            )
        except PermissionError as exc:
            await self.log_step("propagate_slack", "skipped", {"reason": str(exc)})
            return None
        except Exception as exc:
            await self.log_step("propagate_slack", "failed", error=str(exc))
            return None

        await self.log_step("propagate_slack", "completed", result)
        return result

    async def _resolve_policy_channel(self) -> str | None:
        """Order of precedence:
          1. organizations.metadata.compliance.policy_channel_id
          2. organizations.metadata.default_slack_channel (legacy)
          3. None (skip the post — admin needs to configure one)
        """
        svc = get_service_client()
        res = await asyncio.to_thread(
            lambda: svc.table("organizations")
            .select("metadata")
            .eq("id", self.org_id)
            .maybe_single()
            .execute()
        )
        meta = ((res.data or {}).get("metadata") or {}) if res else {}
        compliance = meta.get("compliance") or {}
        channel = (
            compliance.get("policy_channel_id")
            or meta.get("default_slack_channel")
        )
        if isinstance(channel, str) and channel.strip():
            return channel.strip()
        return None

    def _build_slack_text(
        self,
        *,
        doc_name: str,
        diff_summary: str | None,
        version_number: int,
    ) -> str:
        settings = get_settings()
        app_url = settings.app_url.rstrip("/")
        link = f"{app_url}/documents/{self.document_id}"
        if diff_summary:
            return (
                f":scroll: *Policy update — {doc_name}* (v{version_number})\n\n"
                f"*What changed:*\n{diff_summary}\n\n"
                f"All members have been asked to acknowledge this update. "
                f"<{link}|Open document>."
            )
        return (
            f":scroll: *New policy — {doc_name}* (v{version_number})\n\n"
            f"This document has been added to Company Brain and requires "
            f"acknowledgement. <{link}|Open document>."
        )

    # ── Acknowledgement fan-out ────────────────────────────────────────

    async def _fan_out_acknowledgements(
        self,
        *,
        version_id: str,
        diff_id: str | None,
    ) -> int:
        svc = get_service_client()
        users = await asyncio.to_thread(
            lambda: svc.table("users")
            .select("id")
            .eq("org_id", self.org_id)
            .execute()
        )
        user_ids = [u["id"] for u in (users.data or []) if u.get("id")]
        if not user_ids:
            return 0

        rows = [
            {
                "org_id": self.org_id,
                "document_id": self.document_id,
                "document_version_id": version_id,
                "user_id": uid,
                "status": "pending",
                "agent_run_id": self.run_id,
                "diff_id": diff_id,
            }
            for uid in user_ids
        ]

        # Bulk insert with on_conflict — repeated runs are no-ops thanks to
        # the UNIQUE(document_id, user_id, document_version_id) constraint.
        try:
            res = await asyncio.to_thread(
                lambda: svc.table("acknowledgements")
                .upsert(rows, on_conflict="document_id,user_id,document_version_id", ignore_duplicates=True)
                .execute()
            )
            inserted = res.data or []
            return len(inserted) if inserted else len(rows)
        except Exception as exc:
            # Fall back to per-row insert if the bulk path collides badly
            # (older postgrest builds don't support ignore_duplicates).
            log.warning("ack_fanout_bulk_failed_falling_back", error=str(exc))
            inserted_n = 0
            for r in rows:
                try:
                    await asyncio.to_thread(
                        lambda r=r: svc.table("acknowledgements").insert(r).execute()
                    )
                    inserted_n += 1
                except Exception:
                    pass
            return inserted_n

    async def _mark_propagated(self, version_id: str) -> None:
        svc = get_service_client()
        try:
            await asyncio.to_thread(
                lambda: svc.table("documents")
                .update(
                    {
                        "current_version_id": version_id,
                        "last_propagated_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                .eq("id", self.document_id)
                .execute()
            )
        except Exception as exc:
            log.warning("mark_propagated_failed", document_id=self.document_id, error=str(exc))

    # ── Trigger condition ──────────────────────────────────────────────

    @staticmethod
    def _has_policy_tag(doc: dict[str, Any]) -> bool:
        tags = doc.get("tags") or []
        if not isinstance(tags, list):
            return False
        return any(isinstance(t, str) and t.lower() == "policy" for t in tags)


async def should_propagate(*, document_id: str, org_id: str) -> bool:
    """Cheap pre-check used by the Inngest trigger to skip non-policy docs
    without spinning up an agent run."""
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("documents")
        .select("tags, requires_acknowledgement")
        .eq("id", document_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    data = (res.data or {}) if res else {}
    if data.get("requires_acknowledgement"):
        return True
    tags = data.get("tags") or []
    if isinstance(tags, list):
        return any(isinstance(t, str) and t.lower() == "policy" for t in tags)
    return False
