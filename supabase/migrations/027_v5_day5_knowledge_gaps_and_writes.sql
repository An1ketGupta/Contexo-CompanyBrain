-- ── V5 Day 5 / Agent Roadmap Day 4-5
--
-- Three things in one migration because they ship as one feature surface:
--   1. drive_integrations.scopes    — Day 4 Docs write scope persistence
--   2. knowledge_gaps               — Day 5 zero-result query signal
--   3. document_drafts              — Day 5 AI-stub-doc review queue
--
-- All three are additive (no existing data invalidated).

-- ── 0. documents.source check — allow knowledge_gap_stub ────────────────────
-- The approval flow inserts approved drafts as regular documents with this
-- provenance tag so admins can filter / report on them in the docs table.
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_source_check;
ALTER TABLE documents ADD CONSTRAINT documents_source_check
  CHECK (source IN (
    'upload',
    'google_drive',
    'notion',
    'email_forward',
    'api',
    'knowledge_gap_stub'
  ));


-- ── 1. drive_integrations.scopes ────────────────────────────────────────────
-- We added a Docs write scope to the OAuth request. Persist the granted scope
-- list so the UI can render "Reconnect to enable Docs export" for existing
-- installs that only have drive.readonly. Default = ['drive.readonly'] so
-- existing rows are interpreted correctly (they were granted exactly that).
ALTER TABLE drive_integrations
  ADD COLUMN IF NOT EXISTS scopes TEXT[] NOT NULL DEFAULT ARRAY['https://www.googleapis.com/auth/drive.readonly']::TEXT[];

COMMENT ON COLUMN drive_integrations.scopes IS
  'Scope strings granted by Google. Used by the UI to decide whether the Docs export action is available.';


-- ── 2. knowledge_gaps ───────────────────────────────────────────────────────
-- One row per LLM-issued search that returned zero hits. We persist at search
-- granularity (not user-message granularity) because the LLM issues 2-4
-- searches per turn and each one is a separately-actionable signal: "policy
-- on returns" and "refund timeline" can hit the same chat turn but represent
-- two different missing documents.
CREATE TABLE IF NOT EXISTS knowledge_gaps (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  -- The actual search query the LLM issued. We treat this as the "topic"
  -- — no NLP normalization. The LLM already normalizes via its tool-call
  -- argument (e.g. "what is our refund policy" → "refund policy"), and
  -- doing it again here would just introduce drift.
  topic            TEXT NOT NULL,
  -- The user's original message that produced this search. Useful context
  -- for the admin reviewing the gap — they can see what the user actually
  -- asked, not just the LLM's distilled search term.
  query            TEXT NOT NULL,
  user_id          UUID REFERENCES users(id) ON DELETE SET NULL,
  conversation_id  UUID REFERENCES conversations(id) ON DELETE SET NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Hot reads (admin panel, threshold counts): org + topic + recency.
CREATE INDEX IF NOT EXISTS idx_knowledge_gaps_org_topic_created
  ON knowledge_gaps(org_id, topic, created_at DESC);

-- The admin panel sorts by created_at for the recent-activity feed.
CREATE INDEX IF NOT EXISTS idx_knowledge_gaps_org_created
  ON knowledge_gaps(org_id, created_at DESC);

ALTER TABLE knowledge_gaps ENABLE ROW LEVEL SECURITY;

-- Admins-only read. Gaps include user queries which can be sensitive
-- (e.g. someone asking about "termination policy"); we don't want them
-- visible to the rest of the workspace.
DROP POLICY IF EXISTS knowledge_gaps_select_admin ON knowledge_gaps;
CREATE POLICY knowledge_gaps_select_admin ON knowledge_gaps
  FOR SELECT USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin')
  );


-- ── 3. document_drafts ──────────────────────────────────────────────────────
-- AI-generated stub documents waiting for admin approval before becoming
-- real `documents` rows. We DON'T re-use the documents table with a status
-- column because drafts have fundamentally different lifecycle semantics
-- (they may never become docs at all), and we want the search path to
-- never accidentally surface unapproved content.
CREATE TABLE IF NOT EXISTS document_drafts (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                 UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  title                  TEXT NOT NULL,
  content                TEXT NOT NULL,
  -- 'knowledge_gap_autoflow' for Day 5, 'admin_manual' for hand-created
  -- stubs in a follow-up day, 'version_diff' reserved for Day 15.
  source                 TEXT NOT NULL,
  gap_topic              TEXT,
  gap_count              INT,
  -- pending_review | approved | rejected
  status                 TEXT NOT NULL DEFAULT 'pending_review',
  reviewed_by            UUID REFERENCES users(id) ON DELETE SET NULL,
  reviewed_at            TIMESTAMPTZ,
  -- When approved → ingested as a real document, link the resulting row so
  -- the UI can jump straight to the live doc. NULL until approval.
  ingested_document_id   UUID REFERENCES documents(id) ON DELETE SET NULL,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT document_drafts_status_check
    CHECK (status IN ('pending_review', 'approved', 'rejected'))
);

-- One pending draft per (org, gap_topic) — re-emitting the threshold event
-- for the same topic just updates gap_count rather than creating duplicates.
CREATE UNIQUE INDEX IF NOT EXISTS uq_document_drafts_pending_topic
  ON document_drafts(org_id, gap_topic)
  WHERE status = 'pending_review' AND gap_topic IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_document_drafts_org_status_created
  ON document_drafts(org_id, status, created_at DESC);

ALTER TABLE document_drafts ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS document_drafts_select_admin ON document_drafts;
CREATE POLICY document_drafts_select_admin ON document_drafts
  FOR SELECT USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin')
  );

-- Writes only via service-role (the worker + the admin approve/reject
-- endpoints, which already gate on role='admin' at the FastAPI layer).
-- No general-purpose insert policy.
