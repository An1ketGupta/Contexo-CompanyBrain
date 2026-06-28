-- ── 053 — Agent2 Days 5 & 6: Recruiting, Sales Enablement, Executive Assistant,
--           Calendar Intelligence, Action Tracker, Quality Scoring,
--           plus the unified-integrations CHECK widening for new providers.
--
-- Adds 8 tables + a handful of columns:
--   • job_requisitions          recruiting agent JD variants + ATS publish
--   • rfp_responses             RFP intake + per-requirement match results
--   • executive_briefings       Google Doc-backed exec syntheses
--   • calendar_meetings         synced Google Calendar events + prep brief
--   • action_items              extracted meeting actions tracked externally
--   • message_quality_scores    computed per-message quality (0–10)
--   • organizations.quality_alert_threshold
--   • integrations CHECK widened to: greenhouse, lever, ashby, google_workspace,
--                                    asana, linear
--
-- Multi-tenancy: every table carries org_id with RLS via the existing
-- auth_org_id() helper. Read paths: members can see their own (or org-wide
-- where the data isn't user-private); writes are admin-only for shared tables
-- and self-only for per-user objects.

-- ── 1. Widen unified integrations CHECK ──────────────────────────────────────
-- Per CLAUDE.md, the unified table is the right home for new providers. The
-- ATS adapters (Greenhouse Harvest, Lever Postings, Ashby) and the workplace
-- (Google Workspace per-user OAuth bundling calendar + docs + drive.file) all
-- land here. Asana/Linear too, as per-user OAuth.
ALTER TABLE integrations DROP CONSTRAINT IF EXISTS integrations_provider_check;
ALTER TABLE integrations
  ADD CONSTRAINT integrations_provider_check CHECK (
    provider IN (
      'onedrive', 'confluence', 'github', 'dropbox',
      'greenhouse', 'lever', 'ashby',
      'google_workspace',
      'asana', 'linear'
    )
  );


-- ── 2. Recruiting Agent (#20) ────────────────────────────────────────────────
-- One row per "we need to hire X" request. The 5 generated JD variants land
-- in jd_variants (JSONB array); the selected variant index plus ATS publish
-- result columns capture the post-publish state.
CREATE TABLE IF NOT EXISTS job_requisitions (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                 UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  created_by             UUID NOT NULL REFERENCES users(id),
  role_request           TEXT NOT NULL,
  -- [{tone, text}, …] — Pydantic JdVariant on the backend.
  jd_variants            JSONB NOT NULL DEFAULT '[]',
  selected_variant_index INTEGER,
  ats_platform           TEXT CHECK (ats_platform IN ('greenhouse', 'lever', 'ashby')),
  ats_job_id             TEXT,
  ats_url                TEXT,
  notion_tracker_url     TEXT,
  sourcing_templates     JSONB NOT NULL DEFAULT '[]',
  linkedin_search_urls   JSONB NOT NULL DEFAULT '[]',
  hiring_manager_email   TEXT,
  slack_channel          TEXT,
  status                 TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'Published', 'failed')),
  error_message          TEXT,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  Published_at           TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_job_requisitions_org_created
  ON job_requisitions(org_id, created_at DESC);

ALTER TABLE job_requisitions ENABLE ROW LEVEL SECURITY;

-- Members read all requisitions in their org (recruiting is org-shared work).
-- Writes: creator may edit their own; admins may edit any.
CREATE POLICY "job_requisitions_select" ON job_requisitions
  FOR SELECT USING (org_id = auth_org_id());

CREATE POLICY "job_requisitions_insert" ON job_requisitions
  FOR INSERT WITH CHECK (org_id = auth_org_id() AND created_by = auth.uid());

CREATE POLICY "job_requisitions_update" ON job_requisitions
  FOR UPDATE USING (
    org_id = auth_org_id()
    AND (created_by = auth.uid() OR EXISTS (
      SELECT 1 FROM users WHERE id = auth.uid() AND role = 'admin'
    ))
  );

CREATE POLICY "job_requisitions_delete" ON job_requisitions
  FOR DELETE USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE id = auth.uid() AND role = 'admin')
  );


-- ── 3. Sales Enablement RFP (#22) ────────────────────────────────────────────
-- The RFP intake stores the parsed requirements list + the per-requirement
-- match result, so the rep can review/edit BEFORE the .docx is generated.
CREATE TABLE IF NOT EXISTS rfp_responses (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  created_by         UUID NOT NULL REFERENCES users(id),
  source_filename    TEXT NOT NULL,
  source_file_path   TEXT,
  source_text        TEXT,                 -- extracted full text (debug/audit)
  -- [{id, requirement_text, category}, …]
  requirements       JSONB NOT NULL DEFAULT '[]',
  -- [{requirement_id, status: matched|gap, source_doc?, response_text?, flag_message?}, …]
  matches            JSONB NOT NULL DEFAULT '[]',
  -- One of: extracted, reviewed, generating, ready, failed
  status             TEXT NOT NULL DEFAULT 'extracted'
    CHECK (status IN ('extracted', 'reviewed', 'generating', 'ready', 'failed')),
  output_file_path   TEXT,                 -- generated .docx path
  gap_count          INTEGER NOT NULL DEFAULT 0,
  error_message      TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  generated_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_rfp_responses_org_created
  ON rfp_responses(org_id, created_at DESC);

ALTER TABLE rfp_responses ENABLE ROW LEVEL SECURITY;

CREATE POLICY "rfp_responses_select" ON rfp_responses
  FOR SELECT USING (org_id = auth_org_id());

CREATE POLICY "rfp_responses_insert" ON rfp_responses
  FOR INSERT WITH CHECK (org_id = auth_org_id() AND created_by = auth.uid());

CREATE POLICY "rfp_responses_update" ON rfp_responses
  FOR UPDATE USING (
    org_id = auth_org_id()
    AND (created_by = auth.uid() OR EXISTS (
      SELECT 1 FROM users WHERE id = auth.uid() AND role = 'admin'
    ))
  );

CREATE POLICY "rfp_responses_delete" ON rfp_responses
  FOR DELETE USING (org_id = auth_org_id() AND created_by = auth.uid());


-- ── 4. Executive Assistant briefings (#25) ──────────────────────────────────
-- Saved Google-Doc-backed exec briefings. Recipients tracked so we can list
-- "briefings shared with you" later.
CREATE TABLE IF NOT EXISTS executive_briefings (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  created_by      UUID NOT NULL REFERENCES users(id),
  request_text    TEXT NOT NULL,
  recipients      JSONB NOT NULL DEFAULT '[]',  -- ["alice@acme.com", …]
  -- Structured sections {executive_summary, market_context, competitive_advantages, risks, recommendations}
  sections        JSONB NOT NULL DEFAULT '{}',
  google_doc_id   TEXT,
  google_doc_url  TEXT,
  followup_event_id   TEXT,                 -- Google Calendar event id, if scheduled
  followup_event_url  TEXT,
  status          TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'doc_created', 'sent', 'failed')),
  error_message   TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  sent_at         TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_executive_briefings_org_created
  ON executive_briefings(org_id, created_at DESC);

ALTER TABLE executive_briefings ENABLE ROW LEVEL SECURITY;

CREATE POLICY "executive_briefings_select" ON executive_briefings
  FOR SELECT USING (org_id = auth_org_id() AND (
    created_by = auth.uid() OR EXISTS (
      SELECT 1 FROM users WHERE id = auth.uid() AND role = 'admin'
    )
  ));

CREATE POLICY "executive_briefings_all_self" ON executive_briefings
  FOR ALL USING (org_id = auth_org_id() AND created_by = auth.uid());


-- ── 5. Calendar Intelligence — synced meetings + prep brief (#51) ────────────
-- One row per Google Calendar event we've pulled. Idempotent on
-- (org_id, user_id, external_event_id) so a re-sync overwrites cleanly.
CREATE TABLE IF NOT EXISTS calendar_meetings (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  external_event_id   TEXT NOT NULL,           -- Google Calendar event.id
  calendar_id         TEXT NOT NULL DEFAULT 'primary',
  title               TEXT,
  description         TEXT,
  location            TEXT,
  attendee_emails     JSONB NOT NULL DEFAULT '[]',  -- ["foo@x.com", …]
  start_time          TIMESTAMPTZ NOT NULL,
  end_time            TIMESTAMPTZ NOT NULL,
  meeting_url         TEXT,
  -- {available_at, executive_summary, attendee_context, topic_research, suggested_questions, source_documents}
  prep_brief          JSONB,
  prep_brief_available_at TIMESTAMPTZ,
  prep_brief_status   TEXT NOT NULL DEFAULT 'pending'
    CHECK (prep_brief_status IN ('pending', 'generating', 'ready', 'failed', 'skipped')),
  prep_brief_error    TEXT,
  last_synced_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_calendar_meetings_user_event
  ON calendar_meetings(user_id, external_event_id);

CREATE INDEX IF NOT EXISTS idx_calendar_meetings_user_start
  ON calendar_meetings(user_id, start_time);

CREATE INDEX IF NOT EXISTS idx_calendar_meetings_prep_pending
  ON calendar_meetings(prep_brief_status, start_time)
  WHERE prep_brief_status = 'pending';

ALTER TABLE calendar_meetings ENABLE ROW LEVEL SECURITY;

CREATE POLICY "calendar_meetings_select_own" ON calendar_meetings
  FOR SELECT USING (org_id = auth_org_id() AND user_id = auth.uid());

CREATE POLICY "calendar_meetings_write_own" ON calendar_meetings
  FOR ALL USING (org_id = auth_org_id() AND user_id = auth.uid());


-- ── 6. Action Item Tracker (#44) ─────────────────────────────────────────────
-- Per-action row. external_provider/external_id captures the Notion page id,
-- Asana task gid, or Linear issue id once we've created it. status mirrors
-- the local view; the reminder cron polls the external system for completion.
CREATE TABLE IF NOT EXISTS action_items (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  created_by         UUID NOT NULL REFERENCES users(id),
  source_meeting_id  UUID REFERENCES calendar_meetings(id) ON DELETE SET NULL,
  source_notes       TEXT,
  action_text        TEXT NOT NULL,
  owner_user_id      UUID REFERENCES users(id) ON DELETE SET NULL,
  owner_name_raw     TEXT,                     -- raw extracted string before fuzzy match
  due_date           DATE,
  external_provider  TEXT CHECK (external_provider IN ('notion', 'asana', 'linear')),
  external_id        TEXT,                     -- e.g. Notion page id, Asana gid, Linear issue id
  external_url       TEXT,
  status             TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'tracked', 'completed', 'overdue', 'cancelled')),
  last_reminded_at   TIMESTAMPTZ,
  reminder_count     INTEGER NOT NULL DEFAULT 0,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_action_items_org_status_due
  ON action_items(org_id, status, due_date);

CREATE INDEX IF NOT EXISTS idx_action_items_owner_status
  ON action_items(owner_user_id, status)
  WHERE status IN ('pending', 'tracked', 'overdue');

ALTER TABLE action_items ENABLE ROW LEVEL SECURITY;

CREATE POLICY "action_items_select_member" ON action_items
  FOR SELECT USING (org_id = auth_org_id());

CREATE POLICY "action_items_write_self_or_admin" ON action_items
  FOR ALL USING (
    org_id = auth_org_id()
    AND (
      created_by = auth.uid()
      OR owner_user_id = auth.uid()
      OR EXISTS (SELECT 1 FROM users WHERE id = auth.uid() AND role = 'admin')
    )
  );


-- ── 7. Output Quality Scoring (#52) ──────────────────────────────────────────
-- One row per scored assistant message. We store the input components so the
-- admin UI can show "why this score" without re-deriving from raw signals.
ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS quality_alert_threshold REAL;

CREATE TABLE IF NOT EXISTS message_quality_scores (
  message_id        UUID PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
  org_id            UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  score             REAL NOT NULL,            -- 0..10
  copy_count        INTEGER NOT NULL DEFAULT 0,
  feedback          TEXT,                     -- 'positive' | 'negative' | NULL
  confidence_score  REAL,                     -- from messages.metadata.confidence.score (0..10)
  sources_count     INTEGER NOT NULL DEFAULT 0,
  -- bucket: query_category if available, NULL otherwise. Surfaces in trend
  -- breakdowns ("policy questions" vs "creative drafts").
  category          TEXT,
  computed_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_quality_scores_org_computed
  ON message_quality_scores(org_id, computed_at DESC);

CREATE INDEX IF NOT EXISTS idx_quality_scores_org_category_computed
  ON message_quality_scores(org_id, category, computed_at DESC)
  WHERE category IS NOT NULL;

ALTER TABLE message_quality_scores ENABLE ROW LEVEL SECURITY;

-- Admins only — quality scoring is a manager-level surface.
CREATE POLICY "quality_scores_select_admin" ON message_quality_scores
  FOR SELECT USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE id = auth.uid() AND role = 'admin')
  );


-- ── 8. Trend aggregation RPC ────────────────────────────────────────────────
-- Pushing the per-week AVG/COUNT GROUP BY down to SQL keeps the admin
-- /admin/quality endpoint to one round-trip even when scanning 8+ weeks.
CREATE OR REPLACE FUNCTION get_org_quality_trend(
  p_org_id UUID,
  p_weeks  INTEGER DEFAULT 8
)
RETURNS TABLE (
  week_start  TIMESTAMPTZ,
  avg_score   REAL,
  message_count INTEGER
) AS $$
  SELECT
    date_trunc('week', computed_at) AS week_start,
    AVG(score)::REAL                AS avg_score,
    COUNT(*)::INTEGER               AS message_count
  FROM message_quality_scores
  WHERE org_id = p_org_id
    AND computed_at >= now() - (p_weeks || ' weeks')::INTERVAL
  GROUP BY 1
  ORDER BY 1;
$$ LANGUAGE sql STABLE SECURITY DEFINER;

-- Category breakdown (this-week-vs-last-week).
CREATE OR REPLACE FUNCTION get_org_quality_by_category(
  p_org_id UUID,
  p_weeks  INTEGER DEFAULT 4
)
RETURNS TABLE (
  category    TEXT,
  avg_score   REAL,
  message_count INTEGER
) AS $$
  SELECT
    COALESCE(category, 'uncategorized') AS category,
    AVG(score)::REAL                    AS avg_score,
    COUNT(*)::INTEGER                   AS message_count
  FROM message_quality_scores
  WHERE org_id = p_org_id
    AND computed_at >= now() - (p_weeks || ' weeks')::INTERVAL
  GROUP BY 1
  ORDER BY 2 DESC;
$$ LANGUAGE sql STABLE SECURITY DEFINER;


-- ── 9. updated_at touchers (consistency with existing tables) ───────────────
CREATE OR REPLACE FUNCTION touch_day56_updated_at()
  RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.last_synced_at = now();
  RETURN NEW;
END;
$$;
-- (intentionally minimal — most tables don't have an updated_at; calendar_meetings
--  uses last_synced_at which we update at sync time explicitly.)
