-- Migration 059 — Fix infinite recursion in conversation_participants RLS
--
-- The cp_select policy in migration 058 contains a self-referential subquery:
--
--   EXISTS (SELECT 1 FROM conversation_participants p2
--           WHERE p2.conversation_id = conversation_participants.conversation_id
--             AND p2.user_id = auth.uid())
--
-- When the user-scoped client queries `conversations`, the conversations_select
-- policy checks conversation_participants (to admit channel members). That
-- triggers cp_select, which then checks conversation_participants again
-- (the self-join) — causing PostgreSQL error 42P17 "infinite recursion
-- detected in policy for relation conversation_participants".
--
-- Fix: remove the self-referential subquery. The `user_id = auth.uid()` clause
-- already covers "you can see your own participant row." All roster fetches
-- (seeing other participants) go through the service-role client in
-- app/services/channels.py, which bypasses RLS — so this clause was never
-- load-bearing for application functionality.

DROP POLICY IF EXISTS cp_select ON conversation_participants;

CREATE POLICY cp_select ON conversation_participants
  FOR SELECT USING (
    org_id = auth_org_id()
    AND (
      -- Your own participant row
      user_id = auth.uid()
      -- Org-visibility channel: any org member can see the roster
      OR EXISTS (
        SELECT 1 FROM conversations c
        WHERE c.id = conversation_participants.conversation_id
          AND c.channel_visibility = 'org'
      )
      -- Admins see everything
      OR EXISTS (SELECT 1 FROM users WHERE id = auth.uid() AND role = 'admin')
    )
  );
