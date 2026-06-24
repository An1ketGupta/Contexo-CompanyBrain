"""Auto-generated 2-3 sentence summary + key topic tags for an ingested document.

Runs as a post-ready Inngest step so it never blocks user-visible status. The
output goes into `documents.metadata`:

    metadata.summary               TEXT  (2-3 sentence overview)
    metadata.key_topics            TEXT[] (5 short topic tags, lowercased)
    metadata.summary_generated_at  ISO timestamp

If the LLM call fails or returns non-JSON we no-op — the document is still
fully queryable, the chips just won't appear.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from supabase import Client

from app.database import get_service_client
from app.services.langfuse import observe
from app.services.llm import LLMClient, Message, get_llm_client

log = logging.getLogger(__name__)

# Total content fed to the model. 8k chars ≈ 2k tokens; with a 300-token output
# cap we stay well under any free-tier rate limit and the summary cost is < $0.0002.
_MAX_CONTENT_CHARS = 8_000
# How many ordered chunks to pull. With our default 800-token chunks this covers
# the first ~12k tokens of the document and is always long enough to fill the
# 8k char budget above, even with very small chunks.
_CHUNK_LIMIT = 20
_SUMMARY_MAX_CHARS = 600
_TOPIC_MAX_CHARS = 40

_SUMMARY_PROMPT = """You are a document indexing assistant. Read the document below and respond with ONLY a JSON object — no markdown fences, no preamble, no trailing commentary.

Required shape:
{
  "summary": "2 to 3 sentences describing what this document covers, who it's for, and its primary purpose. Factual, no marketing fluff.",
  "key_topics": ["topic one", "topic two", "topic three", "topic four", "topic five"]
}

Rules:
- summary: 2-3 complete sentences. No bullet lists.
- key_topics: exactly 5 short lowercase phrases, 2-4 words each. Concrete nouns, not verbs.
- If the document is too short or generic for 5 distinct topics, return fewer (minimum 1) — do not invent.

Document:
{content}"""

# Tolerant fence stripper. Gemini occasionally wraps JSON in ```json … ``` despite
# the prompt; production logs showed ~3% of calls do this. Strip both forms.
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


@observe(name="generate_document_summary")
async def generate_document_summary(
    *,
    document_id: str,
    client: Client | None = None,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    """Idempotent: re-running on a doc that already has a summary will overwrite it.

    Returns a small dict for Inngest step output / log payloads:
        {"status": "ok" | "skipped" | "failed", ...}
    """
    client = client or get_service_client()
    llm = llm or get_llm_client()

    doc = await asyncio.to_thread(
        lambda: client.table("documents")
        .select("id, status, metadata")
        .eq("id", document_id)
        .maybe_single()
        .execute()
    )
    if not doc or not doc.data:
        return {"status": "skipped", "reason": "document_not_found"}
    if doc.data.get("status") != "ready":
        return {"status": "skipped", "reason": "not_ready"}

    chunks_resp = await asyncio.to_thread(
        lambda: client.table("chunks")
        .select("content")
        .eq("document_id", document_id)
        .order("chunk_index")
        .limit(_CHUNK_LIMIT)
        .execute()
    )
    chunks = chunks_resp.data or []
    content = "\n\n".join(c["content"] for c in chunks if c.get("content"))
    content = content.strip()
    if not content:
        return {"status": "skipped", "reason": "no_content"}

    # Cap input — anything beyond the cap is unlikely to materially change the
    # 2-sentence summary, and the cap protects free-tier rate limits.
    if len(content) > _MAX_CONTENT_CHARS:
        content = content[:_MAX_CONTENT_CHARS]

    prompt = _SUMMARY_PROMPT.replace("{content}", content)

    try:
        response = await llm.complete(
            [Message(role="user", content=prompt)],
            tools=(),
            temperature=0.1,
        )
    except Exception as exc:
        log.warning("generate_document_summary llm.complete failed: doc=%s err=%s",
                    document_id, exc)
        return {"status": "failed", "reason": "llm_error"}

    summary, key_topics = _parse_summary_response(response.text or "")
    if not summary and not key_topics:
        log.info("generate_document_summary empty/unparseable output: doc=%s raw=%r",
                 document_id, (response.text or "")[:200])
        return {"status": "failed", "reason": "unparseable"}

    # Merge into existing metadata so we never clobber `embedding`, `toc`, etc.
    existing = doc.data.get("metadata") or {}
    if not isinstance(existing, dict):
        existing = {}
    next_metadata = {
        **existing,
        "summary": summary,
        "key_topics": key_topics,
        "summary_generated_at": datetime.now(UTC).isoformat(),
    }

    # Agent2 Day 2 #14: also embed the summary so duplicate detection has
    # a doc-level vector to compare against. We embed the concatenation of
    # the document name + summary + key topics — names alone are too short
    # for a stable cosine, and summaries alone miss obvious title overlaps.
    # Best-effort: if embedding fails we still persist the summary text.
    summary_embedding: list[float] | None = None
    summary_embed_input = " ".join(
        [doc.data.get("name") or "", summary, " ".join(key_topics)]
    ).strip()
    if summary_embed_input:
        try:
            from app.services.ingestion.embedder import get_embedder

            embedder = get_embedder()
            vecs = await embedder.embed_texts(
                [summary_embed_input], task_type="RETRIEVAL_DOCUMENT"
            )
            if vecs and len(vecs[0]) > 0:
                summary_embedding = list(vecs[0])
        except Exception as exc:
            log.warning(
                "summary_embedding_failed doc=%s err=%s", document_id, exc
            )

    update_payload: dict[str, Any] = {"metadata": next_metadata}
    if summary_embedding is not None:
        update_payload["summary_embedding"] = summary_embedding
        update_payload["summary_embedded_at"] = datetime.now(UTC).isoformat()

    await asyncio.to_thread(
        lambda: client.table("documents")
        .update(update_payload)
        .eq("id", document_id)
        .execute()
    )

    # Fire-and-forget: queue a duplicate scan for this doc now that we have
    # a fresh summary embedding. Skipped silently if either the embedding
    # write failed above or the Inngest enqueue itself fails.
    if summary_embedding is not None:
        try:
            import inngest as _inngest_pkg

            from app.inngest.client import get_inngest_client

            await get_inngest_client().send(
                _inngest_pkg.Event(
                    name="document/duplicate.scan",
                    data={
                        "document_id": document_id,
                        "org_id": doc.data["org_id"],
                    },
                )
            )
        except Exception as exc:
            log.warning(
                "duplicate_scan_enqueue_failed doc=%s err=%s", document_id, exc
            )

    return {
        "status": "ok",
        "summary_len": len(summary),
        "topic_count": len(key_topics),
        "embedded_summary": summary_embedding is not None,
    }


def _parse_summary_response(raw: str) -> tuple[str, list[str]]:
    """Extract summary + key_topics from the LLM's JSON response.

    Tolerant of: code fences, leading/trailing prose, single trailing newlines,
    and missing key_topics (returns []).
    """
    text = raw.strip()
    if not text:
        return "", []

    # Strip ```json ... ``` fences if present.
    text = _FENCE_RE.sub("", text).strip()

    # Some models like to prefix "Here is the JSON:" — grab the first {...} block.
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return "", []
        text = text[start : end + 1]

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return "", []
    if not isinstance(data, dict):
        return "", []

    summary = data.get("summary")
    if not isinstance(summary, str):
        summary = ""
    summary = summary.strip()
    if len(summary) > _SUMMARY_MAX_CHARS:
        summary = summary[:_SUMMARY_MAX_CHARS].rstrip() + "…"

    topics_raw = data.get("key_topics") or []
    if not isinstance(topics_raw, list):
        topics_raw = []
    topics: list[str] = []
    for t in topics_raw:
        if not isinstance(t, str):
            continue
        t = t.strip().lower()
        if not t:
            continue
        if len(t) > _TOPIC_MAX_CHARS:
            t = t[:_TOPIC_MAX_CHARS].rstrip()
        if t not in topics:
            topics.append(t)
        if len(topics) >= 5:
            break

    return summary, topics
