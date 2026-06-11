-- ── organizations.metadata ─────────────────────────────────────────────────
-- Migration 018 referenced `organizations.metadata->'onboarding'` in a comment
-- but never created the column, so reads and writes from the onboarding
-- service (`apps/api/app/services/onboarding.py`) fail with 42703 in prod.
-- Adding it now as a shared JSONB bucket — onboarding writes the `dismissed`
-- bit under the `onboarding` key, and future features can co-tenant under
-- their own keys (analytics_dismissed, intro_seen, etc.) without another
-- migration.

ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
