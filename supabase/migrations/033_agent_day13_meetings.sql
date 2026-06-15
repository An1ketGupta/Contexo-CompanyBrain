-- ── Agent Roadmap Day 13 — Meeting Notes Ingestion
--
-- Uploaded transcripts (.vtt from Zoom, .json from Teams) get routed to the
-- MeetingNotesAgent which extracts structured info and saves it here. The
-- raw transcript stays in `documents` (so it's still searchable as a doc);
-- this table holds the agent's interpretation:
--
--   * attendees       — JSONB array of {name, raw_label, utterance_count}
--   * decisions       — JSONB array of {text} extracted by LLM
--   * action_items    — JSONB array of {owner, task, due_date_text, raw}
--   * summary         — 3-sentence prose summary (also chunked + embedded
--                       into the derived document)
--   * derived_document_id — pointer to the auto-created "Meeting summary: X"
--                       document so the summary is itself queryable.
--
-- The derived document is what surfaces in the docs list; the source
-- transcript stays present but is auto-tagged 'meeting-notes' + 'transcript'
-- so users can filter to either layer.
--
-- One row per source_document_id (unique constraint) — if the same
-- transcript is reprocessed (e.g. user re-uploads), we update in place.

CREATE TABLE IF NOT EXISTS meeting_summaries (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                   UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

  source_document_id       UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  derived_document_id      UUID REFERENCES documents(id) ON DELETE SET NULL,
  agent_run_id             UUID REFERENCES agent_runs(id) ON DELETE SET NULL,

  -- 'zoom_vtt' | 'teams_json' — informational, used by the UI to show a
  -- "Source: Zoom transcript" badge on the derived doc.
  source_format            TEXT NOT NULL DEFAULT 'unknown',

  -- Meeting timestamp guesses (parsed out of the VTT cues or Teams metadata).
  -- Both nullable — many transcripts don't include a calendar timestamp.
  meeting_started_at       TIMESTAMPTZ,
  meeting_duration_seconds INT,

  attendees                JSONB NOT NULL DEFAULT '[]'::jsonb,
  decisions                JSONB NOT NULL DEFAULT '[]'::jsonb,
  action_items             JSONB NOT NULL DEFAULT '[]'::jsonb,
  summary                  TEXT,

  -- Side-effect outcomes (so we can tell whether Slack was posted to).
  slack_channel_id         TEXT,
  slack_message_ts         TEXT,
  slack_posted_at          TIMESTAMPTZ,

  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_meeting_summaries_source
  ON meeting_summaries(source_document_id);

CREATE INDEX IF NOT EXISTS idx_meeting_summaries_org_created
  ON meeting_summaries(org_id, created_at DESC);

CREATE OR REPLACE FUNCTION touch_meeting_summaries_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_meeting_summaries_updated_at ON meeting_summaries;
CREATE TRIGGER trg_meeting_summaries_updated_at
  BEFORE UPDATE ON meeting_summaries
  FOR EACH ROW
  EXECUTE FUNCTION touch_meeting_summaries_updated_at();

ALTER TABLE meeting_summaries ENABLE ROW LEVEL SECURITY;

-- All org members can read meeting summaries (same as documents).
DROP POLICY IF EXISTS meeting_summaries_select_org ON meeting_summaries;
CREATE POLICY meeting_summaries_select_org ON meeting_summaries
  FOR SELECT USING (org_id = auth_org_id());

-- Writes via service-role only (agent runs inside Inngest worker).

COMMENT ON TABLE meeting_summaries IS
  'Day 13: structured extraction (attendees/decisions/actions/summary) for uploaded meeting transcripts.';


-- ── Allow `vtt` as a documents.file_type ────────────────────────────────────
-- The constraint was relaxed in 023 to include `webpage`/`email`; we extend it
-- once more so transcript uploads pass the same CHECK. Listed in the same
-- migration as the meeting_summaries table because they're conceptually
-- coupled — adding the table without the constraint relaxation would let an
-- admin upload a .vtt and immediately hit a DB error.

ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_file_type_check;
ALTER TABLE documents ADD CONSTRAINT documents_file_type_check
  CHECK (file_type IN (
    'pdf', 'docx', 'txt', 'md',
    'xlsx', 'pptx', 'html', 'csv',
    'webpage', 'email',
    'vtt'
  ));
