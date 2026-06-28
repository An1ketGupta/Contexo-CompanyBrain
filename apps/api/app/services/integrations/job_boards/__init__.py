"""Job-board adapters for the Recruiting Agent.

Job boards differ from ATSes (`integrations/ats/`) in two important ways:

  1. They are publish-only sinks — there's no concept of a "requisition" that
     lives in the provider; we hand them a job ad and they broadcast it.
  2. Their taxonomy is candidate-facing (functional area, role category,
     industry type) rather than internal (department, team, office).

The adapters expose the SAME contract as the ATS adapters so the publish
flow in `recruiting_agent.publish_requisition` can dispatch through a single
table:

    async def publish_job(*, org_id, title, content, location, department,
                          metadata?) -> {job_id, url, raw?}

`metadata` for Naukri carries the resolved taxonomy IDs (functional_area_id,
role_category_id, industry_type_id) plus experience/skills the recruiter
selected at publish time.
"""
from app.services.integrations.job_boards import naukri

__all__ = ["naukri"]
