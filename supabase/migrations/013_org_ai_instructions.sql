-- ── Custom org AI instructions (Day 9 / #67) ────────────────────────────────
-- Admin-only org-wide prompt context prepended to the LLM system instruction
-- on every chat turn. Free-form text; cap enforced at the API layer (500 chars)
-- so we can change the cap without a migration.
--
-- Storage choice: a single nullable column instead of a JSONB blob. We have
-- exactly one knob today and adding a second column later is cheaper than
-- inventing a schema for a one-field object.

ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS ai_instructions TEXT;
