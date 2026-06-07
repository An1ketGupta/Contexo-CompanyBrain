-- Fix infinite RLS recursion on the `users` table.
--
-- auth_org_id() does `SELECT org_id FROM users WHERE id = auth.uid()`.
-- The `users_select` policy is `org_id = auth_org_id()`.
-- Reading users → fires users RLS → calls auth_org_id() → reads users → ...
-- Postgres aborts with "stack depth limit exceeded" (SQLSTATE 54001).
--
-- SECURITY DEFINER makes the function run as its owner (postgres in Supabase),
-- which has BYPASSRLS — so the inner SELECT on users skips policy evaluation.
-- SET search_path is the standard SECURITY DEFINER hardening to prevent
-- search-path-based privilege escalation.

CREATE OR REPLACE FUNCTION auth_org_id()
RETURNS UUID
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT org_id FROM public.users WHERE id = auth.uid();
$$;
