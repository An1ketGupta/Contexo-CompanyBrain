"""Support-agent Inngest pipeline (Agent Day 12).

Two functions live here:

    classify-inbound-email  — runs the two-tier classifier on the inbound
                              envelope. Heuristic first; LLM only if
                              ambiguous. Fans out either to support-email
                              -received (drafting agent) or
                              doc/process-text (existing knowledge ingest),
                              or drops as internal noise.

    support-email-received  — wraps SupportResponseAgent.run_safely() so
                              the drafting work happens asynchronously
                              after classification. Concurrency capped at
                              1 per org_id to bound the LLM token spike
                              when an inbox gets blasted.

The webhook handler in email_forward.py only does signature verification
and envelope normalization, then fires `support/classify-inbound` and
returns 200 immediately. All the slow work happens here.
"""
from __future__ import annotations

import hashlib
from typing import Any

import inngest

from app.inngest.client import get_inngest_client
from app.observability import get_logger
from app.services.agents.support_response_agent import (
    SupportResponseAgent,
    parse_from_name,
)
from app.services.integrations.email_classify import classify_inbound_email
from app.services.integrations.text_ingest import upsert_external_document

log = get_logger(__name__)

_inngest_client = get_inngest_client()

# Source tag for documents created from inbound mail (mirrors email_forward).
_KNOWLEDGE_SOURCE_TAG = "email_forward"


async def _classify_as_dict(
    *, subject: str, body: str, from_email: str,
) -> dict[str, Any]:
    """Wrapper so step.run gets a JSON-serializable result. The dataclass
    instance itself wouldn't survive the durable-execution checkpoint."""
    c = await classify_inbound_email(
        subject=subject, body=body, from_email=from_email,
    )
    return {
        "category": c.category,
        "confidence": c.confidence,
        "reason": c.reason,
        "source": c.source,
    }


@_inngest_client.create_function(
    fn_id="classify-inbound-email",
    trigger=inngest.TriggerEvent(event="support/classify-inbound"),
    retries=2,
    # Per-org cap so a 100-email burst doesn't fan out 100 parallel LLM
    # classifier calls. 5 in flight is enough for normal volume and keeps
    # any single org's burst from starving others.
    concurrency=[inngest.Concurrency(limit=5, key="event.data.org_id", scope="fn")],
)
async def classify_inbound_email_fn(ctx: inngest.Context) -> dict[str, Any]:
    """Decide whether the inbound email is support/sales (→ agent) or
    something else (→ existing knowledge-ingest path)."""
    step = ctx.step
    data = ctx.event.data

    org_id: str = data["org_id"]
    subject: str = data.get("subject") or ""
    body: str = data.get("body") or ""
    from_email: str = data.get("from_email") or ""
    from_raw: str | None = data.get("from_raw")

    classification = await step.run(
        "classify",
        lambda: _classify_as_dict(
            subject=subject, body=body, from_email=from_email,
        ),
    )

    category = classification["category"]
    log.info(
        "support_email_classified",
        org_id=org_id,
        category=category,
        confidence=classification.get("confidence"),
        source=classification.get("source"),
    )

    if category in ("support", "sales"):
        await step.send_event(
            "fanout-support-email-received",
            inngest.Event(
                name="support/email-received",
                data={
                    "org_id": org_id,
                    "from_email": from_email,
                    "from_name": data.get("from_name") or parse_from_name(from_raw),
                    "from_raw": from_raw,
                    "subject": subject,
                    "body": body,
                    "category": category,
                    "classifier_confidence": classification.get("confidence"),
                    "classifier_reason": classification.get("reason"),
                },
                # Idempotency: same inbound (org + sig) won't double-trigger.
                id=f"support-ticket-{org_id}-{_inbound_sig(subject, body)}",
            ),
        )
        return {"category": category, "routed_to": "support_agent"}

    if category == "knowledge":
        # Lazily create the document row + queue text-ingest. We only
        # create the doc once classification finishes so internal noise
        # doesn't pollute the docs list.
        doc_id = await step.run(
            "create-knowledge-doc",
            lambda: _upsert_inbound_doc(
                org_id=org_id, from_email=from_email,
                subject=subject, body=body,
            ),
        )
        content = f"Subject: {subject}\nFrom: {from_email}\n\n{body}"
        await step.send_event(
            "fanout-doc-process-text",
            inngest.Event(
                name="doc/process-text",
                data={"doc_id": doc_id, "org_id": org_id, "text": content},
                id=f"email-{doc_id}",
            ),
        )
        return {"category": "knowledge", "routed_to": "knowledge_ingest", "doc_id": doc_id}

    # internal — drop. We log so admins can see classifier behaviour in
    # the audit pages without reading a mailbox.
    log.info(
        "support_email_dropped_internal",
        org_id=org_id,
        from_email=from_email,
        reason=classification.get("reason"),
    )
    return {"category": "internal", "routed_to": "dropped"}


@_inngest_client.create_function(
    fn_id="support-email-received",
    trigger=inngest.TriggerEvent(event="support/email-received"),
    retries=2,
    concurrency=[inngest.Concurrency(limit=1, key="event.data.org_id", scope="fn")],
)
async def support_email_received_fn(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    if not data.get("org_id") or not data.get("from_email"):
        return {"status": "skipped", "reason": "missing_required_fields"}

    api_context = data.get("_api_context")
    agent = SupportResponseAgent(
        org_id=data["org_id"],
        ticket_input={
            "from_email": data["from_email"],
            "from_name": data.get("from_name") or parse_from_name(data.get("from_raw")),
            "subject": data.get("subject") or "",
            "body": data.get("body") or "",
            "category": data.get("category") or "support",
            "classifier_confidence": data.get("classifier_confidence"),
            "classifier_reason": data.get("classifier_reason"),
        },
        api_context=api_context if isinstance(api_context, dict) else None,
    )
    try:
        result = await agent.run_safely()
    except Exception as exc:
        log.warning(
            "support_agent_run_failed",
            org_id=data["org_id"],
            error=str(exc),
        )
        raise
    return {"status": "ok", "run_id": agent.run_id, **(result or {})}


async def _upsert_inbound_doc(
    *, org_id: str, from_email: str, subject: str, body: str,
) -> str:
    """Create/recover the external_document row for an inbound email
    classified as knowledge. Same dedupe sig as before email_forward.ingest
    so re-classification doesn't double-insert.
    """
    sig = hashlib.sha256(f"{subject}\n{body[:256]}".encode("utf-8")).hexdigest()[:24]
    name = f"Email: {subject[:80]}" if subject else f"Email from {from_email[:60]}"
    return await upsert_external_document(
        org_id=org_id,
        source=_KNOWLEDGE_SOURCE_TAG,
        external_id=sig,
        name=name,
        file_type="txt",
    )


def _inbound_sig(subject: str, body: str) -> str:
    """Stable idempotency key matching the dedupe sig on support_tickets."""
    return hashlib.sha256(f"{subject}\n{body[:256]}".encode("utf-8")).hexdigest()[:24]


FUNCTIONS = [classify_inbound_email_fn, support_email_received_fn]
