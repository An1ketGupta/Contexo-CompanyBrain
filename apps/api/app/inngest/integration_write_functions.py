"""Inngest workers for outbound writes to external destinations.

Day 4 of the Agent roadmap: push AI-generated content to Notion (create
page) and Google Docs (create doc + share with requester).

Same shape as `gmail_functions` / `slack_functions`:
    * One Inngest function per destination
    * PermissionError → mark delivery_status='failed' (no retry)
    * Other exceptions → propagate so Inngest retries with backoff
    * Success → mark delivery_status='sent' with external_id + url
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import inngest

from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.services.integrations import drive as drive_service
from app.services.integrations import notion as notion_service

log = logging.getLogger(__name__)

_inngest_client = get_inngest_client()


async def _mark_delivery(
    *,
    message_id: str,
    org_id: str,
    delivery: dict[str, Any],
) -> None:
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("messages")
        .update({"delivery_status": delivery})
        .eq("id", message_id)
        .eq("org_id", org_id)
        .execute()
    )


# ── Notion ──────────────────────────────────────────────────────────────────


@_inngest_client.create_function(
    fn_id="notion-create-page",
    trigger=inngest.TriggerEvent(event="notion/create-page"),
    retries=3,
    # Per-org concurrency cap — Notion's REST API is rate-limited around
    # 3 req/s per integration; one create-page in flight per org is plenty.
    concurrency=[inngest.Concurrency(limit=1, key="event.data.org_id", scope="fn")],
)
async def notion_create_page(ctx: inngest.Context) -> dict[str, Any]:
    step = ctx.step
    data = ctx.event.data

    job_id: str = data["job_id"]
    message_id: str = data["message_id"]
    org_id: str = data["org_id"]
    user_id: str | None = data.get("user_id")
    conversation_id: str | None = data.get("conversation_id")
    parent_page_id: str = data["parent_page_id"]
    parent_page_title: str = data.get("parent_page_title") or "Notion"
    title: str = data["title"]
    content: str = data["content"]

    from app.services.outbound_postwrite import on_failed, on_sent

    try:
        result = await step.run(
            "create-page",
            lambda: notion_service.create_page(
                org_id=org_id,
                parent_page_id=parent_page_id,
                title=title,
                content=content,
            ),
        )
    except PermissionError as exc:
        reason = str(exc) or "notion_unauthorized"
        await _mark_delivery(
            message_id=message_id,
            org_id=org_id,
            delivery={
                "channel": "notion",
                "status": "failed",
                "destination": parent_page_title,
                "job_id": job_id,
                "error": reason,
                "failed_at": datetime.now(UTC).isoformat(),
            },
        )
        await on_failed(
            run_id=job_id,
            channel="notion",
            org_id=org_id,
            user_id=user_id,
            message_id=message_id,
            conversation_id=conversation_id,
            destination=parent_page_title,
            reason=reason,
            permanent=True,
        )
        return {"status": "failed", "reason": reason}

    await step.run(
        "record-delivery",
        lambda: _mark_delivery(
            message_id=message_id,
            org_id=org_id,
            delivery={
                "channel": "notion",
                "status": "sent",
                "destination": parent_page_title,
                "job_id": job_id,
                "external_id": result.get("page_id"),
                "url": result.get("url"),
                "delivered_at": datetime.now(UTC).isoformat(),
            },
        ),
    )

    await on_sent(
        run_id=job_id,
        channel="notion",
        org_id=org_id,
        message_id=message_id,
        destination=parent_page_title,
        external_id=result.get("page_id"),
        url=result.get("url"),
    )

    return {"status": "sent", "page_id": result.get("page_id"), "url": result.get("url")}


# ── Google Docs ─────────────────────────────────────────────────────────────


@_inngest_client.create_function(
    fn_id="gdocs-create-doc",
    trigger=inngest.TriggerEvent(event="gdocs/create-doc"),
    retries=3,
    # Docs's per-user write quota is generous (around 60/min) but bursty
    # callers can still trip it. One per org is conservative and matches
    # the human-driven cadence of "user clicks Export".
    concurrency=[inngest.Concurrency(limit=1, key="event.data.org_id", scope="fn")],
)
async def gdocs_create_doc(ctx: inngest.Context) -> dict[str, Any]:
    step = ctx.step
    data = ctx.event.data

    job_id: str = data["job_id"]
    message_id: str = data["message_id"]
    org_id: str = data["org_id"]
    user_id: str | None = data.get("user_id")
    conversation_id: str | None = data.get("conversation_id")
    title: str = data["title"]
    content: str = data["content"]
    share_with_email: str | None = data.get("share_with_email")

    from app.services.outbound_postwrite import on_failed, on_sent

    try:
        result = await step.run(
            "create-doc",
            lambda: drive_service.create_document(
                org_id=org_id,
                title=title,
                content=content,
                share_with_email=share_with_email,
            ),
        )
    except PermissionError as exc:
        reason = str(exc) or "docs_unauthorized"
        await _mark_delivery(
            message_id=message_id,
            org_id=org_id,
            delivery={
                "channel": "gdocs",
                "status": "failed",
                "destination": "Google Docs",
                "job_id": job_id,
                "error": reason,
                "failed_at": datetime.now(UTC).isoformat(),
            },
        )
        await on_failed(
            run_id=job_id,
            channel="gdocs",
            org_id=org_id,
            user_id=user_id,
            message_id=message_id,
            conversation_id=conversation_id,
            destination="Google Docs",
            reason=reason,
            permanent=True,
        )
        return {"status": "failed", "reason": reason}

    await step.run(
        "record-delivery",
        lambda: _mark_delivery(
            message_id=message_id,
            org_id=org_id,
            delivery={
                "channel": "gdocs",
                "status": "sent",
                "destination": "Google Docs",
                "job_id": job_id,
                "external_id": result.get("doc_id"),
                "url": result.get("url"),
                "delivered_at": datetime.now(UTC).isoformat(),
            },
        ),
    )

    await on_sent(
        run_id=job_id,
        channel="gdocs",
        org_id=org_id,
        message_id=message_id,
        destination="Google Docs",
        external_id=result.get("doc_id"),
        url=result.get("url"),
    )

    return {"status": "sent", "doc_id": result.get("doc_id"), "url": result.get("url")}


FUNCTIONS = [notion_create_page, gdocs_create_doc]
