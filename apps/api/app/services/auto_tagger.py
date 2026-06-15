"""Auto-tagger (Agent Day 12 Part B).

Runs as a post-ready Inngest step on every newly-processed document. The
agent reads the first ~1500 chars of content, asks the LLM to return a
JSON array of tags from a fixed taxonomy, then appends those tags to
`documents.tags` and flips `documents.auto_tagged = true`.

Design points worth keeping:

  * **Taxonomy is fixed and prompt-pinned.** The LLM is allowed to choose
    only from `TAXONOMY` — anything else is dropped during validation.
    This prevents tag-sprawl ("hr-policies", "human-resources", "HR")
    that would defeat the GIN index's usefulness for filter queries.
  * **Respect manual tags.** If a user has already tagged the doc, we
    *merge* — the agent only adds tags from the taxonomy that aren't
    already present. We never remove a manual tag.
  * **Best-effort, idempotent.** Re-running is safe (the merge dedupes).
    LLM failure → return {status: failed}; the doc is fully queryable
    and the tag chip simply doesn't appear.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from supabase import Client

from app.database import get_service_client
from app.services.langfuse import observe
from app.services.llm import LLMClient, Message, get_llm_client

log = logging.getLogger(__name__)

# Fixed taxonomy. Lowercase, hyphenated, ASCII. Keep small enough that the
# user-facing filter UI stays scannable (~20 chips). New categories require
# a deliberate code change so analytics dashboards stay stable.
TAXONOMY: tuple[str, ...] = (
    "policy",
    "hr",
    "onboarding",
    "recruiting",
    "legal",
    "finance",
    "engineering",
    "product",
    "sales",
    "marketing",
    "customer-success",
    "operations",
    "compliance",
    "handbook",
    "meeting-notes",
    "announcement",
    "security",
    "support",
)

# What the LLM sees. Limited content size keeps the call sub-cent and well
# under any free-tier rate limit. Title + first 1500 chars is plenty to
# distinguish "engineering handbook" from "Q3 marketing plan".
_CONTENT_CAP = 1500
# Pull a small number of leading chunks so we always see the doc's intro.
_CHUNK_LIMIT = 6

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)

_MAX_TAGS = 4
_MIN_TAGS = 1


def _build_prompt(*, title: str, content: str) -> str:
    """Prompt pins the taxonomy + format and forbids invented tags."""
    return (
        "Tag a document with categories from a FIXED list.\n\n"
        "Allowed categories (lowercase, exact spelling):\n  "
        f"{', '.join(TAXONOMY)}\n\n"
        "Rules:\n"
        f"  - Return ONLY a JSON array of {_MIN_TAGS} to {_MAX_TAGS} strings.\n"
        "  - Each string MUST be one of the allowed categories — no synonyms, no new tags.\n"
        "  - Choose the most specific applicable categories. Don't list 'policy' AND 'handbook' unless both clearly apply.\n"
        "  - No markdown fences, no preamble, no commentary. JSON array only.\n\n"
        "Example output:\n"
        '  ["hr", "policy", "handbook"]\n\n'
        f"Document title: {title}\n"
        f"Document content (truncated):\n{content}"
    )


@observe(name="auto_tag_document")
async def auto_tag_document(
    *,
    document_id: str,
    client: Client | None = None,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    """Idempotent — re-running merges new tags with whatever already exists.

    Returns a small dict for Inngest step output:
        {"status": "ok" | "skipped" | "failed", ...}
    """
    client = client or get_service_client()
    llm = llm or get_llm_client()

    doc = await asyncio.to_thread(
        lambda: client.table("documents")
        .select("id, name, status, tags, auto_tagged")
        .eq("id", document_id)
        .maybe_single()
        .execute()
    )
    if not doc or not doc.data:
        return {"status": "skipped", "reason": "document_not_found"}
    if doc.data.get("status") != "ready":
        return {"status": "skipped", "reason": "not_ready"}
    if doc.data.get("auto_tagged"):
        # Re-tagging an already-tagged doc is OK but wasteful; we skip by
        # default. A future "Re-tag" admin action can force-rerun by
        # clearing auto_tagged first.
        return {"status": "skipped", "reason": "already_tagged"}

    chunks_resp = await asyncio.to_thread(
        lambda: client.table("chunks")
        .select("content")
        .eq("document_id", document_id)
        .order("chunk_index")
        .limit(_CHUNK_LIMIT)
        .execute()
    )
    content = "\n\n".join(
        (c.get("content") or "") for c in (chunks_resp.data or [])
    ).strip()
    if not content:
        return {"status": "skipped", "reason": "no_content"}

    content = content[:_CONTENT_CAP]
    title = (doc.data.get("name") or "").strip() or "(untitled)"
    prompt = _build_prompt(title=title, content=content)

    try:
        response = await llm.complete(
            [Message(role="user", content=prompt)],
            tools=(),
            temperature=0.0,
        )
    except Exception as exc:
        log.warning(
            "auto_tag_document llm.complete failed: doc=%s err=%s",
            document_id, exc,
        )
        return {"status": "failed", "reason": "llm_error"}

    new_tags = _parse_tags(response.text or "")
    if not new_tags:
        log.info(
            "auto_tag_document unparseable: doc=%s raw=%r",
            document_id, (response.text or "")[:200],
        )
        return {"status": "failed", "reason": "unparseable"}

    # Merge with manual tags, dedupe, preserve order: manual first, then
    # new auto-tags. This way the user's curation is visible above
    # algorithmic noise.
    existing_tags = doc.data.get("tags") or []
    merged: list[str] = list(existing_tags)
    seen = {t for t in merged}
    for tag in new_tags:
        if tag not in seen:
            merged.append(tag)
            seen.add(tag)

    await asyncio.to_thread(
        lambda: client.table("documents")
        .update({"tags": merged, "auto_tagged": True})
        .eq("id", document_id)
        .execute()
    )

    return {
        "status": "ok",
        "tags_added": [t for t in new_tags if t not in existing_tags],
        "tag_count": len(merged),
    }


def _parse_tags(raw: str) -> list[str]:
    """Extract a JSON array of tags from the LLM response.

    Tolerant of: code fences, leading/trailing prose, malformed quoting.
    Anything not in TAXONOMY is dropped. Output is deduped, lowercased,
    and capped at _MAX_TAGS.
    """
    text = (raw or "").strip()
    if not text:
        return []

    text = _FENCE_RE.sub("", text).strip()
    if not text.startswith("["):
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end <= start:
            return []
        text = text[start : end + 1]

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []

    taxonomy_set = set(TAXONOMY)
    out: list[str] = []
    seen: set[str] = set()
    for item in parsed:
        if not isinstance(item, str):
            continue
        norm = item.strip().lower()
        if norm in taxonomy_set and norm not in seen:
            out.append(norm)
            seen.add(norm)
        if len(out) >= _MAX_TAGS:
            break
    return out
