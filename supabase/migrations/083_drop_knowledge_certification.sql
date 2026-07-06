-- ── Migration 083 — Remove Knowledge Certification (2.10) ───────────────────
--
-- The Knowledge Certification feature (LLM-generated quizzes gating policy
-- acknowledgement) has been removed. Drops the tables and column added in
-- migration 058 section 4. Plain policy acknowledgement (compliance.py,
-- `acknowledgements` table) is unaffected.

DROP TABLE IF EXISTS certification_attempts;
DROP TABLE IF EXISTS knowledge_quizzes;

ALTER TABLE documents
  DROP COLUMN IF EXISTS requires_certification;
