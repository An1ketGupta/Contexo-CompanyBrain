-- V4 Day 3 (#32) — Chrome Extension "Add to Brain" ingestion path.
--
-- Webpage / email documents arrive with pre-extracted text rather than a file
-- in Storage. We extend the existing documents table instead of introducing a
-- second table because everything downstream (chunks, embeddings, citations,
-- health score, tagging, RLS) already keys off documents.id — splitting would
-- mean teaching every read path about two source-of-truth tables.
--
-- Three changes:
--   1) Relax the file_type CHECK so 'webpage' (and future 'email') is valid.
--      Original constraint in 001 was already tightened against current code,
--      which accepts xlsx/pptx/html/csv via the router — re-declare with the
--      full allow-list to match the router's Literal type.
--   2) file_path is now nullable. Webpage docs have no Storage blob; the
--      original text content is what we ingest.
--   3) Add source_url + content columns. source_url is what we display +
--      dedupe against; content is the raw scraped text we feed the chunker.

ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_file_type_check;
ALTER TABLE documents ADD CONSTRAINT documents_file_type_check
  CHECK (file_type IN (
    'pdf', 'docx', 'txt', 'md',
    'xlsx', 'pptx', 'html', 'csv',
    'webpage', 'email'
  ));

ALTER TABLE documents ALTER COLUMN file_path DROP NOT NULL;

ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS source_url TEXT,
  ADD COLUMN IF NOT EXISTS content    TEXT;

-- Partial unique index: an org can only have one live row per source URL.
-- A user clicking "Add to Brain" twice on the same Notion doc collapses to
-- a single ingest run instead of stacking duplicate chunks in retrieval.
-- Partial on (source_url IS NOT NULL) so file-uploaded docs aren't affected.
CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_org_source_url
  ON documents (org_id, source_url)
  WHERE source_url IS NOT NULL;
