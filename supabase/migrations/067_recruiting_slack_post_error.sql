-- Recruiting: surface Slack post failures on the requisition row.
--
-- Until now, `_notify_slack` swallowed all errors with a log warning — the
-- requisition went published with no UI signal that the announcement never
-- landed. This column lets the publish flow record the Slack-side error
-- (e.g. "slack_not_in_channel") so the detail page can render an actionable
-- amber card next to the Notion tracker, symmetric with how per-platform
-- ATS failures are shown.
--
-- NULL means either Slack was skipped (no channel) or the post succeeded.
-- The detail page renders the card only when this is non-null.
ALTER TABLE job_requisitions
  ADD COLUMN IF NOT EXISTS slack_post_error TEXT;
