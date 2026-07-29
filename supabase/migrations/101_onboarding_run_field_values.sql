-- ── 101: HR-entered field values on an onboarding run ─────────────────────
--
-- A run blocks when a required template field has no value anywhere in the
-- candidate profile — "Company Address is required but has no value." Until
-- now the only fix was to go and populate the system of record the field maps
-- to (the org's registered address, the run's reporting manager), which for a
-- one-off field is a detour through three screens to type one line.
--
-- This column is the direct answer: values HR typed for this run, keyed by
-- `doc_template_variables.internal_name`. The mapping engine applies them as
-- its highest-precedence layer, above configured mappings and above the
-- profile, on exactly the same principle as `resolve_profile(overrides=...)` —
-- a human correcting a value is the most authoritative source there is.
--
-- Keyed by internal_name, not by dotted profile path, because the blocking
-- error names a variable and this is what HR is answering. A field with no
-- mapping at all (`notice_period`, which nothing in the profile can supply)
-- is therefore fillable too, and that is the common case.
--
-- Scoped to the run, so a value typed for one candidate never leaks into
-- another's contract. An org-wide fact belongs in `organizations` or in the
-- variable's `default_value`; this is not a shortcut around either.

ALTER TABLE onboarding_runs
  ADD COLUMN IF NOT EXISTS field_values JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN onboarding_runs.field_values IS
  'HR-entered template field values for this run, keyed by '
  'doc_template_variables.internal_name. Highest-precedence source in the '
  'mapping engine. Blank values are deleted rather than stored, so a key '
  'being present means a human put it there.';
