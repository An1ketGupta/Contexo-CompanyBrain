-- Recruiting: multi-ATS publish.
--
-- A requisition can now be Published to multiple ATS platforms in a single
-- click. We keep the legacy single-platform columns populated with the first
-- successful posting for back-compat (existing detail pages, audit log
-- filters, integrations dashboards) and store the full set in `ats_postings`.
--
-- Shape of each element:
--   {
--     "platform": "greenhouse" | "lever" | "ashby",
--     "job_id":   "<id from the ATS>",
--     "url":      "<canonical job URL>",
--     "error":    "<message>"  -- only when this platform failed
--   }
ALTER TABLE job_requisitions
  ADD COLUMN IF NOT EXISTS ats_postings JSONB NOT NULL DEFAULT '[]'::jsonb;
