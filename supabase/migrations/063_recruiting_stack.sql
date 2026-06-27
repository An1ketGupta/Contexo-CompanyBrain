-- Recruiting: per-requisition tech stack field.
--
-- Stored separately from context_notes so it can be surfaced in the KB
-- facet queries directly (e.g. "React Node.js PostgreSQL") without being
-- buried inside free-form notes. Also shown as a dedicated badge in the UI.
ALTER TABLE job_requisitions
  ADD COLUMN IF NOT EXISTS stack TEXT;
