"""Knowledge-gap pipeline (Agent Day 5).

Three Inngest functions form the pipeline:

  1. knowledge_gap_detected   — fired on every zero-hit search.
       Persists the gap row, counts recent occurrences for the same topic,
       triggers (3) when the threshold is crossed.

  2. knowledge_gap_threshold  — fired when a topic hits the alert threshold.
       Debounced 24h per (org, topic) so a chatty topic doesn't spam admins.
       Runs the AI stub draft (via execute_task) and emails the admins.

  3. (No third function — approval ingest is a regular `doc/process-text`
       event fired from the FastAPI approval endpoint.)

The threshold is hard-coded at 3 because:
    * 1 is noise (one user asked once → no signal)
    * 2 could be the same user retrying
    * 3 in a 7d window is "real demand"
If admins want to tune it, the dial belongs in the same per-org settings
surface as the confidence thresholds — adding it is one config field +
one read in the threshold check.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import inngest

from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.observability import get_logger
from app.services.webhooks import trigger_event as trigger_webhook_event

log = get_logger(__name__)

_inngest_client = get_inngest_client()

# Window we look back over when counting occurrences. 7 days is short enough
# that "an old topic resurfacing" registers as new, long enough to absorb
# weekday/weekend usage gaps in low-traffic orgs.
_GAP_WINDOW_DAYS = 7
_GAP_THRESHOLD = 3


@_inngest_client.create_function(
    fn_id="knowledge-gap-detected",
    trigger=inngest.TriggerEvent(event="knowledge/gap-detected"),
    retries=2,
)
async def knowledge_gap_detected(ctx: inngest.Context) -> dict[str, Any]:
    step = ctx.step
    data = ctx.event.data

    org_id: str = data["org_id"]
    topic: str = (data.get("topic") or "").strip()
    if not topic:
        return {"status": "skipped", "reason": "empty_topic"}

    # Step 1: persist. RLS bypassed via service-role.
    await step.run(
        "persist-gap",
        lambda: _insert_gap(
            org_id=org_id,
            topic=topic,
            query=data.get("query") or "",
            user_id=data.get("user_id"),
            conversation_id=data.get("conversation_id"),
        ),
    )

    # Step 2: count occurrences in the rolling window.
    count = await step.run(
        "count-topic-gaps",
        lambda: _count_topic_gaps(org_id=org_id, topic=topic, days=_GAP_WINDOW_DAYS),
    )

    # Step 3: fan to the threshold worker when crossed. The threshold worker
    # is debounced per (org, topic) so duplicate fires here are harmless.
    if count >= _GAP_THRESHOLD:
        client = get_inngest_client()
        await client.send(
            inngest.Event(
                name="knowledge/gap-threshold-hit",
                data={"org_id": org_id, "topic": topic, "count": count},
            )
        )
        return {"status": "threshold-hit", "count": count}

    return {"status": "recorded", "count": count}


@_inngest_client.create_function(
    fn_id="knowledge-gap-threshold",
    trigger=inngest.TriggerEvent(event="knowledge/gap-threshold-hit"),
    retries=2,
    # Debounce — one alert + draft per (org, topic) per 24h. Without this,
    # a topic that keeps recurring (e.g. people keep asking "vacation
    # policy") would generate a new draft every search after the third.
    debounce=inngest.Debounce(
        period=timedelta(hours=24),
        key="event.data.org_id + '-' + event.data.topic",
    ),
)
async def knowledge_gap_threshold(ctx: inngest.Context) -> dict[str, Any]:
    step = ctx.step
    data = ctx.event.data

    org_id: str = data["org_id"]
    topic: str = data["topic"]
    count: int = int(data.get("count") or _GAP_THRESHOLD)

    # Step 1: short-circuit if a pending draft already exists for this topic.
    # The unique partial index on document_drafts(org, gap_topic) where status =
    # 'pending_review' guarantees DB-level safety; checking here saves an LLM
    # call when the worker re-fires after a transient failure.
    existing = await step.run(
        "check-existing-draft",
        lambda: _has_pending_draft(org_id=org_id, topic=topic),
    )
    if existing:
        log.info("knowledge_gap_threshold_existing_draft", org_id=org_id, topic=topic)
        return {"status": "skipped", "reason": "draft_already_pending"}

    # Step 2: generate the stub via the existing execute_task pipeline so the
    # stub uses real org context for any partial information AND surfaces
    # citations the admin can verify against. Bounded by the same tool-rounds
    # / chunk caps as a regular chat turn — no separate cost ceiling needed.
    stub = await step.run(
        "draft-stub-document",
        lambda: _generate_stub(org_id=org_id, topic=topic, count=count),
    )

    # Step 3: persist the draft. The unique partial index protects against a
    # tight race where two workers both passed the existence check.
    draft_id = await step.run(
        "save-draft",
        lambda: _insert_draft(
            org_id=org_id,
            topic=topic,
            count=count,
            stub_title=stub["title"],
            stub_content=stub["content"],
        ),
    )

    # Step 4: notify the org admins. Fire-and-forget — the draft is already
    # saved; a missed email isn't blocking. The email template links the
    # admin straight to the review dialog so the path is two clicks.
    if draft_id:
        await step.run(
            "notify-admins",
            lambda: _notify_admins(
                org_id=org_id,
                topic=topic,
                count=count,
                draft_id=draft_id,
                stub_preview=(stub["content"][:600] + "…") if len(stub["content"]) > 600 else stub["content"],
            ),
        )

    # Outbound webhook. Fires at threshold-hit (debounced 24h by Inngest)
    # so receivers get one notification per topic-per-day, not one per
    # zero-hit search. draft_id is null when the unique-index race fired.
    try:
        await trigger_webhook_event(
            org_id=org_id,
            event="knowledge_gap.detected",
            payload={
                "topic": topic,
                "count": count,
                "draft_id": draft_id,
                "review_url_path": f"/admin/knowledge-gaps?draft={draft_id}" if draft_id else None,
            },
        )
    except Exception as exc:
        log.warning(
            "knowledge_gap_webhook_failed",
            org_id=org_id,
            topic=topic,
            error=str(exc),
        )

    return {"status": "drafted", "draft_id": draft_id}


# ── Internals ───────────────────────────────────────────────────────────────


def _insert_gap(
    *,
    org_id: str,
    topic: str,
    query: str,
    user_id: str | None,
    conversation_id: str | None,
) -> None:
    svc = get_service_client()
    row = {
        "org_id": org_id,
        "topic": topic[:500],
        "query": query[:2000],
        "user_id": user_id,
        "conversation_id": conversation_id,
    }
    svc.table("knowledge_gaps").insert(row).execute()


def _count_topic_gaps(*, org_id: str, topic: str, days: int) -> int:
    svc = get_service_client()
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    res = (
        svc.table("knowledge_gaps")
        .select("id", count="exact", head=True)
        .eq("org_id", org_id)
        .eq("topic", topic[:500])
        .gte("created_at", cutoff)
        .execute()
    )
    return int(res.count or 0)


def _has_pending_draft(*, org_id: str, topic: str) -> bool:
    svc = get_service_client()
    res = (
        svc.table("document_drafts")
        .select("id", count="exact", head=True)
        .eq("org_id", org_id)
        .eq("gap_topic", topic[:500])
        .eq("status", "pending_review")
        .execute()
    )
    return (res.count or 0) > 0


async def _generate_stub(*, org_id: str, topic: str, count: int) -> dict[str, str]:
    """Run the LLM through execute_task with a stub-writing prompt.

    Using execute_task (not a one-off LLM call) means the stub draws on any
    *partial* information already in the org's knowledge base — if a related
    doc exists, the LLM cites it; if nothing exists, the stub is honest
    about the gap (the chat's knowledge-gap branch will trigger inside the
    nested execute_task call too, which is fine — we surface the resulting
    text either way).
    """
    from supabase import Client as SupabaseClient

    from app.database import get_service_client
    from app.services.llm.task_chain import execute_task_blocking

    prompt = (
        f"Write a stub knowledge-base document about: \"{topic}\".\n\n"
        f"Context: this topic has been asked about {count} times in the last "
        "week, and our knowledge base has nothing on it. Your job is to "
        "produce a draft document an internal subject-matter expert can "
        "quickly review and edit, not a finished policy.\n\n"
        "Structure:\n"
        "1. Start with a one-line summary of what this document is about.\n"
        "2. Bullet 3–6 questions or sub-topics the doc should cover.\n"
        "3. For anything you DO have org context on, draft a short answer "
        "and cite the source.\n"
        "4. For anything you DON'T, write `[NEEDS INPUT: ...]` so the admin "
        "can see exactly what to fill in.\n\n"
        "Keep the tone neutral and internal. No marketing language."
    )

    svc: SupabaseClient = get_service_client()
    try:
        result = await execute_task_blocking(
            user_message=prompt,
            org_id=org_id,
            db_client=svc,
        )
    except Exception as exc:
        log.warning("knowledge_gap_stub_llm_failed", org_id=org_id, topic=topic, error=str(exc))
        return {
            "title": f"[STUB] {topic[:160]}",
            "content": (
                f"# {topic}\n\n"
                f"_AI-drafted stub could not be generated automatically — "
                f"please write this document manually. This topic has been "
                f"asked about {count} times in the last week._\n"
            ),
        }

    body = result.text or ""
    if result.sources:
        names = sorted({s.get("document_name") for s in result.sources if s.get("document_name")})
        if names:
            body += "\n\n---\n_Source documents consulted: " + ", ".join(names) + "_"

    return {
        "title": f"[STUB] {topic[:160]}",
        "content": body.strip()
        or (
            f"# {topic}\n\n"
            f"_The AI couldn't draft this stub. Asked {count} times in the "
            "last week._\n"
        ),
    }


def _insert_draft(
    *,
    org_id: str,
    topic: str,
    count: int,
    stub_title: str,
    stub_content: str,
) -> str | None:
    svc = get_service_client()
    row = {
        "org_id": org_id,
        "title": stub_title,
        "content": stub_content,
        "source": "knowledge_gap_autoflow",
        "gap_topic": topic[:500],
        "gap_count": count,
        "status": "pending_review",
    }
    try:
        res = svc.table("document_drafts").insert(row).execute()
        if res.data:
            return res.data[0]["id"]
        return None
    except Exception as exc:
        # Most likely the unique partial index fired — another worker raced
        # us. That's the protection we want; treat it as success and move on.
        if "duplicate key" in str(exc).lower():
            log.info("knowledge_gap_draft_race", org_id=org_id, topic=topic)
            return None
        raise


async def _notify_admins(
    *,
    org_id: str,
    topic: str,
    count: int,
    draft_id: str,
    stub_preview: str,
) -> None:
    from app.config import get_settings
    from app.services.email import send_email_event

    settings = get_settings()
    svc = get_service_client()

    def _resolve_admins() -> list[tuple[str, str, str | None]]:
        rows = (
            svc.table("users")
            .select("id, org_id, display_name")
            .eq("org_id", org_id)
            .eq("role", "admin")
            .execute()
        )
        out: list[tuple[str, str, str | None]] = []
        for u in rows.data or []:
            try:
                au = svc.auth.admin.get_user_by_id(u["id"])
                email = getattr(getattr(au, "user", None), "email", None)
                if email:
                    out.append((u["id"], email, u.get("display_name")))
            except Exception:
                continue
        return out

    admins = await asyncio.to_thread(_resolve_admins)
    if not admins:
        log.info("knowledge_gap_no_admins", org_id=org_id)
        return

    review_url = f"{settings.app_url.rstrip('/')}/admin/knowledge-gaps?draft={draft_id}"

    # Dedupe key per-draft so re-firing this step doesn't double-send.
    for admin_id, email, _name in admins:
        await send_email_event(
            event_type="knowledge_gap_alert",  # type: ignore[arg-type]
            to=email,
            user_id=admin_id,
            org_id=org_id,
            dedupe_key=f"draft-{draft_id}",
            data={
                "topic": topic,
                "count": count,
                "stub_preview": stub_preview,
                "review_url": review_url,
                "app_url": settings.app_url,
            },
        )


FUNCTIONS = [knowledge_gap_detected, knowledge_gap_threshold]
