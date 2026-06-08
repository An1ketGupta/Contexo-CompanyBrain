-- Enable Supabase Realtime broadcasts for the documents table so the
-- frontend can subscribe to status changes (pending → processing → ready/failed)
-- without polling.
--
-- The `supabase_realtime` publication is created by Supabase during project
-- init. Adding a table to it causes WAL changes on that table to be relayed
-- to subscribed clients. RLS still applies — clients only see rows their
-- session can SELECT.
--
-- Idempotent: re-running is safe because `ADD TABLE` errors only if the
-- table is already in the publication, which we swallow.

DO $$
BEGIN
  ALTER PUBLICATION supabase_realtime ADD TABLE documents;
EXCEPTION
  WHEN duplicate_object THEN
    -- Already in the publication; nothing to do.
    NULL;
END $$;
