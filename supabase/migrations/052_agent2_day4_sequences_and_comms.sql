-- ── Agent Roadmap 2 — Day 4: Sequences (#8) + Internal Communications (#23)
--
-- Two adjacent features that both ride on multi-step Inngest scheduling
-- and per-step external sends:
--
-- 1. Sales follow-up sequences (#8)
--    A sequence targets ONE prospect (per the Day-4 sign-off decision —
--    multi-prospect "enrollments" are a P2 follow-up). Each sequence has
--    3 timed steps (default Day 0/3/7) sent from the creator's Gmail.
--    Subsequent steps reply on the first step's Gmail thread so the
--    prospect sees a coherent thread, not three disconnected messages.
--
-- 2. Internal multi-channel announcements (#23)
--    One LLM call produces three channel-appropriate versions of the
--    same message (email recap, Slack post, Notion page). Single
--    "Approve & Schedule" action fires all three concurrently at the
--    scheduled time. Status flow lives on the announcement row — no
--    second-party approval table involvement (per the Day-4 sign-off).
--
-- Both tables use RLS for org isolation and read-only SELECT for the
-- relevant role. Writes go through service-role from the worker; the
-- routers enforce in-app authorisation before issuing writes.

-- ── 1. sequences (#8) ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sequences (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  created_by        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

  -- Admin-facing label, e.g. "Acme Corp Q1 outreach".
  name              TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),

  -- The single prospect this sequence targets. Validated as email-shaped at
  -- the application layer; the DB cap is defence-in-depth.
  prospect_email    TEXT NOT NULL CHECK (char_length(prospect_email) <= 320),
  prospect_name     TEXT CHECK (prospect_name IS NULL OR char_length(prospect_name) <= 200),

  -- Free-form context the creator gives the LLM ("enterprise prospect,
  -- mentioned SOC2 concerns on the last call").
  prospect_context  TEXT NOT NULL CHECK (char_length(prospect_context) BETWEEN 1 AND 4000),

  -- 'draft'      — generated, awaiting review
  -- 'scheduled'  — admin scheduled; step rows have scheduled_for set
  -- 'active'     — first step sent, later steps still pending
  -- 'completed'  — all steps either sent or failed
  -- 'cancelled'  — admin cancelled mid-flight
  status            TEXT NOT NULL DEFAULT 'draft'
                      CHECK (status IN ('draft', 'scheduled', 'active', 'completed', 'cancelled')),

  -- Snapshot of the user's Gmail address at schedule time, so a later
  -- disconnect of Gmail doesn't orphan the sent-from attribution.
  sender_email      TEXT,

  -- Used as the IANA timezone for any UI display of scheduled times.
  -- Sends themselves use absolute UTC timestamps on sequence_steps.
  -- IANA TZ string ("America/New_York", "Europe/London"); validated app-side.
  display_timezone  TEXT NOT NULL DEFAULT 'UTC',

  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sequences_org_status
  ON sequences(org_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_sequences_creator_created
  ON sequences(created_by, created_at DESC);


ALTER TABLE sequences ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS sequences_select_own ON sequences;
CREATE POLICY sequences_select_own ON sequences
  FOR SELECT USING (
    org_id = auth_org_id()
    -- Creators see their own sequences; admins see all.
    AND (created_by = auth.uid()
         OR EXISTS (
           SELECT 1 FROM users
            WHERE users.id = auth.uid()
              AND users.role = 'admin'
              AND users.org_id = sequences.org_id
         ))
  );


-- ── 2. sequence_steps (#8) ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sequence_steps (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  sequence_id         UUID NOT NULL REFERENCES sequences(id) ON DELETE CASCADE,
  -- Denormalised for RLS — every read goes through org_id.
  org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

  -- 0, 1, 2 for a 3-step sequence. Unique per sequence.
  step_order          INT NOT NULL CHECK (step_order >= 0 AND step_order <= 20),

  -- Days from the schedule_sequence() call when this step should fire.
  -- 0 = immediate, 3 = +3 days, etc.
  send_offset_days    INT NOT NULL DEFAULT 0
                        CHECK (send_offset_days >= 0 AND send_offset_days <= 365),

  subject             TEXT NOT NULL CHECK (char_length(subject) BETWEEN 1 AND 998),
  body                TEXT NOT NULL CHECK (char_length(body) BETWEEN 1 AND 50000),

  -- Per-step lifecycle, independent of the sequence's overall status.
  status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'scheduled', 'sent', 'failed', 'cancelled')),

  scheduled_for       TIMESTAMPTZ,
  sent_at             TIMESTAMPTZ,

  -- Gmail message id of the send (returned by users.messages.send).
  gmail_message_id    TEXT,

  -- Thread id from the first step; later steps reply to this thread so
  -- the prospect sees a coherent conversation rather than 3 fresh emails.
  gmail_thread_id     TEXT,

  error_message       TEXT,

  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_sequence_steps_sequence_order
  ON sequence_steps(sequence_id, step_order);

CREATE INDEX IF NOT EXISTS idx_sequence_steps_status_scheduled
  ON sequence_steps(status, scheduled_for)
  WHERE status = 'scheduled';

CREATE INDEX IF NOT EXISTS idx_sequence_steps_sequence
  ON sequence_steps(sequence_id, step_order);


ALTER TABLE sequence_steps ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS sequence_steps_select ON sequence_steps;
CREATE POLICY sequence_steps_select ON sequence_steps
  FOR SELECT USING (
    org_id = auth_org_id()
    AND EXISTS (
      SELECT 1 FROM sequences s
       WHERE s.id = sequence_steps.sequence_id
         AND (s.created_by = auth.uid()
              OR EXISTS (
                SELECT 1 FROM users u
                 WHERE u.id = auth.uid()
                   AND u.role = 'admin'
                   AND u.org_id = sequence_steps.org_id
              ))
    )
  );


-- ── 3. internal_announcements (#23) ───────────────────────────────────────

CREATE TABLE IF NOT EXISTS internal_announcements (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id               UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  created_by           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

  -- Admin's prompt to the LLM. e.g. "Send an all-hands recap of Q3 planning".
  request_text         TEXT NOT NULL CHECK (char_length(request_text) BETWEEN 1 AND 4000),

  -- Three channel-specific drafts. NULL when the channel is disabled for
  -- this announcement. Admins toggle channels at schedule time.
  email_subject        TEXT,
  email_body           TEXT,
  slack_body           TEXT,
  notion_title         TEXT,
  notion_body          TEXT,

  -- Channel destinations the admin picked at schedule time.
  -- recipients: JSONB array of email strings, e.g. ["alice@acme.com", ...]
  -- slack_channel_id: a workspace channel id (e.g. C0123ABCD)
  -- notion_parent_page_id: the page where the Notion page is created
  recipients           JSONB NOT NULL DEFAULT '[]'::jsonb
                          CHECK (jsonb_typeof(recipients) = 'array'),
  slack_channel_id     TEXT,
  notion_parent_page_id TEXT,

  -- 'draft'     — generated, awaiting review
  -- 'scheduled' — approved + scheduled_for set; Inngest job queued
  -- 'sending'   — dispatcher fired, mid-flight
  -- 'sent'      — at least one channel sent OK
  -- 'failed'    — every enabled channel failed
  -- 'cancelled' — admin cancelled before dispatch
  status               TEXT NOT NULL DEFAULT 'draft'
                          CHECK (status IN ('draft', 'scheduled', 'sending', 'sent', 'failed', 'cancelled')),

  scheduled_for        TIMESTAMPTZ,
  sent_at              TIMESTAMPTZ,

  -- Send results recorded on dispatch for the dashboard.
  email_sent_count     INT NOT NULL DEFAULT 0,
  slack_ts             TEXT,
  notion_page_url      TEXT,

  error_message        TEXT,

  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE INDEX IF NOT EXISTS idx_announcements_org_status
  ON internal_announcements(org_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_announcements_scheduled
  ON internal_announcements(status, scheduled_for)
  WHERE status = 'scheduled';


ALTER TABLE internal_announcements ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS internal_announcements_select ON internal_announcements;
CREATE POLICY internal_announcements_select ON internal_announcements
  FOR SELECT USING (
    org_id = auth_org_id()
    -- Admins see all announcements; the creator sees their own drafts.
    AND (created_by = auth.uid()
         OR EXISTS (
           SELECT 1 FROM users
            WHERE users.id = auth.uid()
              AND users.role = 'admin'
              AND users.org_id = internal_announcements.org_id
         ))
  );


-- Touch updated_at on row changes for both parent tables. Existing migrations
-- use triggers like this on `documents`, `conversations`, etc. — match the
-- existing convention rather than relying on app-layer writes.

CREATE OR REPLACE FUNCTION _touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sequences_updated_at ON sequences;
CREATE TRIGGER trg_sequences_updated_at
  BEFORE UPDATE ON sequences
  FOR EACH ROW EXECUTE FUNCTION _touch_updated_at();

DROP TRIGGER IF EXISTS trg_announcements_updated_at ON internal_announcements;
CREATE TRIGGER trg_announcements_updated_at
  BEFORE UPDATE ON internal_announcements
  FOR EACH ROW EXECUTE FUNCTION _touch_updated_at();


COMMENT ON TABLE sequences IS
  'Agent2 Day 4 #8: per-prospect sales follow-up sequence (1 prospect, N timed steps via Gmail).';

COMMENT ON TABLE sequence_steps IS
  'Agent2 Day 4 #8: individual emails in a sequence. status=scheduled rows wait on Inngest step.sleep_until.';

COMMENT ON TABLE internal_announcements IS
  'Agent2 Day 4 #23: multichannel internal announcement (one source, three channel renderings, fire concurrently).';
