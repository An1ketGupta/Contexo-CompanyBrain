"""Outbound webhooks (Day 13 / #85 + #99).

Public surface is tiny: `trigger_event(org_id, event, payload)`. The function
fans matching webhooks into Inngest events; the actual HTTP POST + retry +
delivery-log row land in the `deliver-webhook` Inngest function (see
`app/inngest/functions.py`). Splitting transport off the hot path means a
customer's slow endpoint never taxes the chat or ingest call sites.

Event taxonomy — keep this list in sync with ALLOWED_EVENTS below and with
the UI's ALL_EVENTS array in apps/web/app/(dashboard)/settings/webhooks:
    document.processed       — doc finished ingesting, ready to query
    document.failed          — ingestion gave up after retries
    query.completed          — an assistant message landed successfully
    approval.requested       — an approval row was created (preview + approver)
    approval.decided         — an approval was approved/rejected
    compliance.acknowledged  — a user acknowledged a policy doc
    knowledge_gap.detected   — a topic crossed the gap threshold; stub drafted
    agent.completed / .failed — terminal status of an agent_runs row

Signature: when `secret` is set, the worker computes
    sha256 = hmac(secret, raw_json_body)
and sends `X-NirnayaIQ-Signature: sha256=<hex>`. Receivers verify by
recomputing — the constant-time compare is theirs to write.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable

import inngest

from app.database import get_service_client
from app.inngest.client import get_inngest_client

log = logging.getLogger(__name__)

# Whitelist of events we'll accept on webhook config (rejected elsewhere).
ALLOWED_EVENTS: tuple[str, ...] = (
    "document.processed",
    "document.failed",
    "query.completed",
    # Day 14: emitted on terminal status of every agent_runs row. Lets an
    # admin route onboarding/policy/support-response completions into an
    # external system without setting up the public API trigger first.
    "agent.completed",
    "agent.failed",
    # Workflow events — fired from approvals.py / compliance.py / the
    # knowledge-gap pipeline. See module docstring for payload shapes.
    "approval.requested",
    "approval.decided",
    "compliance.acknowledged",
    "knowledge_gap.detected",
)


async def trigger_event(
    *,
    org_id: str,
    event: str,
    payload: dict[str, Any],
) -> int:
    """Fan-out helper: enqueue one Inngest delivery event per matching webhook.

    Returns the number of webhooks queued (0 when nothing's configured for
    this event, which is the common case). Failures to enqueue are logged
    and swallowed — webhook delivery is best-effort by design.
    """
    if event not in ALLOWED_EVENTS:
        log.warning("webhook_event_rejected", event=event)
        return 0

    webhooks = await _matching_webhooks(org_id=org_id, event=event)
    if not webhooks:
        return 0

    client = get_inngest_client()
    queued = 0
    for hook in webhooks:
        try:
            await client.send(
                inngest.Event(
                    name="webhook/deliver",
                    data={
                        "webhook_id": hook["id"],
                        "org_id": org_id,
                        "event": event,
                        "payload": payload,
                    },
                )
            )
            queued += 1
        except Exception as exc:
            log.warning(
                "webhook_enqueue_failed",
                webhook_id=hook["id"],
                event=event,
                error=str(exc),
            )
    return queued


async def _matching_webhooks(
    *,
    org_id: str,
    event: str,
) -> list[dict[str, Any]]:
    """Active webhooks for this org whose `events` array contains `event`.

    Uses the service-role client because the hot path is the chat / Inngest
    worker — both already gate org_id at a higher layer, and avoiding RLS
    here saves a per-row policy eval on a query that may run on every chat
    turn.
    """
    svc = get_service_client()

    def _run() -> list[dict[str, Any]]:
        result = (
            svc.table("webhooks")
            .select("id, url, secret, events, name")
            .eq("org_id", org_id)
            .eq("is_active", True)
            .contains("events", [event])
            .execute()
        )
        return result.data or []

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        log.warning("webhook_lookup_failed", org_id=org_id, error=str(exc))
        return []


# ── DB helpers consumed by the Inngest delivery function ────────────────────

async def fetch_webhook(webhook_id: str) -> dict[str, Any] | None:
    svc = get_service_client()
    try:
        result = await asyncio.to_thread(
            lambda: svc.table("webhooks")
            .select("id, org_id, url, secret, events, is_active")
            .eq("id", webhook_id)
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        log.warning("webhook_fetch_failed", webhook_id=webhook_id, error=str(exc))
        return None
    return result.data if result else None


async def record_delivery(
    *,
    webhook_id: str,
    org_id: str,
    event: str,
    payload: dict[str, Any],
    status_code: int | None,
    response_body: str | None,
    error: str | None,
    attempt: int,
) -> None:
    """Persist one row in webhook_deliveries + update last_status on parent."""
    svc = get_service_client()
    row = {
        "webhook_id": webhook_id,
        "org_id": org_id,
        "event": event,
        "payload": payload,
        "status_code": status_code,
        # Cap response body to keep this table from ballooning if a customer
        # endpoint returns a 5MB HTML error page on every retry.
        "response_body": (response_body or "")[:2000] or None,
        "error": (error or "")[:500] or None,
        "attempt": attempt,
    }

    def _run() -> None:
        svc.table("webhook_deliveries").insert(row).execute()
        # Mirror last status to the webhook row for the list-view snapshot.
        # We do it in the same call site rather than as a DB trigger so
        # localised failures don't risk webhooks/deliveries diverging silently.
        svc.table("webhooks").update(
            {
                "last_status": status_code,
                "last_triggered_at": "now()",
            }
        ).eq("id", webhook_id).execute()

    try:
        await asyncio.to_thread(_run)
    except Exception as exc:
        # Telemetry write — log and drop. The user-facing delivery has already
        # happened (or failed) by the time we get here.
        log.warning(
            "webhook_delivery_record_failed",
            webhook_id=webhook_id,
            event=event,
            error=str(exc),
        )


def _iter_chunks(seq: Iterable[Any], size: int) -> Iterable[list[Any]]:
    """Tiny chunker used by callers that want to paginate batch writes."""
    batch: list[Any] = []
    for item in seq:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
