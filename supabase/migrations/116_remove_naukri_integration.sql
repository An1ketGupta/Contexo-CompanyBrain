-- Remove the retired Naukri posting destination from existing databases.

DELETE FROM integrations WHERE provider = 'naukri';

UPDATE job_requisitions
SET
  ats_platform = NULL,
  ats_job_id = NULL,
  ats_url = NULL
WHERE ats_platform = 'naukri';

UPDATE job_requisitions
SET ats_postings = COALESCE(
  (
    SELECT jsonb_agg(posting)
    FROM jsonb_array_elements(COALESCE(ats_postings, '[]'::jsonb)) AS posting
    WHERE posting->>'platform' <> 'naukri'
  ),
  '[]'::jsonb
);

DROP INDEX IF EXISTS idx_job_requisitions_naukri_posted;

ALTER TABLE job_requisitions
  DROP COLUMN IF EXISTS naukri_search_urls,
  DROP COLUMN IF EXISTS naukri_taxonomy;

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
    OR ats_platform IN ('greenhouse', 'lever', 'ashby')
  );
