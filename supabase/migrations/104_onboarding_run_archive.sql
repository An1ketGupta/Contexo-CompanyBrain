-- ── 104: Archive state for onboarding runs ──────────────────────────────────
--
-- Onboarding runs can't be hard-deleted: the row is what signed LOI/NDA
-- documents, BGV reference tokens, esign envelopes, and the onboarding_events
-- audit trail key off. Same reasoning as job_requisitions (103) — archiving
-- is the retire path instead: reversible, loses nothing, hides the row from
-- the default listing. Works on runs in any status, including active ones —
-- unlike cancel, which only flips non-terminal runs and leaves them listed.
-- NULL = active.

ALTER TABLE onboarding_runs
  ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

ALTER TABLE onboarding_runs
  ADD COLUMN IF NOT EXISTS archived_by UUID REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_onboarding_runs_active
  ON onboarding_runs(org_id, created_at DESC)
  WHERE archived_at IS NULL;
