-- ── 072: Standardise job_requisitions.status on lowercase ────────────────────
--
-- Migration 053 created the column with CHECK (status IN ('draft', 'Published',
-- 'failed')) — the only enum in the codebase with a capital letter. That
-- inconsistency leaked into Pydantic Literals, TS unions, and 25+ string
-- comparisons across api + web. A handful of rows ended up as lowercase
-- 'published' anyway (manual edits / earlier code paths), which the read
-- model then 500'd on because the Literal demanded the capitalised form.
--
-- Fold everything to lowercase 'published' to match the rest of the schema.
-- Display labels in the UI keep "Published" — that's a presentation choice,
-- not an enum value.

UPDATE job_requisitions
   SET status = 'published'
 WHERE status = 'Published';

ALTER TABLE job_requisitions
  DROP CONSTRAINT IF EXISTS job_requisitions_status_check;

ALTER TABLE job_requisitions
  ADD CONSTRAINT job_requisitions_status_check
  CHECK (status IN ('draft', 'published', 'failed'));
