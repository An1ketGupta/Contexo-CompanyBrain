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

-- ── Enforce private-by-default transcripts at the DB level ───────────────────
-- Migration 084 added the visibility gate and retroactively privatized existing
-- google_meet_transcript rows, but nothing sets visibility on *new* inserts:
-- upsert_external_document() omits the column, so the DEFAULT 'org' applies and
-- every transcript ingested since 084 has been landing org-wide-searchable —
-- exactly the leak 084 set out to close. Fix it where it can't be bypassed: a
-- BEFORE INSERT trigger, so every ingest path (Doc export, binary download, and
-- any future one) is covered regardless of what the app layer forgets to pass.
--
-- INSERT-only: publishing a transcript to the org (documents router) is an
-- UPDATE, and re-sync via upsert is also an UPDATE, so neither is clobbered.
CREATE OR REPLACE FUNCTION set_transcript_private()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.source = 'google_meet_transcript' THEN
    NEW.visibility := 'private';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_transcript_private ON documents;
CREATE TRIGGER trg_transcript_private
  BEFORE INSERT ON documents
  FOR EACH ROW
  EXECUTE FUNCTION set_transcript_private();

-- Re-privatize any transcripts that leaked to 'org' during the gap between 084
-- and this migration. Mirrors 084's stance: owners re-publish what they meant
-- to share (explicit, rare) — the safe default for a privacy fix is private.
UPDATE documents
  SET visibility = 'private'
  WHERE source = 'google_meet_transcript' AND visibility = 'org';
