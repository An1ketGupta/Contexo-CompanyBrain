-- ── 068: Naukri job-board integration + posting_destinations split ──────────
--
-- Why this migration exists
-- -------------------------
-- Naukri is the largest job portal in India. Unlike Greenhouse / Lever / Ashby
-- (which are ATSes — internal hiring systems), Naukri is a job board: a
-- publish-only sink that broadcasts an opening to candidates. The two are
-- distinct enough that mashing them under a single `ats_platform` enum is a
-- mild abstraction smell. We bias toward clarity now while there's still only
-- one job-board provider — adding the second one (Indeed, Hirist, Foundit)
-- becomes additive rather than a refactor.
--
-- Three structural changes:
--
-- 1. WIDEN the integrations.provider CHECK so a Naukri credential can be
--    persisted in the same unified `integrations` row shape every other
--    org-scoped provider uses (see migration 036).
--
-- 2. WIDEN job_requisitions.ats_platform-style enum to include 'naukri'. The
--    column is named ats_platform for back-compat with rows written before
--    this migration; new code reads ats_postings[].platform which is already
--    free-text JSONB and needs no schema change.
--
-- 3. ADD job_requisitions.naukri_search_urls — parallel to linkedin_search_urls
--    introduced in migration 053. Stores the 6 Resdex deep-link variants the
--    Naukri search-variant generator produces at publish time.
--
-- 4. ADD job_requisitions.naukri_taxonomy — Naukri requires three taxonomy
--    fields (functional_area, role_category, industry_type) that LinkedIn /
--    Greenhouse / Lever / Ashby don't model. We keep them per-requisition as
--    a small JSONB doc so adding more job-boards later doesn't sprawl into
--    one column per provider.
--
-- Idempotency
-- -----------
-- Every statement is wrapped to be re-runnable; `supabase db push` against a
-- partially-migrated database (e.g. dev re-applying after a rollback) won't
-- error on already-applied DDL.


-- ── 1. Allow 'naukri' as an integration provider ───────────────────────────
-- Mirrors migration 058's widening pattern: drop the existing constraint by
-- its canonical name, recreate with the new set. Listing every provider
-- explicitly (rather than ALTER COLUMN … ADD) keeps the source of truth
-- diffable in PR review.

ALTER TABLE integrations DROP CONSTRAINT IF EXISTS integrations_provider_check;
ALTER TABLE integrations
  ADD CONSTRAINT integrations_provider_check CHECK (
    provider IN (
      'onedrive', 'confluence', 'github', 'dropbox',
      'greenhouse', 'lever', 'ashby', 'naukri',
      'google_workspace',
      'asana', 'linear', 'jira'
    )
  );


-- ── 2. Allow 'naukri' in job_requisitions.ats_platform ──────────────────────
-- The column was created in migration 053 with an inline CHECK. We DROP
-- generously (the constraint name PG auto-generated varies across deploys
-- depending on the migration order) and re-ADD with the canonical name.

DO $$
DECLARE
  con record;
BEGIN
  FOR con IN
    SELECT conname
    FROM pg_constraint
    WHERE conrelid = 'job_requisitions'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%ats_platform%'
  LOOP
    EXECUTE format('ALTER TABLE job_requisitions DROP CONSTRAINT %I', con.conname);
  END LOOP;
END $$;

ALTER TABLE job_requisitions
  ADD CONSTRAINT job_requisitions_ats_platform_check CHECK (
    ats_platform IS NULL
    OR ats_platform IN ('greenhouse', 'lever', 'ashby', 'naukri')
  );


-- ── 3. Naukri Resdex search URLs (parallel to linkedin_search_urls) ─────────
-- JSONB array of {label, url, description, query_summary}. Empty until the
-- requisition is published.
ALTER TABLE job_requisitions
  ADD COLUMN IF NOT EXISTS naukri_search_urls JSONB NOT NULL DEFAULT '[]'::jsonb;


-- ── 4. Naukri taxonomy selections per requisition ───────────────────────────
-- Shape:
--   {
--     "functional_area":  {"id": "...", "name": "..."},
--     "role_category":    {"id": "...", "name": "..."},
--     "industry_type":    {"id": "...", "name": "..."},
--     "experience_min":   3,
--     "experience_max":   8,
--     "key_skills":       ["python", "fastapi"]
--   }
-- Populated by the publish form. NULLABLE so existing draft rows stay valid.
ALTER TABLE job_requisitions
  ADD COLUMN IF NOT EXISTS naukri_taxonomy JSONB;


-- ── 5. Optional index for Naukri-published requisitions ─────────────────────
-- Most tracker queries filter by org_id+status, but a recruiter may want to
-- list "all requisitions posted to Naukri last month" without a sequential
-- scan over ats_postings. A simple expression index covers it.
CREATE INDEX IF NOT EXISTS idx_job_requisitions_naukri_posted
  ON job_requisitions (org_id, created_at DESC)
  WHERE ats_postings::text ILIKE '%"platform":"naukri"%';


-- ── 6. Comment columns so psql \d carries the why-this-exists context ───────
COMMENT ON COLUMN job_requisitions.naukri_search_urls IS
  'Generated at publish time: Resdex deep-link variants for candidate sourcing.';
COMMENT ON COLUMN job_requisitions.naukri_taxonomy IS
  'Naukri-specific taxonomy choices (functional_area / role_category / industry_type / experience / key_skills). NULL when Naukri was not in ats_platforms.';
