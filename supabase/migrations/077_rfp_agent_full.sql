-- ── 077: RFP Agent — full state-machine + per-requirement normalization ───
--
-- Why this migration exists
-- -------------------------
-- The RFP scaffolding from migration 053 (rfp_responses) was a synchronous,
-- single-shot pipeline: upload → parse → review → generate → DOCX. To turn it
-- into a real production agent that mirrors the SalesAgent / OnboardingV2Agent
-- pattern, we need:
--
--   1. Per-row normalization. The JSONB `requirements` array on rfp_responses
--      worked for small RFPs but doesn't scale: every per-row edit triggers a
--      whole-array UPDATE, there is no RLS at the row level, no FK-able answer
--      table, and reps cannot collaborate on independent rows. Split into
--      `rfp_requirements` + `rfp_answers`.
--
--   2. Approval gates. Legal review on customer-facing artifacts is a hard
--      requirement for enterprise prospects. Mirror the deal_approvals table
--      from migration 076 with `rfp_approvals`.
--
--   3. State machine. Widen the status enum from {extracted, reviewed,
--      generating, ready, failed} to also cover: awaiting_requirements_review,
--      drafting, awaiting_rep_review, awaiting_legal_review, legal_rejected,
--      finalizing. Old states kept for rows already created.
--
--   4. Format-preserving export. Real RFPs arrive as the buyer's spreadsheet
--      with a specific Answer column. We must round-trip into THAT file, not
--      emit a fresh DOCX. So we persist the original source bytes in Supabase
--      Storage and remember the source format + the answer cell address per
--      requirement.
--
--   5. Linkage to other agents. Add `deal_run_id` (so an RFP is tied to a sales
--      opportunity) and `collection_id` (so the agent can scope retrieval to
--      the org's "RFP-approved" collection rather than the whole KB).
--
--   6. Audit. Add `run_id` referencing agent_runs so the agent's per-step log
--      surfaces in the admin runs view alongside Sales/Onboarding/etc.
--
-- RLS: org-scoped select via auth_org_id(); mutations through service-role
-- (Inngest + FastAPI). Same convention as 076.
--
-- Idempotent — every statement re-runnable.

-- ── 1. Extend rfp_responses ───────────────────────────────────────────────

ALTER TABLE rfp_responses
  ADD COLUMN IF NOT EXISTS run_id              UUID REFERENCES agent_runs(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS deal_run_id         UUID REFERENCES deal_runs(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS collection_id       UUID REFERENCES collections(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS title               TEXT,
  ADD COLUMN IF NOT EXISTS source_format       TEXT,
  ADD COLUMN IF NOT EXISTS source_storage_path TEXT,
  ADD COLUMN IF NOT EXISTS filled_output_storage_path  TEXT,
  ADD COLUMN IF NOT EXISTS summary_output_storage_path TEXT,
  ADD COLUMN IF NOT EXISTS approved_by_rep_at       TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS approved_by_rep_user_id  UUID REFERENCES users(id),
  ADD COLUMN IF NOT EXISTS approved_by_legal_at     TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS approved_by_legal_user_id UUID REFERENCES users(id),
  ADD COLUMN IF NOT EXISTS legal_required           BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS legal_rejection_notes    TEXT;

-- Widen status enum. Keep legacy values; add the state-machine ones.
ALTER TABLE rfp_responses DROP CONSTRAINT IF EXISTS rfp_responses_status_check;
ALTER TABLE rfp_responses
  ADD CONSTRAINT rfp_responses_status_check CHECK (
    status IN (
      -- legacy
      'extracted', 'reviewed', 'generating', 'ready', 'failed',
      -- state machine (this migration)
      'extracting',
      'awaiting_requirements_review',
      'drafting',
      'awaiting_rep_review',
      'awaiting_legal_review',
      'legal_rejected',
      'finalizing'
    )
  );

ALTER TABLE rfp_responses
  ADD CONSTRAINT rfp_responses_source_format_check CHECK (
    source_format IS NULL OR source_format IN ('xlsx', 'docx', 'pdf', 'csv', 'md', 'txt')
  );

CREATE INDEX IF NOT EXISTS idx_rfp_responses_status
  ON rfp_responses(org_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rfp_responses_deal_run
  ON rfp_responses(deal_run_id) WHERE deal_run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_rfp_responses_run_id
  ON rfp_responses(run_id) WHERE run_id IS NOT NULL;


-- ── 2. rfp_requirements ───────────────────────────────────────────────────
--
-- One row per parsed question. Replaces (going forward) the JSONB array on
-- rfp_responses.requirements. Old rows keep their JSONB; new flows write here.
--
-- source_row_index + source_sheet + source_answer_cell are the format-
-- preservation breadcrumbs: at export time the agent loads the original
-- file and writes the answer back into the cell at (sheet, row, column).
-- They're nullable because PDFs / free-text DOCX have no cell address.

CREATE TABLE IF NOT EXISTS rfp_requirements (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  rfp_id             UUID NOT NULL REFERENCES rfp_responses(id) ON DELETE CASCADE,
  org_id             UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  ordinal            INTEGER NOT NULL,
  external_ref       TEXT,                  -- buyer's row id ("R1.2", "Q14")
  requirement_text   TEXT NOT NULL,
  category           TEXT,
  -- Format-preservation breadcrumbs (XLSX only — null otherwise)
  source_sheet           TEXT,
  source_row_index       INTEGER,           -- 1-based, matches openpyxl
  source_answer_column   TEXT,              -- column letter ("C") for fill-in
  -- Semantic dedup: similar questions get the same cluster_id so the answerer
  -- runs once and shares the answer across all members.
  cluster_id         UUID,
  is_cluster_lead    BOOLEAN NOT NULL DEFAULT TRUE,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rfp_requirements_rfp ON rfp_requirements(rfp_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_rfp_requirements_cluster ON rfp_requirements(cluster_id) WHERE cluster_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uniq_rfp_requirements_rfp_ordinal
  ON rfp_requirements(rfp_id, ordinal);

ALTER TABLE rfp_requirements ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "rfp_requirements_select" ON rfp_requirements;
CREATE POLICY "rfp_requirements_select" ON rfp_requirements
  FOR SELECT USING (org_id = auth_org_id());

DROP POLICY IF EXISTS "rfp_requirements_update" ON rfp_requirements;
CREATE POLICY "rfp_requirements_update" ON rfp_requirements
  FOR UPDATE USING (org_id = auth_org_id());


-- ── 3. rfp_answers ────────────────────────────────────────────────────────
--
-- One row per (rfp_id, requirement_id). Status maps the answer lifecycle:
--   draft     — agent just produced; awaiting rep review
--   edited    — rep edited; treat answer_text as truth, ignore generated
--   approved  — rep approved as-is or with edits
--   gap       — agent could not find supporting docs; needs legal/SME review
--   rejected  — legal rejected (notes carry reason); agent will regenerate
--
-- Sources is a JSONB array of {document_id, document_name, chunk_id, similarity}
-- so the citation UI can deep-link back to the chunk.

CREATE TABLE IF NOT EXISTS rfp_answers (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  rfp_id             UUID NOT NULL REFERENCES rfp_responses(id) ON DELETE CASCADE,
  requirement_id     UUID NOT NULL REFERENCES rfp_requirements(id) ON DELETE CASCADE,
  org_id             UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  answer_text        TEXT,
  sources            JSONB NOT NULL DEFAULT '[]',
  confidence         NUMERIC,         -- 0..1, top match similarity
  confidence_band    TEXT,            -- 'high' | 'medium' | 'low' | 'none'
  search_queries     JSONB NOT NULL DEFAULT '[]',  -- LLM-issued tool queries
  status             TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'edited', 'approved', 'gap', 'rejected')),
  flag_message       TEXT,
  edited_by          UUID REFERENCES users(id),
  edited_at          TIMESTAMPTZ,
  generated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  llm_tokens         INTEGER NOT NULL DEFAULT 0,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_rfp_answers_req ON rfp_answers(requirement_id);
CREATE INDEX IF NOT EXISTS idx_rfp_answers_rfp ON rfp_answers(rfp_id);
CREATE INDEX IF NOT EXISTS idx_rfp_answers_status ON rfp_answers(rfp_id, status);

ALTER TABLE rfp_answers ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "rfp_answers_select" ON rfp_answers;
CREATE POLICY "rfp_answers_select" ON rfp_answers
  FOR SELECT USING (org_id = auth_org_id());

DROP POLICY IF EXISTS "rfp_answers_update" ON rfp_answers;
CREATE POLICY "rfp_answers_update" ON rfp_answers
  FOR UPDATE USING (org_id = auth_org_id());


-- ── 4. rfp_approvals ──────────────────────────────────────────────────────
--
-- Mirrors deal_approvals shape but RFP-specific. Two gate kinds:
--   rep   — rep signs off on all answers (mass approval)
--   legal — legal/compliance signs off; can reject with notes → kicks the
--           agent back into drafting for flagged requirements.
--
-- We don't track per-row approvals here — those live on rfp_answers.status.
-- This table is the "package-level" gate.

CREATE TABLE IF NOT EXISTS rfp_approvals (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  rfp_id             UUID NOT NULL REFERENCES rfp_responses(id) ON DELETE CASCADE,
  org_id             UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  gate_type          TEXT NOT NULL CHECK (gate_type IN ('rep', 'legal')),
  status             TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled')),
  reviewer_user_id   UUID REFERENCES users(id),
  decided_at         TIMESTAMPTZ,
  notes              TEXT,
  -- When legal rejects, the rows they want re-drafted. JSONB array of
  -- rfp_answers.id strings.
  rejected_requirement_ids JSONB NOT NULL DEFAULT '[]',
  revision           INTEGER NOT NULL DEFAULT 1,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rfp_approvals_rfp ON rfp_approvals(rfp_id);
CREATE INDEX IF NOT EXISTS idx_rfp_approvals_open
  ON rfp_approvals(org_id, gate_type, status) WHERE status = 'pending';

ALTER TABLE rfp_approvals ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "rfp_approvals_select" ON rfp_approvals;
CREATE POLICY "rfp_approvals_select" ON rfp_approvals
  FOR SELECT USING (org_id = auth_org_id());

DROP POLICY IF EXISTS "rfp_approvals_update" ON rfp_approvals;
CREATE POLICY "rfp_approvals_update" ON rfp_approvals
  FOR UPDATE USING (org_id = auth_org_id());


-- ── 5. organizations.rfp_approved_collection_id ───────────────────────────
--
-- The "rfp-approved KB scope" is org-level config: a single collection_id
-- the org wires up in Settings → Integrations. The RFP agent uses it as
-- the default retrieval scope (avoids pulling internal docs into a customer-
-- facing response). Individual RFPs can still override via rfp_responses.collection_id.

ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS rfp_approved_collection_id UUID REFERENCES collections(id) ON DELETE SET NULL;


-- ── 6. Storage bucket for RFP files ───────────────────────────────────────
--
-- We deliberately use a private bucket distinct from the KB ingestion bucket.
-- RFP buyer files are sensitive (often under NDA) and shouldn't be exposed
-- to KB search even by accident.
INSERT INTO storage.buckets (id, name, public)
VALUES ('rfp-files', 'rfp-files', FALSE)
ON CONFLICT (id) DO NOTHING;
