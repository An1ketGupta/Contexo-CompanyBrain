-- ── 088: Track hiring completion on the requisition ─────────────────────────
-- Replaces the old "Mark hired & start onboarding" form (which collected
-- candidate/offer details inline and kicked off the Onboarding v2 LOI
-- pipeline) with a single "Hiring completed" marker on the requisition
-- itself. NULL = not yet marked complete.
ALTER TABLE job_requisitions
  ADD COLUMN IF NOT EXISTS hiring_completed_at TIMESTAMPTZ;
