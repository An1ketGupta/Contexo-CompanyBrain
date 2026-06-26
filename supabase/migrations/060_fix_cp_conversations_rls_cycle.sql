-- Migration 060 — Break conversations <-> conversation_participants RLS cycle
--
-- 059 fixed the self-recursion inside cp_select but kept a subquery against
-- `conversations`. That re-creates an inter-table cycle:
--
--   conversations_select (058) EXISTS-joins conversation_participants
--     → cp_select (059) EXISTS-joins conversations
--       → conversations_select fires again → 42P17.
--
-- All roster reads/writes go through the service-role client in
-- app/services/channels.py (RLS bypassed), so the "org-visibility channel:
-- any org member can see the roster" branch is unused by application code.
-- Strip it and the cycle dissolves. Your-own-row + admin escape hatch are
-- preserved.

DROP POLICY IF EXISTS cp_select ON conversation_participants;

CREATE POLICY cp_select ON conversation_participants
  FOR SELECT USING (
    org_id = auth_org_id()
    AND (
      user_id = auth.uid()
      OR EXISTS (SELECT 1 FROM users WHERE id = auth.uid() AND role = 'admin')
    )
  );
