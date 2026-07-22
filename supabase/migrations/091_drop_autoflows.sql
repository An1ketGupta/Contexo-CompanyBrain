-- ── Drop the Autoflow engine (reverses migration 048) ─────────────────────
--
-- The V1 autoflow feature (trigger→action automations + admin builder UI) is
-- removed. This drops both autoflow tables and reverts the `approvals`
-- generalization introduced in 048 back to message-only subjects.
--
-- What we KEEP from 048:
--   * approvals.subject_type / subject_id columns and the subject-aware
--     pending-uniqueness index. All approval inserts now depend on these
--     columns (message approvals set subject_type='message'), so removing
--     them would break the surviving approval flow. We only tighten the
--     CHECK back to 'message'.

-- ── Drop autoflow tables ───────────────────────────────────────────────────
-- autoflow_runs first: it FKs autoflows(id) and approvals(id). The CASCADE
-- clears its own rows; approvals rows are untouched by the drop itself.
DROP TABLE IF EXISTS autoflow_runs CASCADE;
DROP TABLE IF EXISTS autoflows CASCADE;

-- The updated_at trigger drops with the table; its backing function does not.
DROP FUNCTION IF EXISTS autoflows_set_updated_at();

-- ── Revert approvals subject generalization to message-only ────────────────
-- Any approval rows that gated an autoflow_run are now orphaned (their subject
-- table is gone). Delete them before re-tightening the constraint.
DELETE FROM approvals WHERE subject_type = 'autoflow_run';

ALTER TABLE approvals
  DROP CONSTRAINT IF EXISTS approvals_subject_type_check;
ALTER TABLE approvals
  ADD  CONSTRAINT approvals_subject_type_check
       CHECK (subject_type IN ('message'));

-- Message subjects must stay consistent with message_id. With autoflow_run
-- gone, every row is a message subject.
ALTER TABLE approvals
  DROP CONSTRAINT IF EXISTS approvals_subject_message_consistency;
ALTER TABLE approvals
  ADD  CONSTRAINT approvals_subject_message_consistency
       CHECK (subject_type = 'message' AND message_id IS NOT NULL AND subject_id = message_id);

COMMENT ON COLUMN approvals.subject_type IS
  'message — the only supported approval subject after the autoflow engine was removed.';
