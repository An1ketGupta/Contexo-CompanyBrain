-- 085 — Prior-meeting context for prep briefs.
--
-- Prep briefs (calendar_meetings.prep_brief) can now ground themselves in the
-- PREVIOUS occurrence of a recurring meeting. To find that previous occurrence
-- we need Google Calendar's recurring-series key, which sync currently drops.
--
-- With singleEvents=true the Calendar API still returns `recurringEventId` on
-- every expanded instance; we capture it so all instances of one series share a
-- stable key. Non-recurring events have it NULL (the brief resolver falls back
-- to owner+title+time matching for those).

ALTER TABLE calendar_meetings ADD COLUMN IF NOT EXISTS recurring_event_id TEXT;

-- Series lookup is always scoped to one user's own meetings.
CREATE INDEX IF NOT EXISTS idx_calendar_meetings_series
  ON calendar_meetings (user_id, recurring_event_id)
  WHERE recurring_event_id IS NOT NULL;

-- Formalize documents.source. The runtime already writes sources the last
-- constraint (migration 036) never listed — 'google_meet_transcript' (Meet
-- transcript ingest) and 'zoom' (Zoom transcript ingest) among them — so the
-- prior-meeting feature depends on these rows existing. Re-add the constraint
-- with the full known set. NOT VALID so we only gate *new* inserts and never
-- fail on any legacy row that predates this list.
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_source_check;
ALTER TABLE documents
  ADD CONSTRAINT documents_source_check CHECK (
    source IN (
      'upload', 'google_drive', 'notion', 'email_forward', 'api',
      'slack', 'onedrive', 'sharepoint', 'confluence', 'github', 'dropbox',
      'google_meet_transcript', 'zoom', 'webpage', 'email'
    )
  ) NOT VALID;
