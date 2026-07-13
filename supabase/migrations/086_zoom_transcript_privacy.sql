-- 086 — Zoom transcripts: private-by-default, same as Google Meet.
--
-- Migrations 084/085 built the private-by-default gate for meeting
-- transcripts but only wired `source = 'google_meet_transcript'` into the
-- BEFORE INSERT trigger. Zoom transcripts (`source = 'zoom'`, ingested by
-- services/integrations/zoom.py off the account-wide
-- recording.transcript_completed webhook) fell through to the column
-- DEFAULT 'org' — every cloud-recorded meeting in the company's Zoom
-- account became org-wide searchable the moment it was transcribed.
-- Extend the trigger so Zoom rides the exact same gate: owner-only until
-- the owner explicitly publishes.

CREATE OR REPLACE FUNCTION set_transcript_private()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.source IN ('google_meet_transcript', 'zoom') THEN
    NEW.visibility := 'private';
  END IF;
  RETURN NEW;
END;
$$;

-- Re-privatize Zoom transcripts that landed org-visible before this
-- migration. Mirrors 084's stance: owners re-publish what they meant to
-- share (explicit, rare) — the safe default for a privacy fix is private.
UPDATE documents
  SET visibility = 'private'
  WHERE source = 'zoom' AND visibility = 'org';

-- Zoom transcripts feed MeetingNotesAgent, which writes decisions / action
-- items / summary into meeting_summaries — a table migration 033 made
-- org-readable. Gate it on the source document's visibility so a private
-- transcript's extraction is as owner-only as the transcript itself.
-- (source_document_id is NOT NULL ON DELETE CASCADE, so the EXISTS always
-- has a live doc to check.) The derived summary *document* is covered
-- separately: MeetingNotesAgent now inherits the source doc's visibility
-- when it mints the derived doc.
DROP POLICY IF EXISTS meeting_summaries_select_org ON meeting_summaries;
CREATE POLICY meeting_summaries_select_org ON meeting_summaries
  FOR SELECT USING (
    org_id = auth_org_id()
    AND EXISTS (
      SELECT 1 FROM documents d
      WHERE d.id = meeting_summaries.source_document_id
        AND (d.visibility = 'org' OR d.created_by = auth.uid())
    )
  );
