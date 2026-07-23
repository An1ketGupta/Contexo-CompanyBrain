-- ── 092: Drop the Marketing & Sales agents (reverses 052/076/077/079) ─────
--
-- The Marketing (Briefs), Sales agent (Deals pipeline), RFP responses,
-- Sequences, and Pre-call brief features are removed and deferred to V2. This
-- drops every table / column / function those features owned. The application
-- code, routers, Inngest functions, and UI have already been deleted.
--
-- What we KEEP:
--   * agent_runs rows produced by these agents — historical audit records.
--     Their run_id FKs were ON DELETE SET NULL, so nothing dangles.
--   * The `sales_enablement` KB use-case category (organizations.primary_use_case,
--     the seed recommendations, and default AI instructions) — that's a
--     knowledge-base classification, unrelated to the removed Sales agent.
--   * _touch_updated_at() (from 052) — a shared trigger function still used by
--     surviving tables. Only the sequence tables' triggers drop with them.
--
-- Idempotent: every statement guarded.

-- ── Marketing (079) ───────────────────────────────────────────────────────
DROP TABLE IF EXISTS marketing_briefs CASCADE;
DROP FUNCTION IF EXISTS marketing_briefs_touch_updated_at();

-- ── RFP (rfp_responses created in 053; child tables + org column in 077) ──
-- Children first, though CASCADE would clear the FKs either way.
DROP TABLE IF EXISTS rfp_approvals CASCADE;
DROP TABLE IF EXISTS rfp_answers CASCADE;
DROP TABLE IF EXISTS rfp_requirements CASCADE;
DROP TABLE IF EXISTS rfp_responses CASCADE;

ALTER TABLE organizations
  DROP COLUMN IF EXISTS rfp_approved_collection_id;

-- ── Sales agent / Deals pipeline (076) ────────────────────────────────────
DROP TABLE IF EXISTS deal_events CASCADE;
DROP TABLE IF EXISTS deal_approvals CASCADE;
DROP TABLE IF EXISTS deal_research CASCADE;
DROP TABLE IF EXISTS deal_documents CASCADE;
DROP TABLE IF EXISTS deal_runs CASCADE;
DROP FUNCTION IF EXISTS sales_agent_touch_updated_at();

-- Revert documents.template_kind_check to the onboarding-only set that 076
-- widened. Null out any doc tagged with a now-defunct sales kind before we
-- re-tighten the constraint, or the ADD CONSTRAINT would fail.
UPDATE documents
   SET template_kind = NULL
 WHERE template_kind IN (
   'icp', 'proposal', 'sow', 'pricing_sheet', 'battle_card',
   'sales_value_props', 'sales_tone_guide', 'sales_case_study'
 );

ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_template_kind_check;
ALTER TABLE documents
  ADD CONSTRAINT documents_template_kind_check CHECK (
    template_kind IS NULL OR template_kind IN (
      'loi', 'appointment_letter', 'nda', 'induction'
    )
  );

COMMENT ON COLUMN documents.template_kind IS
  'Tags a KB document as a typed template / reference doc an agent looks up by '
  'kind. Onboarding agent uses loi/appointment_letter/nda/induction.';

-- ── Sequences (052) ───────────────────────────────────────────────────────
-- sequence_steps FKs sequences(id); drop the child first.
DROP TABLE IF EXISTS sequence_steps CASCADE;
DROP TABLE IF EXISTS sequences CASCADE;
