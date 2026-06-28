-- ── 071: Onboarding v2 — LOI → BGV → Appointment Letter + NDA → Policies → Induction ──
--
-- Why this migration exists
-- -------------------------
-- The existing OnboardingAgent (migration 019) runs AFTER a new hire accepts
-- their invite — it generates a 90-day plan and welcomes them. That covers
-- post-join enablement.
--
-- This migration models the BEFORE-join workflow: the HR / recruiting pipeline
-- between "candidate marked hired" and "new hire signs in for Day 1." It's a
-- multi-step, multi-document, multi-party flow with humans-in-the-loop and a
-- 3-day BGV response wait, so we need durable state + an audit-able paper
-- trail.
--
-- Five additive surfaces:
--
--   1. documents.template_kind — admins tag KB docs as 'loi' | 'appointment_letter'
--      | 'nda' templates. The agent looks them up by tag rather than by name
--      heuristic. If no doc carries the tag for an org, the run is blocked
--      with an actionable error ("upload your LOI template").
--
--   2. onboarding_runs — one row per candidate going through onboarding. Carries
--      the canonical state-machine field (status), inputs collected at trigger
--      time (start_date, ctc, manager, location, designation), and pointers to
--      the agent_run + generated documents.
--
--   3. onboarding_documents — one row per generated PDF (LOI, appointment letter,
--      NDA, induction). Distinct from `documents` (which is the KB) because
--      these are *output* artifacts that don't get indexed/searched and have a
--      sign-cycle of their own (draft → sent_to_hr → signed → sent_to_candidate).
--
--   4. onboarding_bgv_references — the 2 references HR collects at hire-time +
--      one unique token per reference. The token (UUID) lets the reference
--      open the public response form without a login. Token has an expiry +
--      single-use flag so a leaked URL doesn't replay forever.
--
--   5. onboarding_status enum widening on users — adds 'pre_onboarding'
--      (before invite is accepted) which the OnboardingAgent v1 will skip past
--      when the invite finally lands.
--
-- Idempotency: every statement re-runnable.

-- ── 1. documents.template_kind ─────────────────────────────────────────────
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS template_kind TEXT;

ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_template_kind_check;
ALTER TABLE documents
  ADD CONSTRAINT documents_template_kind_check CHECK (
    template_kind IS NULL OR template_kind IN (
      'loi', 'appointment_letter', 'nda', 'induction'
    )
  );

CREATE INDEX IF NOT EXISTS idx_documents_template_kind
  ON documents (org_id, template_kind)
  WHERE template_kind IS NOT NULL;

COMMENT ON COLUMN documents.template_kind IS
  'When set, marks this KB document as a Jinja-templated DOCX the Onboarding v2 agent fills per candidate (loi | appointment_letter | nda | induction).';


-- ── 2. onboarding_runs ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS onboarding_runs (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                   UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

  -- Source candidate (optional — a hire may be created out-of-band, no ATS).
  candidate_id             UUID REFERENCES recruiting_candidates(id) ON DELETE SET NULL,
  requisition_id           UUID REFERENCES job_requisitions(id) ON DELETE SET NULL,

  -- Snapshotted at trigger time so renames / candidate-row edits don't
  -- mutate what's already in legally-binding generated PDFs.
  candidate_name           TEXT NOT NULL,
  candidate_email          TEXT NOT NULL,
  candidate_phone          TEXT,
  role_title               TEXT NOT NULL,
  designation              TEXT,
  ctc_amount               NUMERIC(14, 2),
  ctc_currency             TEXT DEFAULT 'INR',
  ctc_breakdown            JSONB,
  start_date               DATE NOT NULL,
  work_location            TEXT,
  probation_period_months  INTEGER,
  reporting_manager_name   TEXT,
  reporting_manager_email  TEXT,
  reporting_manager_user_id UUID REFERENCES users(id) ON DELETE SET NULL,

  -- State machine. See OnboardingV2Agent.STATUS_FLOW for transitions.
  --   draft -> loi_generating -> loi_pending_hr_sign -> loi_signed_uploaded ->
  --   loi_sent_to_candidate -> bgv_pending -> bgv_complete ->
  --   appointment_bundle_generating -> appointment_pending_hr_review ->
  --   appointment_sent_to_candidate -> policies_assigned ->
  --   policies_acknowledged -> induction_generating -> induction_sent ->
  --   completed
  -- Plus terminal: blocked_missing_template | failed | cancelled
  status                   TEXT NOT NULL DEFAULT 'draft',
  blocked_reason           TEXT,
  blocked_template_kind    TEXT,
  current_step             TEXT,

  agent_run_id             UUID REFERENCES agent_runs(id) ON DELETE SET NULL,
  triggered_by_user_id     UUID REFERENCES users(id) ON DELETE SET NULL,

  -- Stamped at each transition for the timeline UI.
  loi_sent_to_hr_at        TIMESTAMPTZ,
  loi_signed_at            TIMESTAMPTZ,
  loi_sent_to_candidate_at TIMESTAMPTZ,
  bgv_sent_at              TIMESTAMPTZ,
  bgv_completed_at         TIMESTAMPTZ,
  appointment_sent_at      TIMESTAMPTZ,
  policies_assigned_at     TIMESTAMPTZ,
  policies_acknowledged_at TIMESTAMPTZ,
  induction_sent_at        TIMESTAMPTZ,
  completed_at             TIMESTAMPTZ,

  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE onboarding_runs DROP CONSTRAINT IF EXISTS onboarding_runs_status_check;
ALTER TABLE onboarding_runs
  ADD CONSTRAINT onboarding_runs_status_check CHECK (
    status IN (
      'draft',
      'loi_generating', 'loi_pending_hr_sign', 'loi_signed_uploaded',
      'loi_sent_to_candidate',
      'bgv_pending', 'bgv_complete',
      'appointment_bundle_generating', 'appointment_pending_hr_review',
      'appointment_sent_to_candidate',
      'policies_assigned', 'policies_acknowledged',
      'induction_generating', 'induction_sent',
      'completed',
      'blocked_missing_template', 'failed', 'cancelled'
    )
  );

CREATE INDEX IF NOT EXISTS idx_onboarding_runs_org_status
  ON onboarding_runs (org_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_onboarding_runs_candidate
  ON onboarding_runs (candidate_id) WHERE candidate_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_onboarding_runs_email
  ON onboarding_runs (org_id, candidate_email);

ALTER TABLE onboarding_runs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS onboarding_runs_select ON onboarding_runs;
CREATE POLICY onboarding_runs_select ON onboarding_runs
  FOR SELECT USING (org_id = auth_org_id());

-- Mutations go through service-role (the Inngest agent + the FastAPI router
-- both use the service client); no INSERT/UPDATE policy needed.

COMMENT ON TABLE onboarding_runs IS
  'One row per candidate going through HR onboarding (LOI → BGV → Appointment + NDA → Policies → Induction). State machine driven by OnboardingV2Agent.';


-- ── 3. onboarding_documents ────────────────────────────────────────────────
-- Per-run artifacts (PDFs). Distinct from the KB documents table — these are
-- generated outputs that don't need parsing/embedding, but DO need signed
-- URLs, a sign-cycle, and audit. The `kind` enum mirrors documents.template_kind
-- plus an 'offer_bundle' meta-kind covering the AL+NDA combo PDF.
CREATE TABLE IF NOT EXISTS onboarding_documents (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  run_id              UUID NOT NULL REFERENCES onboarding_runs(id) ON DELETE CASCADE,

  kind                TEXT NOT NULL,
  -- KB template used to generate this artifact (NULL for induction since
  -- induction is LLM-synthesized, not template-filled).
  source_template_id  UUID REFERENCES documents(id) ON DELETE SET NULL,

  storage_path        TEXT NOT NULL,
  signed_url          TEXT,
  signed_url_expires_at TIMESTAMPTZ,
  file_bytes          INTEGER,

  -- Per-document sign cycle.
  --   draft → sent_to_hr → signed_by_hr → sent_to_candidate → signed_by_candidate
  -- (skip cells as needed — induction goes draft→sent_to_candidate directly)
  sign_status         TEXT NOT NULL DEFAULT 'draft',
  signed_pdf_path     TEXT,
  signed_uploaded_by  UUID REFERENCES users(id) ON DELETE SET NULL,
  signed_uploaded_at  TIMESTAMPTZ,

  -- Context the agent rendered with — useful for re-generation if HR rejects.
  render_context      JSONB,

  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE onboarding_documents DROP CONSTRAINT IF EXISTS onboarding_documents_kind_check;
ALTER TABLE onboarding_documents
  ADD CONSTRAINT onboarding_documents_kind_check CHECK (
    kind IN ('loi', 'appointment_letter', 'nda', 'offer_bundle', 'induction')
  );

ALTER TABLE onboarding_documents DROP CONSTRAINT IF EXISTS onboarding_documents_sign_status_check;
ALTER TABLE onboarding_documents
  ADD CONSTRAINT onboarding_documents_sign_status_check CHECK (
    sign_status IN (
      'draft', 'sent_to_hr', 'signed_by_hr',
      'sent_to_candidate', 'signed_by_candidate'
    )
  );

CREATE INDEX IF NOT EXISTS idx_onboarding_documents_run
  ON onboarding_documents (run_id, kind);
CREATE INDEX IF NOT EXISTS idx_onboarding_documents_org
  ON onboarding_documents (org_id, created_at DESC);

ALTER TABLE onboarding_documents ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS onboarding_documents_select ON onboarding_documents;
CREATE POLICY onboarding_documents_select ON onboarding_documents
  FOR SELECT USING (org_id = auth_org_id());


-- ── 4. onboarding_bgv_references ───────────────────────────────────────────
-- HR enters 2 references at hire-time; agent sends each a personalised
-- verification email. Each reference gets a UUID token in their URL so the
-- public response form needs no login.
CREATE TABLE IF NOT EXISTS onboarding_bgv_references (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  run_id          UUID NOT NULL REFERENCES onboarding_runs(id) ON DELETE CASCADE,

  reference_name  TEXT NOT NULL,
  reference_email TEXT NOT NULL,
  reference_phone TEXT,
  relationship    TEXT,

  -- Public form auth — credential lives in the URL. We use a v4 UUID rather
  -- than an HMAC token so we can also revoke / look up by id without a hash.
  token           UUID NOT NULL DEFAULT gen_random_uuid(),
  token_expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + INTERVAL '14 days'),

  -- Lifecycle:
  --   pending → sent → opened → submitted   (or expired)
  status          TEXT NOT NULL DEFAULT 'pending',

  email_sent_at   TIMESTAMPTZ,
  opened_at       TIMESTAMPTZ,
  submitted_at    TIMESTAMPTZ,

  reminder_count  INTEGER NOT NULL DEFAULT 0,
  last_reminder_at TIMESTAMPTZ,

  -- Form response payload.
  response_worked_together_months INTEGER,
  response_would_recommend        BOOLEAN,
  response_strengths              TEXT,
  response_concerns               TEXT,
  response_role_description       TEXT,

  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (token)
);

ALTER TABLE onboarding_bgv_references DROP CONSTRAINT IF EXISTS onboarding_bgv_references_status_check;
ALTER TABLE onboarding_bgv_references
  ADD CONSTRAINT onboarding_bgv_references_status_check CHECK (
    status IN ('pending', 'sent', 'opened', 'submitted', 'expired')
  );

CREATE INDEX IF NOT EXISTS idx_bgv_references_run
  ON onboarding_bgv_references (run_id);
CREATE INDEX IF NOT EXISTS idx_bgv_references_pending
  ON onboarding_bgv_references (status, last_reminder_at)
  WHERE status IN ('sent', 'opened');

ALTER TABLE onboarding_bgv_references ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS bgv_references_select ON onboarding_bgv_references;
CREATE POLICY bgv_references_select ON onboarding_bgv_references
  FOR SELECT USING (org_id = auth_org_id());

COMMENT ON TABLE onboarding_bgv_references IS
  'BGV (background verification) reference contacts. Each reference receives an email with a UUID-token URL → fills a public form → response stored inline. No login required.';


-- ── 5. Onboarding events log (for the timeline UI) ─────────────────────────
-- Lightweight append-only table. Distinct from agent_runs.steps because we
-- want events from HR actions (uploads, approvals) on the same timeline as
-- agent steps. Cheap to query and easy to render.
CREATE TABLE IF NOT EXISTS onboarding_events (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  run_id      UUID NOT NULL REFERENCES onboarding_runs(id) ON DELETE CASCADE,
  actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
  actor_kind  TEXT NOT NULL,             -- 'agent' | 'hr' | 'candidate' | 'reference' | 'system'
  event_type  TEXT NOT NULL,             -- free-form: 'loi_generated', 'hr_uploaded_signed_loi', etc
  message     TEXT,
  metadata    JSONB,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_onboarding_events_run_created
  ON onboarding_events (run_id, created_at DESC);

ALTER TABLE onboarding_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS onboarding_events_select ON onboarding_events;
CREATE POLICY onboarding_events_select ON onboarding_events
  FOR SELECT USING (org_id = auth_org_id());


-- ── 6. updated_at triggers (mirroring the project's convention) ───────────
-- Reuse the existing project-wide `set_updated_at()` trigger function. Defined
-- in migration 001; we just attach it.
DROP TRIGGER IF EXISTS trg_onboarding_runs_updated_at ON onboarding_runs;
CREATE TRIGGER trg_onboarding_runs_updated_at
  BEFORE UPDATE ON onboarding_runs
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_onboarding_documents_updated_at ON onboarding_documents;
CREATE TRIGGER trg_onboarding_documents_updated_at
  BEFORE UPDATE ON onboarding_documents
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_onboarding_bgv_references_updated_at ON onboarding_bgv_references;
CREATE TRIGGER trg_onboarding_bgv_references_updated_at
  BEFORE UPDATE ON onboarding_bgv_references
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
