"""Generic version-diff agent pipeline (Agent Roadmap Day 15).

Wraps VersionDiffAgent.run_safely() in an Inngest function. Fired by the
document-processing pipeline AFTER auto-tag-document (so the policy gate
can read the final tag set).

Concurrency cap: 1 per (document_id) so a rapid re-upload doesn't double-
fire the diff.
"""
from __future__ import annotations

from typing import Any

import inngest

from app.inngest.client import get_inngest_client
from app.observability import get_logger
from app.services.agents.version_diff_agent import VersionDiffAgent

log = get_logger(__name__)

_inngest_client = get_inngest_client()


@_inngest_client.create_function(
    fn_id="version-diff-agent",
    trigger=inngest.TriggerEvent(event="doc/version-diff"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.document_id", scope="fn"),
    ],
)
async def run_version_diff(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    document_id = data.get("document_id")
    org_id = data.get("org_id")
    version_id = data.get("version_id")

    if not document_id or not org_id:
        return {"status": "skipped", "reason": "missing_required_fields"}

    agent = VersionDiffAgent(
        org_id=org_id,
        document_id=document_id,
        version_id=version_id,
    )
    try:
        result = await agent.run_safely()
    except Exception as exc:
        log.warning(
            "version_diff_failed doc=%s err=%s", document_id, exc,
        )
        raise
    return {"status": "ok", "run_id": agent.run_id, **(result or {})}


FUNCTIONS = [run_version_diff]
