-- Recruiting: default Notion parent page for hiring trackers.
--
-- Stored on notion_integrations because (a) it's a Notion concept and (b) it
-- naturally cascades when the user disconnects Notion. Each org picks one
-- parent page once during recruiting onboarding; every requisition publish
-- creates a child page under it unless the publish explicitly overrides.
--
-- We persist the page title alongside the ID so the UI can show a friendly
-- chip without re-fetching from Notion on every render. Title may go stale
-- if the user renames the page in Notion — that's acceptable; we refresh
-- whenever the recruiter re-opens the picker.
ALTER TABLE notion_integrations
  ADD COLUMN IF NOT EXISTS default_recruiting_tracker_parent_id TEXT,
  ADD COLUMN IF NOT EXISTS default_recruiting_tracker_parent_title TEXT;
