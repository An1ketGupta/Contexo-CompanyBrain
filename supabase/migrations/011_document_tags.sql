-- ── Document tags ────────────────────────────────────────────────────────────
-- Tags are user-owned labels for organising documents (e.g. "policy", "hr",
-- "Q4-2024"). Stored as a normalised TEXT[] so filtering by tag is a simple
-- array-contains expression and bulk add/remove is set arithmetic in SQL.
--
-- Normalisation rules (enforced at the API layer, not the DB):
--   * trimmed, lowercased, max 32 chars, no commas
--   * de-duplicated per document
-- We don't model a separate `tags` table — premature for our scale, and the
-- GIN index below makes containment queries cheap.

ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[];

-- GIN index on the array column → O(log n) containment lookups for the
-- `tags @> ARRAY['policy']` and `tags && ARRAY['a','b']` patterns the filter
-- endpoint uses. ARRAY operations without this index are a full sequential
-- scan over every document the org owns.
CREATE INDEX IF NOT EXISTS idx_documents_tags_gin
  ON documents USING GIN (tags);
