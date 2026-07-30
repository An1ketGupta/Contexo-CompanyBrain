-- ── 103: Archive state for requisitions ─────────────────────────────────────
--
-- Published requisitions can't be deleted (the row is the only record of what
-- was pushed to the ATS, and the Notion tracker / Slack announcement / audit
-- log all key off its id). Without a retire path they accumulate in the list
-- forever, and the listing is capped at 100 rows — old published rows would
-- eventually push new ones off the page.
--
-- Archiving is the retire path: reversible, loses nothing, and hides the row
-- from the default listing. Applies to any status, not just published.
-- NULL = active.

ALTER TABLE job_requisitions
  ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

ALTER TABLE job_requisitions
  ADD COLUMN IF NOT EXISTS archived_by UUID REFERENCES users(id) ON DELETE SET NULL;

-- The default listing filters `archived_at IS NULL` and orders by created_at
-- desc; a partial index keeps that path off a full scan as archived rows pile
-- up.
CREATE INDEX IF NOT EXISTS idx_job_requisitions_active
  ON job_requisitions(org_id, created_at DESC)
  WHERE archived_at IS NULL;


-- ── Widen audit-log action CHECK for archive / unarchive ────────────────────
-- Same drop-and-recreate pattern as 070: the constraint name has drifted
-- across migrations, so find it by definition rather than by name.
DO $$
DECLARE
  con record;
BEGIN
  FOR con IN
    SELECT conname
    FROM pg_constraint
    WHERE conrelid = 'recruiting_audit_log'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%action%'
  LOOP
    EXECUTE format('ALTER TABLE recruiting_audit_log DROP CONSTRAINT %I', con.conname);
  END LOOP;
END $$;

ALTER TABLE recruiting_audit_log
  ADD CONSTRAINT recruiting_audit_log_action_check CHECK (
    action IN (
      'publish_attempt',
      'ats_publish',
      'notion_create',
      'slack_notify',
      'hiring_manager_email',
      'edit',
      'delete',
      'candidate_sync',
      'archive',
      'unarchive'
    )
  );
