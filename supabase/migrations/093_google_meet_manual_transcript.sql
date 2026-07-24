-- ── Manual Google Meet transcript upload ──────────────────────────────────
--
-- Google Meet transcripts previously only arrived via the per-user Drive-
-- folder OAuth sync (google_meet_transcripts.py), which ingests them as a
-- plain searchable document with no MeetingNotesAgent structured extraction
-- (attendees/decisions/action items) — unlike Zoom .vtt and Teams .json.
--
-- This adds a manual-upload path: a plain-text transcript (Google Meet's
-- own "Download as .txt" export, or any other tool using the
-- "[00:00:24]\nSpeaker: text" cue format the parser already supports as a
-- fallback) can be uploaded via the dedicated meeting-transcript button and
-- tagged 'transcript', routing it into MeetingNotesAgent the same way
-- vtt/teams_transcript already do.

ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_file_type_check;
ALTER TABLE documents ADD CONSTRAINT documents_file_type_check
  CHECK (file_type IN (
    'pdf', 'docx', 'txt', 'md',
    'xlsx', 'pptx', 'html', 'csv',
    'webpage', 'email',
    'vtt', 'teams_transcript', 'transcript'
  ));

COMMENT ON CONSTRAINT documents_file_type_check ON documents IS
  'Migration 093: adds transcript for manually uploaded plain-text meeting transcripts (Google Meet .txt exports and other bracketed-timestamp transcripts), routed into MeetingNotesAgent like vtt/teams_transcript.';
