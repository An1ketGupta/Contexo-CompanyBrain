"""ATS adapters for the Recruiting Agent (#20).

Three live providers — Greenhouse (Harvest API), Lever (Postings API), Ashby
(Public API). All three are org-scoped, API-key authenticated, and persist on
the unified `integrations` table introduced in migration 036:

    integrations.provider IN ('greenhouse', 'lever', 'ashby')
    integrations.access_token         — provider's API key (Greenhouse, Ashby)
                                        or personal access token (Lever)
    integrations.metadata             — provider-specific config:
                                          greenhouse:  {on_behalf_of_user_id}
                                          lever:       {posting_owner_user_id}
                                          ashby:       {}

The three modules expose a common shape:

    async def publish_job(*, org_id, title, content, location, department,
                          metadata?) -> {job_id, url}

The Recruiting service picks the right module by ats_platform. Per the
"no shared agent framework" decision we don't extract a Protocol — duck typing
keeps the call site readable and individual provider quirks (Lever's two-step
posting/publishing, Ashby's job/jobOpening split) leak appropriately to the
service module that calls them.
"""
from app.services.integrations.ats import ashby, greenhouse, lever

__all__ = ["greenhouse", "lever", "ashby"]
