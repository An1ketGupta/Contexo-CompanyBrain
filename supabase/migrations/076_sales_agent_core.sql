-- ── 076: Sales Agent — Lead → Research → Outreach → Meeting → Proposal → Close ──
--
-- Why this migration exists
-- -------------------------
-- The Sales Agent is a re-entrant, state-machine-driven autonomous pipeline
-- modelled after OnboardingV2Agent (migration 071+). A "deal run" walks a
-- single prospect from a freshly-entered lead all the way to closed_won /
-- closed_lost, with humans-in-the-loop at outreach approval, proposal
-- approval, and the next-call-vs-propose fork after each call.
--
-- This migration adds:
--
--   1. documents.template_kind — widen the existing enum (from migration 071)
--      with sales-side template kinds: 'icp', 'proposal', 'sow', 'pricing_sheet',
--      'battle_card'. The agent looks them up by tag rather than by name
--      heuristic. If a required template is missing the run blocks on
--      `blocked_missing_template` until HR/admin uploads one.
--
--   2. deal_runs — one row per opportunity. Canonical state-machine field
--      (status), snapshot of lead identity, per-stage timestamps, ICP score,
--      outreach drafts, meeting + call data (most-recent; full history in
--      deal_events because deals are re-entrant on meetings), and close info.
--
--   3. deal_documents — generated artefacts (proposal/sow/pricing) similar
--      shape to onboarding_documents. We keep BOTH the agent draft and any
--      rep-uploaded edit for audit.
--
--   4. deal_research — stored research output per stage 1 run. Separate row
--      so a re-research (manual rep retry) keeps history; the agent uses the
--      latest.
--
--   5. deal_approvals — dedicated human-gate table. Outreach and proposal
--      gates need draft → edit → approve tracking (revision count, edited
--      content/path), which is a different shape than the generic
--      approval_requests table; per the architectural decision in
--      Sales_Agent.md we keep this separate.
--
--   6. deal_events — append-only audit timeline (agent + rep + system).
--
--   7. Gmail thread tracking — outreach_thread_id + outreach_message_id on
--      deal_runs so a Gmail watch / push notification can map an inbound
--      reply back to its deal. Used by the automated reply-detection path.
--
-- RLS: select-only policies using the existing `auth_org_id()` helper.
-- Mutations go through the service-role client (the Inngest agent + FastAPI
-- routers both use it), matching the onboarding_v2 convention.
--
-- Idempotent — every statement re-runnable.

-- ── 1. documents.template_kind — widen with sales kinds ───────────────────

ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_template_kind_check;
ALTER TABLE documents
  ADD CONSTRAINT documents_template_kind_check CHECK (
    template_kind IS NULL OR template_kind IN (
      -- onboarding (migrations 071+)
      'loi', 'appointment_letter', 'nda', 'induction',
      -- sales agent (this migration)
      'icp', 'proposal', 'sow', 'pricing_sheet', 'battle_card',
      'sales_value_props', 'sales_tone_guide', 'sales_case_study'
    )
  );

COMMENT ON COLUMN documents.template_kind IS
  'Tags a KB document as a typed template / reference doc that an agent looks '
  'up by kind. Onboarding agent uses loi/appointment_letter/nda/induction. '
  'Sales agent uses icp/proposal/sow/pricing_sheet/battle_card and the '
  'reference kinds sales_value_props/sales_tone_guide/sales_case_study.';


-- ── 2. deal_runs — opportunity state machine ──────────────────────────────

CREATE TABLE IF NOT EXISTS deal_runs (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                   UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

  -- Rep who owns this deal.
  created_by               UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,

  -- Audit pointer to the agent_runs row (one created per execution; long-lived
  -- deals re-use the deal_run but spawn multiple agent_runs for each stage).
  agent_run_id             UUID,

  -- Lead identity (snapshotted at intake — renames don't mutate proposals).
  company_name             TEXT NOT NULL,
  company_website          TEXT,
  company_industry         TEXT,
  contact_name             TEXT,
  contact_title            TEXT,
  contact_email            TEXT,
  contact_linkedin_url     TEXT,
  notes                    TEXT,

  -- State machine. See SalesAgent.STATUS_FLOW for transitions.
  status                   TEXT NOT NULL DEFAULT 'lead_entered',
  previous_status          TEXT, -- restore target when resuming from blocked_*
  blocked_reason           TEXT,
  blocked_template_kind    TEXT,
  current_step             TEXT,

  -- ICP scoring (stage 1)
  icp_score                INTEGER,
  icp_rationale            TEXT,
  icp_scored_at            TIMESTAMPTZ,

  -- Outreach drafts (stage 2). Latest revision lives here for fast read;
  -- full revision history is in deal_approvals.
  outreach_subject         TEXT,
  outreach_email_body      TEXT,
  outreach_linkedin_body   TEXT,
  outreach_edited_at       TIMESTAMPTZ,
  outreach_revision        INTEGER NOT NULL DEFAULT 0,
  outreach_sent_at         TIMESTAMPTZ,

  -- Gmail thread tracking (for automated reply detection via watch())
  outreach_thread_id       TEXT,
  outreach_message_id      TEXT,
  reply_received_at        TIMESTAMPTZ,

  -- Follow-ups (stage 3)
  followup_count           INTEGER NOT NULL DEFAULT 0,
  last_followup_at         TIMESTAMPTZ,
  next_followup_due_at     TIMESTAMPTZ,

  -- Meeting / prep (stage 4) — most recent; full history in deal_events
  meeting_at               TIMESTAMPTZ,
  meeting_count            INTEGER NOT NULL DEFAULT 0,
  prep_brief               JSONB,
  prep_generated_at        TIMESTAMPTZ,

  -- Call summary (stage 5) — most recent; full history in deal_events
  call_transcript          TEXT,
  call_summary             TEXT,
  bant_json                JSONB,
  objections_json          JSONB,
  next_steps_json          JSONB,
  recommended_stage        TEXT,
  call_summarized_at       TIMESTAMPTZ,

  -- Proposal (stage 6)
  proposal_draft_path      TEXT,
  proposal_draft_pdf_path  TEXT,
  proposal_edited_path     TEXT,
  proposal_edited_pdf_path TEXT,
  proposal_revision        INTEGER NOT NULL DEFAULT 0,
  proposal_sent_at         TIMESTAMPTZ,

  -- Awaiting decision (stage 7)
  checkin_count            INTEGER NOT NULL DEFAULT 0,
  last_checkin_at          TIMESTAMPTZ,
  next_checkin_due_at      TIMESTAMPTZ,

  -- Close
  closed_at                TIMESTAMPTZ,
  close_outcome            TEXT, -- 'won' | 'lost'
  close_reason             TEXT,
  close_notes              TEXT,

  -- Total deal value (rep-entered or extracted)
  deal_value_amount        NUMERIC(14, 2),
  deal_value_currency      TEXT DEFAULT 'USD',

  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE deal_runs DROP CONSTRAINT IF EXISTS deal_runs_status_check;
ALTER TABLE deal_runs
  ADD CONSTRAINT deal_runs_status_check CHECK (
    status IN (
      -- Stage 1: research
      'lead_entered', 'researching', 'icp_scoring', 'icp_scored',
      -- Stage 2: outreach
      'outreach_drafting', 'outreach_pending_rep_review', 'outreach_sent',
      -- Stage 3: waiting / follow-ups
      'awaiting_reply', 'followup_pending_rep_review', 'no_reply_closed',
      -- Stage 4: meeting + prep
      'meeting_booked', 'prep_generating', 'prep_ready',
      -- Stage 5: call summary
      'call_summarizing', 'call_summarized', 'awaiting_next_step_decision',
      -- Stage 6: proposal
      'proposal_drafting', 'proposal_pending_rep_review',
      'proposal_sent',
      -- Stage 7: decision
      'awaiting_decision', 'checkin_pending_rep_review', 'at_risk',
      -- Terminal
      'closed_won', 'closed_lost', 'cancelled',
      -- Blocked
      'blocked_missing_icp_doc', 'blocked_missing_template', 'failed'
    )
  );

ALTER TABLE deal_runs DROP CONSTRAINT IF EXISTS deal_runs_close_outcome_check;
ALTER TABLE deal_runs
  ADD CONSTRAINT deal_runs_close_outcome_check CHECK (
    close_outcome IS NULL OR close_outcome IN ('won', 'lost')
  );

ALTER TABLE deal_runs DROP CONSTRAINT IF EXISTS deal_runs_icp_score_range;
ALTER TABLE deal_runs
  ADD CONSTRAINT deal_runs_icp_score_range CHECK (
    icp_score IS NULL OR (icp_score BETWEEN 1 AND 10)
  );

CREATE INDEX IF NOT EXISTS idx_deal_runs_org_status
  ON deal_runs (org_id, status);
CREATE INDEX IF NOT EXISTS idx_deal_runs_owner
  ON deal_runs (org_id, created_by, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_deal_runs_awaiting_reply
  ON deal_runs (status, next_followup_due_at)
  WHERE status = 'awaiting_reply';
CREATE INDEX IF NOT EXISTS idx_deal_runs_awaiting_decision
  ON deal_runs (status, next_checkin_due_at)
  WHERE status = 'awaiting_decision';
CREATE INDEX IF NOT EXISTS idx_deal_runs_outreach_thread
  ON deal_runs (org_id, outreach_thread_id)
  WHERE outreach_thread_id IS NOT NULL;

ALTER TABLE deal_runs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS deal_runs_select ON deal_runs;
CREATE POLICY deal_runs_select ON deal_runs
  FOR SELECT USING (org_id = auth_org_id());

COMMENT ON TABLE deal_runs IS
  'One row per opportunity in the Sales Agent pipeline. Re-entrant state machine '
  'driven by SalesAgent. Meeting/call data on the row reflects the MOST RECENT '
  'iteration; full history lives in deal_events.';
COMMENT ON COLUMN deal_runs.outreach_thread_id IS
  'Gmail thread ID returned by the send call. Used by the gmail-watch / push '
  'notification webhook to map an inbound reply back to this deal.';
COMMENT ON COLUMN deal_runs.meeting_count IS
  'Increments every time the deal re-enters meeting_booked. Multi-call deals '
  'are expected. The current meeting_at / prep_brief / call_summary reflect '
  'iteration N; iterations 1..N-1 live in deal_events payloads.';


-- ── 3. deal_documents — proposals, SOWs, pricing sheets ───────────────────

CREATE TABLE IF NOT EXISTS deal_documents (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  deal_run_id         UUID NOT NULL REFERENCES deal_runs(id) ON DELETE CASCADE,

  kind                TEXT NOT NULL,
  source_template_id  UUID REFERENCES documents(id) ON DELETE SET NULL,

  storage_path        TEXT NOT NULL,
  pdf_storage_path    TEXT,
  file_bytes          INTEGER,

  -- Agent-generated draft vs rep-uploaded edit (audit trail of who produced it).
  source              TEXT NOT NULL DEFAULT 'agent_generated',
  revision            INTEGER NOT NULL DEFAULT 1,

  -- Per-document sign cycle, mirrors onboarding_documents for consistency.
  sign_status         TEXT NOT NULL DEFAULT 'draft',
  signed_pdf_path     TEXT,
  signed_uploaded_by  UUID REFERENCES users(id) ON DELETE SET NULL,
  signed_uploaded_at  TIMESTAMPTZ,

  render_context      JSONB,

  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE deal_documents DROP CONSTRAINT IF EXISTS deal_documents_kind_check;
ALTER TABLE deal_documents
  ADD CONSTRAINT deal_documents_kind_check CHECK (
    kind IN ('proposal', 'sow', 'pricing_sheet')
  );

ALTER TABLE deal_documents DROP CONSTRAINT IF EXISTS deal_documents_source_check;
ALTER TABLE deal_documents
  ADD CONSTRAINT deal_documents_source_check CHECK (
    source IN ('agent_generated', 'rep_uploaded')
  );

ALTER TABLE deal_documents DROP CONSTRAINT IF EXISTS deal_documents_sign_status_check;
ALTER TABLE deal_documents
  ADD CONSTRAINT deal_documents_sign_status_check CHECK (
    sign_status IN ('draft', 'sent_to_prospect', 'signed_by_prospect')
  );

CREATE INDEX IF NOT EXISTS idx_deal_documents_deal
  ON deal_documents (deal_run_id, kind, revision DESC);
CREATE INDEX IF NOT EXISTS idx_deal_documents_org
  ON deal_documents (org_id, created_at DESC);

ALTER TABLE deal_documents ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS deal_documents_select ON deal_documents;
CREATE POLICY deal_documents_select ON deal_documents
  FOR SELECT USING (org_id = auth_org_id());


-- ── 4. deal_research — stored research output ─────────────────────────────

CREATE TABLE IF NOT EXISTS deal_research (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                 UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  deal_run_id            UUID NOT NULL REFERENCES deal_runs(id) ON DELETE CASCADE,

  -- 'kb' = exclusively KB signal; 'web_fallback' = used Tavily; 'merged' = both.
  source                 TEXT NOT NULL,

  company_summary        TEXT,
  headcount_estimate     TEXT,
  industry               TEXT,
  tech_stack_hints       JSONB, -- ["aws", "salesforce", ...]
  pain_point_hypothesis  TEXT,
  competitors_detected   JSONB, -- ["hubspot", ...] — drives battle card lookup
  similar_past_deals     JSONB, -- [{deal_run_id, company_name, outcome, similarity}]
  raw_sources            JSONB, -- [{type: 'kb'|'web', id|url, snippet}]
  has_usable_signal      BOOLEAN NOT NULL DEFAULT false,

  created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE deal_research DROP CONSTRAINT IF EXISTS deal_research_source_check;
ALTER TABLE deal_research
  ADD CONSTRAINT deal_research_source_check CHECK (
    source IN ('kb', 'web_fallback', 'merged')
  );

CREATE INDEX IF NOT EXISTS idx_deal_research_deal
  ON deal_research (deal_run_id, created_at DESC);

ALTER TABLE deal_research ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS deal_research_select ON deal_research;
CREATE POLICY deal_research_select ON deal_research
  FOR SELECT USING (org_id = auth_org_id());

COMMENT ON TABLE deal_research IS
  'Stored research output for a deal. Latest row is the canonical research the '
  'agent uses for downstream stages; older rows kept for audit + comparison '
  'when a rep manually re-runs research.';


-- ── 5. deal_approvals — dedicated human-gate table ────────────────────────

CREATE TABLE IF NOT EXISTS deal_approvals (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  deal_run_id     UUID NOT NULL REFERENCES deal_runs(id) ON DELETE CASCADE,

  -- Which gate this approval is for. outreach covers cold + follow-ups +
  -- check-ins (they're all rep-reviewed emails); proposal covers the
  -- proposal/SOW/pricing bundle.
  gate_type       TEXT NOT NULL,
  -- Specific gate variant when multiple flavors share gate_type ('cold',
  -- 'followup', 'checkin').
  gate_variant    TEXT,

  -- Draft content. For outreach we store inline text. For proposal we point
  -- at deal_documents via draft_document_id and storage paths.
  draft_subject   TEXT,
  draft_content   TEXT,
  draft_document_id UUID REFERENCES deal_documents(id) ON DELETE SET NULL,

  -- Rep edits — either inline text (outreach) or a file upload (proposal).
  edited_subject  TEXT,
  edited_content  TEXT,
  edited_document_id UUID REFERENCES deal_documents(id) ON DELETE SET NULL,

  revision        INTEGER NOT NULL DEFAULT 1,
  status          TEXT NOT NULL DEFAULT 'pending',

  decided_by      UUID REFERENCES users(id) ON DELETE SET NULL,
  decided_at      TIMESTAMPTZ,

  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE deal_approvals DROP CONSTRAINT IF EXISTS deal_approvals_gate_type_check;
ALTER TABLE deal_approvals
  ADD CONSTRAINT deal_approvals_gate_type_check CHECK (
    gate_type IN ('outreach', 'proposal')
  );

ALTER TABLE deal_approvals DROP CONSTRAINT IF EXISTS deal_approvals_status_check;
ALTER TABLE deal_approvals
  ADD CONSTRAINT deal_approvals_status_check CHECK (
    status IN ('pending', 'approved', 'regenerate_requested', 'cancelled')
  );

CREATE INDEX IF NOT EXISTS idx_deal_approvals_deal
  ON deal_approvals (deal_run_id, gate_type, revision DESC);
CREATE INDEX IF NOT EXISTS idx_deal_approvals_pending
  ON deal_approvals (org_id, status, created_at DESC)
  WHERE status = 'pending';

ALTER TABLE deal_approvals ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS deal_approvals_select ON deal_approvals;
CREATE POLICY deal_approvals_select ON deal_approvals
  FOR SELECT USING (org_id = auth_org_id());

COMMENT ON TABLE deal_approvals IS
  'Per-deal human-in-the-loop gates. Separate from approval_requests because '
  'the sales gates need draft → edit → approve tracking with revisions and '
  'inline content storage, which has a different shape than the generic queue.';


-- ── 6. deal_events — audit timeline ───────────────────────────────────────

CREATE TABLE IF NOT EXISTS deal_events (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  deal_run_id     UUID NOT NULL REFERENCES deal_runs(id) ON DELETE CASCADE,

  actor_kind      TEXT NOT NULL, -- 'agent' | 'rep' | 'prospect' | 'system'
  actor_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,

  event_type      TEXT NOT NULL,
  from_status     TEXT,
  to_status       TEXT,
  message         TEXT,
  payload         JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE deal_events DROP CONSTRAINT IF EXISTS deal_events_actor_kind_check;
ALTER TABLE deal_events
  ADD CONSTRAINT deal_events_actor_kind_check CHECK (
    actor_kind IN ('agent', 'rep', 'prospect', 'system')
  );

CREATE INDEX IF NOT EXISTS idx_deal_events_deal_created
  ON deal_events (deal_run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_deal_events_org_created
  ON deal_events (org_id, created_at DESC);

ALTER TABLE deal_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS deal_events_select ON deal_events;
CREATE POLICY deal_events_select ON deal_events
  FOR SELECT USING (org_id = auth_org_id());


-- ── 7. updated_at triggers ────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION sales_agent_touch_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tg_deal_runs_updated ON deal_runs;
CREATE TRIGGER tg_deal_runs_updated
  BEFORE UPDATE ON deal_runs
  FOR EACH ROW EXECUTE FUNCTION sales_agent_touch_updated_at();

DROP TRIGGER IF EXISTS tg_deal_documents_updated ON deal_documents;
CREATE TRIGGER tg_deal_documents_updated
  BEFORE UPDATE ON deal_documents
  FOR EACH ROW EXECUTE FUNCTION sales_agent_touch_updated_at();

DROP TRIGGER IF EXISTS tg_deal_approvals_updated ON deal_approvals;
CREATE TRIGGER tg_deal_approvals_updated
  BEFORE UPDATE ON deal_approvals
  FOR EACH ROW EXECUTE FUNCTION sales_agent_touch_updated_at();
