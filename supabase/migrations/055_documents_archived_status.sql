-- ── 055 — Widen documents.status to allow 'archived' (Feature 1.14)
--
-- The bulk-archive action on the admin knowledge-health page needs a status
-- value that:
--   • Is naturally excluded from every retrieval path (they all filter
--     status = 'ready').
--   • Survives a reversible "restore" — i.e. status='archived' rather than
--     a hard DELETE.
--
-- All other paths (vector_search, fts_search, document list, coverage,
-- health scoring) already gate on `status = 'ready'`, so this is a strict
-- widening — no read-path code changes required.

ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_status_check;
ALTER TABLE documents
  ADD CONSTRAINT documents_status_check CHECK (
    status IN ('pending', 'processing', 'ready', 'failed', 'archived')
  );

COMMENT ON CONSTRAINT documents_status_check ON documents IS
  '055 widens to include ''archived'' (Feature 1.14 bulk health remediation). Search paths filter to status=''ready'' so archived docs are excluded automatically.';
