"""Inbound email classifier pipeline (Agent Day 12).

    classify-inbound-email  — runs the two-tier classifier on the inbound
                              envelope. Heuristic first; LLM only if
                              ambiguous. Emails classified as `knowledge`
                              are ingested as a document via doc/process-text.
                              `support` fires `support/ticket-inbound`, which
                              the customer support agent picks up (see
                              customer_support_functions.py). `sales` and
                              `internal` are still dropped — logged for audit
                              but no downstream action, since there is no
                              pipeline consuming those categories.

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
    """Decide whether the inbound email should be ingested as a knowledge
    document, or dropped as noise/unsupported category."""
    step = ctx.step
    data = ctx.event.data

    org_id: str = data["org_id"]
    subject: str = data.get("subject") or ""
    body: str = data.get("body") or ""
    from_email: str = data.get("from_email") or ""
    from_raw: str = data.get("from_raw") or from_email

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

    if category == "support":
        # Hand off to the customer support agent. It decides for itself
        # whether the org has the feature enabled — this function's job
        # ends at "this is a support email", not "the org wants it acted on".
        await step.send_event(
            "fanout-support-ticket-inbound",
            inngest.Event(
                name="support/ticket-inbound",
                data={
                    "org_id": org_id,
                    "from_email": from_email,
                    "from_raw": from_raw,
                    "subject": subject,
                    "body": body,
                },
                id=f"support-ticket-{org_id}-{hashlib.sha256(f'{subject}{body[:256]}'.encode()).hexdigest()[:24]}",
            ),
        )
        return {"category": "support", "routed_to": "customer_support_agent"}

    # sales / internal — drop. We log so admins can see classifier behaviour
    # in the audit pages without reading a mailbox.
    log.info(
        "support_email_dropped",
        org_id=org_id,
        from_email=from_email,
        category=category,
        reason=classification.get("reason"),
    )
    return {"category": category, "routed_to": "dropped"}


async def _upsert_inbound_doc(
    *, org_id: str, from_email: str, subject: str, body: str,
) -> str:
    """Create/recover the external_document row for an inbound email
    classified as knowledge. Same dedupe sig as before email_forward.ingest
    so re-classification doesn't double-insert.
    """
    sig = hashlib.sha256(f"{subject}\n{body[:256]}".encode()).hexdigest()[:24]
    name = f"Email: {subject[:80]}" if subject else f"Email from {from_email[:60]}"
    return await upsert_external_document(
        org_id=org_id,
        source=_KNOWLEDGE_SOURCE_TAG,
        external_id=sig,
        name=name,
        file_type="txt",
    )


FUNCTIONS = [classify_inbound_email_fn]
