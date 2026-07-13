-- ── Migration 084 — Remove action-item external task tracking ───────────────
--
-- The Action Items feature previously provisioned tasks in Notion/Asana/Linear/
-- Jira and ran a reminder cron that polled them for completion. That path has
-- been removed — extracted items now post to a Slack channel instead — so the
-- external-tracking columns, the reminder bookkeeping, and the 'tracked' /
-- 'overdue' statuses are dead weight. Drop them.
--
-- `_create_one` in action_tracker.py stays (the autoflow `create_task` action
-- reuses it), but it no longer writes to action_items.

-- 1. Fold any rows still parked in a removed status back to 'pending' so the
--    tightened CHECK below accepts them. 'overdue' collapses to 'pending' —
--    due_date still reflects lateness in the UI.
UPDATE action_items
   SET status = 'pending'
 WHERE status IN ('tracked', 'overdue');

-- 2. Tighten the status CHECK to the surviving set. The inline constraint from
--    053 has a generated name that isn't portable, so drop every CHECK on the
--    status column by lookup, then reinstall the canonical one.
DO $$
DECLARE cname TEXT;
BEGIN
  FOR cname IN
    SELECT conname FROM pg_constraint
     WHERE conrelid = 'action_items'::regclass
       AND contype = 'c'
       AND pg_get_constraintdef(oid) ILIKE '%status%'
  LOOP
    EXECUTE format('ALTER TABLE action_items DROP CONSTRAINT %I', cname);
  END LOOP;
END $$;

ALTER TABLE action_items
  ADD CONSTRAINT action_items_status_check
    CHECK (status IN ('pending', 'completed', 'cancelled'));

-- 3. Drop the external-tracking + reminder columns. Dropping external_provider
--    also removes its CHECK constraint.
ALTER TABLE action_items
  DROP COLUMN IF EXISTS external_provider,
  DROP COLUMN IF EXISTS external_id,
  DROP COLUMN IF EXISTS external_url,
  DROP COLUMN IF EXISTS last_reminded_at,
  DROP COLUMN IF EXISTS reminder_count;

-- 4. Recreate the owner/status partial index without the removed statuses.
--    'pending' is the only open state left.
DROP INDEX IF EXISTS idx_action_items_owner_status;
CREATE INDEX IF NOT EXISTS idx_action_items_owner_status
  ON action_items(owner_user_id, status)
  WHERE status = 'pending';
