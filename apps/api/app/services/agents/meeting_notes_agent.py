"""MeetingNotesAgent — structured extraction from uploaded transcripts (Day 13).

Triggered by `meeting/transcript-uploaded` after the regular doc pipeline
has run on a `.vtt` (Zoom) or `teams_transcript` (.json) file. Steps:

  1. load_transcript     Download from Supabase Storage and run the
                         appropriate parser (Zoom VTT vs Teams JSON).
                         Idempotent: an existing meeting_summaries row
                         short-circuits the run.
  2. extract             One LLM call returns a strict JSON object:
                         {summary, decisions, action_items, attendees_meta}.
                         We pin the schema in the prompt and validate it.
  3. persist             Save into meeting_summaries; also auto-tag the
                         source document with `meeting-notes` + `transcript`
                         so the docs UI surfaces them with the right chip.
  4. create_derived_doc  A new document "Meeting summary: …" carrying the
                         structured text body (decisions, action items,
                         then the dialog). Chunked + embedded via the
                         standard doc pipeline so semantic search hits the
                         summary first and the raw transcript second.
  5. post_to_slack       Optional. If a default org Slack channel exists,
                         post action items so owners can react instantly.

The agent never blocks the source doc's queryability; the transcript is
fully searchable as a doc the moment the regular pipeline marks it ready.
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import inngest

from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.observability import get_logger
from app.services.agents.base_agent import BaseAgent
from app.services.ingestion import download_from_storage
from app.services.integrations import slack as slack_service
from app.services.llm import Message, get_llm_client
from app.services.parsers.meeting_transcript import (
    MeetingTranscript,
    parse_transcript,
)

log = get_logger(__name__)

# LLM input cap. 12k chars ≈ ~3k tokens; enough for a 30-min transcript at
# typical speaking pace. Long meetings get truncated; the action-item /
# decision extraction targets the front of the meeting where most of the
# context is set, which empirically captures > 90% of the useful structure.
_LLM_INPUT_CAP = 12_000


_EXTRACTION_PROMPT = """You analyse meeting transcripts.

Read the transcript and respond with ONLY a JSON object. No markdown
fences, no commentary, no preamble. The object must have exactly these
keys:

{
  "summary": "<3-sentence factual summary of what the meeting covered>",
  "decisions": ["<one decision>", "..."],
  "action_items": [
    {
      "owner": "<speaker name from the transcript, or 'Unassigned'>",
      "task": "<single concrete task>",
      "due": "<date or 'unspecified'>"
    }
  ],
  "attendees_meta": [
    { "name": "<speaker label>", "role": "<inferred role or 'unknown'>" }
  ]
}

Rules:
- decisions: 0 to 8 items. Only items the meeting explicitly agreed on.
  Don't include "we should consider X" — that's a discussion, not a decision.
- action_items: 0 to 12 items. Owner MUST be a speaker that appears in the
  transcript, or "Unassigned" when no one accepted the task.
- attendees_meta: derive roles from context (titles mentioned, the kind of
  questions they asked). Unknown is acceptable.
- summary: 3 sentences. Factual, no marketing fluff.

Transcript:
{content}
"""

# Tolerant fence stripper used in document_summary; copied here so this
# module stays free of cross-cutting imports.
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


class MeetingNotesAgent(BaseAgent):
    agent_type = "meeting_notes"

    def __init__(self, *, org_id: str, document_id: str, file_type: str) -> None:
        super().__init__(
            org_id=org_id,
            input_data={"document_id": document_id, "file_type": file_type},
            triggered_by="document_ready",
        )
        self.document_id = document_id
        self.file_type = file_type

    # ── Main flow ──────────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        svc = get_service_client()

        # ── Idempotency: prior summary for this doc short-circuits ─────
        existing = await asyncio.to_thread(
            lambda: svc.table("meeting_summaries")
            .select("id")
            .eq("source_document_id", self.document_id)
            .maybe_single()
            .execute()
        )
        if existing and existing.data:
            await self.log_step(
                "load_transcript", "skipped",
                {"reason": "summary_exists", "id": existing.data["id"]},
            )
            return {"status": "skipped", "reason": "summary_exists"}

        # ── Step 1: load + parse the raw transcript ────────────────────
        await self.log_step("load_transcript", "started")
        doc_row = await asyncio.to_thread(
            lambda: svc.table("documents")
            .select("id, name, file_path, file_type, org_id, created_by, visibility")
            .eq("id", self.document_id)
            .maybe_single()
            .execute()
        )
        if not doc_row or not doc_row.data:
            await self.log_step("load_transcript", "failed", error="document_not_found")
            raise RuntimeError("document_not_found")
        doc = doc_row.data
        if doc.get("org_id") != self.org_id:
            await self.log_step("load_transcript", "failed", error="org_mismatch")
            raise RuntimeError("org_mismatch")

        try:
            file_bytes = await download_from_storage(doc["file_path"])
        except Exception as exc:
            await self.log_step("load_transcript", "failed", error=str(exc))
            raise
        try:
            content = file_bytes.decode("utf-8", errors="replace")
        except Exception as exc:
            await self.log_step("load_transcript", "failed", error=f"decode: {exc}")
            raise

        transcript = parse_transcript(file_type=self.file_type, content=content)
        if transcript.is_empty():
            await self.log_step(
                "load_transcript", "failed",
                error=f"empty_transcript:{transcript.format}",
            )
            return {"status": "failed", "reason": "empty_transcript"}

        await self.log_step(
            "load_transcript", "completed",
            {
                "format": transcript.format,
                "utterances": len(transcript.utterances),
                "speakers": len(transcript.speakers),
                "duration_ms": transcript.duration_ms,
            },
        )

        # ── Step 2: LLM extraction ─────────────────────────────────────
        await self.log_step("extract", "started")
        dialog = transcript.to_dialog_text(max_chars=_LLM_INPUT_CAP)
        prompt = _EXTRACTION_PROMPT.replace("{content}", dialog)
        try:
            resp = await get_llm_client().complete(
                [Message(role="user", content=prompt)],
                tools=(),
                temperature=0.1,
            )
        except Exception as exc:
            await self.log_step("extract", "failed", error=str(exc))
            raise

        extraction = _parse_extraction(resp.text or "")
        if not extraction:
            await self.log_step(
                "extract", "failed",
                error=f"unparseable:{(resp.text or '')[:200]}",
            )
            return {"status": "failed", "reason": "unparseable_extraction"}
        await self.log_step(
            "extract", "completed",
            {
                "decisions": len(extraction["decisions"]),
                "action_items": len(extraction["action_items"]),
            },
        )

        # ── Step 3: persist meeting_summaries row ──────────────────────
        await self.log_step("persist", "started")
        attendees = _build_attendees(transcript, extraction.get("attendees_meta", []))
        try:
            insert_row = {
                "org_id": self.org_id,
                "source_document_id": self.document_id,
                "agent_run_id": self.run_id,
                "source_format": transcript.format,
                "meeting_started_at": transcript.started_at,
                "meeting_duration_seconds": (
                    (transcript.duration_ms or 0) // 1000
                    if transcript.duration_ms else None
                ),
                "attendees": attendees,
                "decisions": extraction["decisions"],
                "action_items": extraction["action_items"],
                "summary": extraction["summary"],
            }
            result = await asyncio.to_thread(
                lambda: svc.table("meeting_summaries").insert(insert_row).execute()
            )
            summary_id = (result.data or [{}])[0].get("id")
            await self.log_step("persist", "completed", {"summary_id": summary_id})
        except Exception as exc:
            await self.log_step("persist", "failed", error=str(exc))
            raise

        # Tag the source document so it's surfaced under meeting-notes.
        try:
            await asyncio.to_thread(
                lambda: svc.rpc(
                    "set_document_tags_merge",
                    {"doc_id": self.document_id, "new_tags": ["meeting-notes"]},
                ).execute()
            )
        except Exception:
            # No RPC yet — fall back to a direct read-modify-write merge.
            await self._merge_source_doc_tags(["meeting-notes"])

        # ── Step 4: derived document with structured body ──────────────
        derived_doc_id = await self._create_derived_doc(
            transcript=transcript,
            extraction=extraction,
            attendees=attendees,
            source_doc_name=doc.get("name") or "Meeting transcript",
            created_by=doc.get("created_by"),
            summary_id=summary_id,
            visibility=doc.get("visibility") or "org",
        )

        # ── Step 5: best-effort Slack action-item ping ────────────────
        # Not for private transcripts (auto-synced Zoom/Meet recordings) —
        # posting their action items to a shared channel would broadcast the
        # very content the visibility gate keeps owner-only.
        if (doc.get("visibility") or "org") == "private":
            await self.log_step(
                "post_action_items", "skipped", {"reason": "private_source"}
            )
        else:
            await self._maybe_post_action_items_to_slack(
                extraction=extraction,
                summary_id=summary_id,
                derived_doc_id=derived_doc_id,
            )

        return {
            "summary_id": summary_id,
            "derived_doc_id": derived_doc_id,
            "decisions": len(extraction["decisions"]),
            "action_items": len(extraction["action_items"]),
            "attendees": len(attendees),
        }

    # ── Sub-steps ──────────────────────────────────────────────────────

    async def _merge_source_doc_tags(self, new_tags: list[str]) -> None:
        svc = get_service_client()
        try:
            row = await asyncio.to_thread(
                lambda: svc.table("documents")
                .select("tags")
                .eq("id", self.document_id)
                .maybe_single()
                .execute()
            )
            existing = (row.data or {}).get("tags") or []
            merged: list[str] = list(existing)
            seen = set(merged)
            for t in new_tags:
                if t not in seen:
                    merged.append(t)
                    seen.add(t)
            await asyncio.to_thread(
                lambda: svc.table("documents")
                .update({"tags": merged})
                .eq("id", self.document_id)
                .execute()
            )
        except Exception as exc:
            log.warning("meeting_tag_merge_failed", doc_id=self.document_id, error=str(exc))

    async def _create_derived_doc(
        self,
        *,
        transcript: MeetingTranscript,
        extraction: dict[str, Any],
        attendees: list[dict[str, Any]],
        source_doc_name: str,
        created_by: str | None,
        summary_id: str | None,
        visibility: str = "org",
    ) -> str | None:
        """Mint a "Meeting summary" doc that holds the structured body.

        We use the existing `doc/process-text` pipeline so the derived doc
        becomes chunked + embedded + searchable like any other text doc.
        Visibility is inherited from the source transcript — a private
        (auto-synced) transcript must not leak its summary org-wide.
        """
        svc = get_service_client()
        derived_id = str(uuid.uuid4())
        derived_name = f"Meeting summary: {source_doc_name[:160]}"
        # Storage path is a synthetic key. We never actually write a file —
        # process-text builds chunks from the in-memory text. The path is
        # only used to satisfy NOT NULL columns on documents.
        synthetic_path = f"orgs/{self.org_id}/derived/{derived_id}.txt"

        try:
            await asyncio.to_thread(
                lambda: svc.table("documents").insert({
                    "id": derived_id,
                    "org_id": self.org_id,
                    "name": derived_name,
                    "file_path": synthetic_path,
                    "file_type": "txt",
                    "file_size_bytes": 0,
                    "status": "pending",
                    "tags": ["meeting-notes"],
                    "auto_tagged": True,
                    "created_by": created_by,
                    "visibility": visibility,
                    "metadata": {
                        "source_document_id": self.document_id,
                        "meeting_summary_id": summary_id,
                    },
                }).execute()
            )
        except Exception as exc:
            log.warning("meeting_derived_doc_insert_failed", error=str(exc))
            return None

        # Mirror any document_shares (migration 089 — attendee auto-share)
        # from the source transcript onto the derived summary doc, so an
        # opted-in attendee who can read the transcript can also read its
        # decisions/action-items summary.
        try:
            shares = await asyncio.to_thread(
                lambda: svc.table("document_shares")
                .select("user_id")
                .eq("document_id", self.document_id)
                .execute()
            )
            share_rows = shares.data or []
            if share_rows:
                await asyncio.to_thread(
                    lambda: svc.table("document_shares")
                    .upsert(
                        [
                            {
                                "document_id": derived_id,
                                "org_id": self.org_id,
                                "user_id": row["user_id"],
                            }
                            for row in share_rows
                        ],
                        on_conflict="document_id,user_id",
                        ignore_duplicates=True,
                    )
                    .execute()
                )
        except Exception as exc:
            log.warning("meeting_derived_doc_share_cascade_failed", error=str(exc))

        body = _render_summary_body(
            transcript=transcript,
            extraction=extraction,
            attendees=attendees,
        )

        # Update the meeting_summaries row with the back-link.
        if summary_id:
            try:
                await asyncio.to_thread(
                    lambda: svc.table("meeting_summaries")
                    .update({"derived_document_id": derived_id})
                    .eq("id", summary_id)
                    .execute()
                )
            except Exception:
                pass

        try:
            client = get_inngest_client()
            await client.send(
                inngest.Event(
                    name="doc/process-text",
                    data={
                        "doc_id": derived_id,
                        "org_id": self.org_id,
                        "text": body,
                    },
                    id=f"meeting-derived-{derived_id}",
                )
            )
            await self.log_step(
                "create_derived_doc", "completed",
                {"derived_doc_id": derived_id},
            )
        except Exception as exc:
            await self.log_step("create_derived_doc", "failed", error=str(exc))
        return derived_id

    async def _maybe_post_action_items_to_slack(
        self,
        *,
        extraction: dict[str, Any],
        summary_id: str | None,
        derived_doc_id: str | None,
    ) -> None:
        items = extraction.get("action_items") or []
        if not items:
            await self.log_step("post_action_items", "skipped", {"reason": "no_items"})
            return

        svc = get_service_client()
        org_row = await asyncio.to_thread(
            lambda: svc.table("organizations")
            .select("metadata")
            .eq("id", self.org_id)
            .maybe_single()
            .execute()
        )
        meta = (org_row.data or {}).get("metadata") or {} if org_row and org_row.data else {}
        channel_id = (
            meta.get("meeting_channel_id")
            or meta.get("default_announcement_channel_id")
        )
        if not channel_id:
            await self.log_step(
                "post_action_items", "skipped",
                {"reason": "no_meeting_channel_configured"},
            )
            return

        lines = ["📋 *Action items from your meeting:*"]
        for item in items[:10]:
            owner = (item.get("owner") or "Unassigned").strip() or "Unassigned"
            task = (item.get("task") or "").strip()
            due = (item.get("due") or "").strip()
            if not task:
                continue
            suffix = f" — _by {due}_" if due and due.lower() not in {"unspecified", "tbd"} else ""
            lines.append(f"  • *{owner}*: {task}{suffix}")
        text = "\n".join(lines)

        await self.log_step(
            "post_action_items", "started",
            {"channel_id": channel_id, "count": len(items)},
        )
        try:
            result = await slack_service.post_message(
                org_id=self.org_id,
                channel_id=channel_id,
                text=text,
            )
            posted_at = datetime.now(UTC).isoformat()
            if summary_id:
                await asyncio.to_thread(
                    lambda: svc.table("meeting_summaries").update({
                        "slack_channel_id": channel_id,
                        "slack_message_ts": result.get("ts") if result else None,
                        "slack_posted_at": posted_at,
                    }).eq("id", summary_id).execute()
                )
            await self.log_step("post_action_items", "completed", result or {})
        except PermissionError as exc:
            await self.log_step("post_action_items", "skipped", {"reason": str(exc)})
        except Exception as exc:
            await self.log_step("post_action_items", "failed", error=str(exc))
        _ = derived_doc_id


# ── Pure helpers ───────────────────────────────────────────────────────────


def _parse_extraction(raw: str) -> dict[str, Any] | None:
    """Tolerant JSON extractor + schema validator.

    Returns a fully-typed dict on success, or None on parse failure.
    Field-level coercion: missing fields default to safe empties; weird
    types get coerced to strings.
    """
    text = (raw or "").strip()
    if not text:
        return None
    text = _FENCE_RE.sub("", text).strip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    summary = str(data.get("summary") or "").strip()[:1000]
    raw_decisions = data.get("decisions") or []
    decisions = [str(d).strip()[:500] for d in raw_decisions if isinstance(d, (str, int, float)) and str(d).strip()][:8]

    raw_items = data.get("action_items") or []
    action_items: list[dict[str, str]] = []
    if isinstance(raw_items, list):
        for item in raw_items[:12]:
            if not isinstance(item, dict):
                continue
            task = str(item.get("task") or "").strip()[:300]
            if not task:
                continue
            action_items.append({
                "owner": str(item.get("owner") or "Unassigned").strip()[:80] or "Unassigned",
                "task": task,
                "due": str(item.get("due") or "unspecified").strip()[:60] or "unspecified",
            })

    raw_attendees_meta = data.get("attendees_meta") or []
    attendees_meta: list[dict[str, str]] = []
    if isinstance(raw_attendees_meta, list):
        for a in raw_attendees_meta[:20]:
            if not isinstance(a, dict):
                continue
            name = str(a.get("name") or "").strip()[:80]
            if not name:
                continue
            attendees_meta.append({
                "name": name,
                "role": str(a.get("role") or "unknown").strip()[:80] or "unknown",
            })
    return {
        "summary": summary,
        "decisions": decisions,
        "action_items": action_items,
        "attendees_meta": attendees_meta,
    }


def _build_attendees(
    transcript: MeetingTranscript,
    attendees_meta: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Merge speaker labels from the parser with role metadata from the LLM.

    The parser's speakers list is authoritative for identity (it's what the
    transcript actually says) — we just decorate with utterance count and
    the LLM's role guess.
    """
    counts: dict[str, int] = {}
    for u in transcript.utterances:
        counts[u.speaker] = counts.get(u.speaker, 0) + 1
    meta_by_name: dict[str, str] = {a["name"]: a.get("role", "unknown") for a in attendees_meta}
    out: list[dict[str, Any]] = []
    for name in transcript.speakers:
        out.append({
            "name": name,
            "role": meta_by_name.get(name, "unknown"),
            "utterance_count": counts.get(name, 0),
        })
    return out


def _render_summary_body(
    *,
    transcript: MeetingTranscript,
    extraction: dict[str, Any],
    attendees: list[dict[str, Any]],
) -> str:
    """Plain-text body for the derived "Meeting summary" doc.

    Structure: summary → attendees → decisions → action items → dialog.
    Hybrid search loves this — the LLM-generated artifacts get indexed
    first and rank highly for "what was decided about X" queries.
    """
    parts: list[str] = []
    parts.append("# Meeting Summary\n")
    parts.append(extraction.get("summary") or "")
    parts.append("\n\n## Attendees\n")
    for a in attendees:
        parts.append(f"- {a['name']}  (role: {a.get('role') or 'unknown'}, {a.get('utterance_count', 0)} turns)")
    parts.append("\n\n## Decisions\n")
    decisions = extraction.get("decisions") or []
    if decisions:
        for d in decisions:
            parts.append(f"- {d}")
    else:
        parts.append("- (no explicit decisions recorded)")
    parts.append("\n\n## Action Items\n")
    items = extraction.get("action_items") or []
    if items:
        for item in items:
            parts.append(
                f"- {item.get('owner', 'Unassigned')}: {item.get('task', '')}"
                + (f" (due: {item['due']})" if item.get('due') and item['due'].lower() != 'unspecified' else "")
            )
    else:
        parts.append("- (no action items recorded)")
    parts.append("\n\n## Transcript\n")
    parts.append(transcript.to_dialog_text(max_chars=40_000))
    return "\n".join(parts)
