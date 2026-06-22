-- ── Day 1 P0: tighten messages_select to per-user, not per-org ────────────────
--
-- Bug fixed:
--   The original messages_select policy (002_rls_policies.sql) filtered on
--   `org_id = auth_org_id()` only. A row was visible to every member of the
--   tenant, not just the conversation owner. With the chat UI showing one
--   user's conversations, this never surfaced via the product, but a member
--   could read teammates' chat history by hitting the Supabase API directly
--   (RLS is the only enforcement boundary — see CLAUDE.md principle #2).
--
-- Policy change:
--   `org_id` scoping stays as defence-in-depth, plus we add
--   `conversation_id IN (SELECT id FROM conversations WHERE user_id = auth.uid())`.
--   The conversations RLS already restricts that subquery to the caller, so
--   the SELECT collapses to "rows in conversations I own". No additional
--   table function or join needed.
--
-- Cross-user reads still required by the product (admin moderation,
-- agent-runs dashboards, scheduled report generation) all use the
-- service-role client, which bypasses RLS entirely — verified by grep
-- before writing this migration. The one user-scoped reader that DID
-- cross users (`/admin/feedback-stats` in routers/settings.py) is moved
-- to the service-role client in the same PR.

BEGIN;

DROP POLICY IF EXISTS "messages_select" ON messages;

CREATE POLICY "messages_select" ON messages
  FOR SELECT
  USING (
    org_id = auth_org_id()
    AND conversation_id IN (
      SELECT id FROM conversations WHERE user_id = auth.uid()
    )
  );

-- INSERT/UPDATE policies stay as-is: writes are gated by the routers
-- (which always verify conversation ownership before mutating), and the
-- service-role client handles legitimate cross-user writes
-- (Slack/Gmail/Inngest paths).

COMMIT;
