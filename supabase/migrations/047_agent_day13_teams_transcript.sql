-- ── Agent Roadmap Day 13 (production hardening) — Teams transcript ingest
--
-- Migration 033 added `vtt` to documents.file_type but Microsoft Teams
-- transcript JSON exports never got their own value — admins had to know to
-- mark them as 'teams_transcript' via the API, which is fine for power users
-- but useless for the "drag-and-drop a Teams .json into the UI" path we
-- actually want.
--
-- This relaxes the CHECK constraint so the upload-init handler can persist
-- 'teams_transcript' as a recognised file_type. The inngest pipeline (see
-- _maybe_reclassify_transcript_json in inngest/functions.py) sniffs the
-- bytes on completion and either keeps the row as 'teams_transcript' (Teams
-- export) or fails the doc with an actionable error message for arbitrary
-- JSON. We deliberately do NOT add a generic 'json' file_type — there's no
-- generic JSON parser in the ingestion pipeline today and adding one is
-- explicitly out of scope (every JSON shape we've shipped has a dedicated
-- parser path).

ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_file_type_check;
ALTER TABLE documents ADD CONSTRAINT documents_file_type_check
  CHECK (file_type IN (
    'pdf', 'docx', 'txt', 'md',
    'xlsx', 'pptx', 'html', 'csv',
    'webpage', 'email',
    'vtt', 'teams_transcript'
  ));

COMMENT ON CONSTRAINT documents_file_type_check ON documents IS
  'Day 13 (047): adds teams_transcript so Microsoft Teams JSON exports can be ingested directly. The ingest worker sniffs the bytes and rejects non-Teams JSON at runtime.';
