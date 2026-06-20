"""V3 #91 — Query-logs retention cron.

Weekly hard-delete of `query_logs` rows older than 180 days. Runs at 04:30
UTC every Sunday — deliberately offset from the daily archive cron (03:00 /
03:15 UTC) to avoid sharing the DB write window.

180 days picked to:
  • Be useful — users can still find work from last quarter on the /history page.
  • Be bounded — at ~250 bytes/row, even a 30k-queries-per-org-per-month org
    only carries ~1.4 GB after a year. Storage is cheap; unbounded growth isn't.

The retention floor is configurable via env (`QUERY_LOG_RETENTION_DAYS`).
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import inngest

from app.inngest.client import get_inngest_client
from app.observability import get_logger

log = get_logger(__name__)

_inngest_client = get_inngest_client()


def _retention_days() -> int:
    """Read retention from env, with a safety floor of 30d.

    Lower floors are almost always a config mistake (someone typo'd 18 for 180);
    raise rather than nuke 6 months of users' query history.
    """
    raw = os.environ.get("QUERY_LOG_RETENTION_DAYS", "180")
    try:
        days = int(raw)
    except (TypeError, ValueError):
        days = 180
    return max(30, days)


@_inngest_client.create_function(
    fn_id="query-logs-retention",
    # Sundays at 04:30 UTC — quiet window, doesn't fight the daily archive crons.
    trigger=inngest.TriggerCron(cron="30 4 * * 0"),
    retries=1,
    concurrency=[inngest.Concurrency(limit=1)],
)
async def query_logs_retention(ctx: inngest.Context) -> dict[str, Any]:
    """Delete query_logs older than the configured retention window."""
    from app.services.query_logs import delete_old_query_logs

    days = _retention_days()

    async def _run() -> int:
        return await asyncio.to_thread(
            lambda: delete_old_query_logs(older_than_days=days)
        )

    deleted = await ctx.step.run("delete-old-query-logs", _run)
    log.info("query_logs.retention.ran", retention_days=days, deleted=deleted)
    return {"retention_days": days, "deleted": int(deleted or 0)}


FUNCTIONS = [query_logs_retention]
