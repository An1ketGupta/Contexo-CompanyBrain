-- ── 102: Finish repointing onboarding_documents.source_template_id ────────
--
-- Migration 100 declared that this column now carries a `doc_templates` id and
-- is "deliberately unconstrained" — but it only restated that in a COMMENT. The
-- foreign key from 071 (`REFERENCES documents(id)`) was never dropped, so every
-- document the new generation pipeline produced failed to record:
--
--   insert or update on table "onboarding_documents" violates foreign key
--   constraint "onboarding_documents_source_template_id_fkey"
--   Key (source_template_id)=(...) is not present in table "documents".
--
-- The failure landed after the run had already flipped to `*_generating` and
-- after `generated_documents` had recorded a successful render, so runs stuck
-- on "Preparing LOIfrom template" forever while the renderer happily produced
-- the same PDF on every Inngest retry. All three generation steps (LOI,
-- appointment letter + NDA, induction) wrote this column, so all three stalled.
--
-- Dropping rather than repointing at doc_templates(id) is the intent recorded
-- in 100: the artifact must stay readable after its template is archived or
-- superseded, and the authoritative versioned record of a generation lives in
-- `generated_documents`, which does carry real FKs.

ALTER TABLE onboarding_documents
  DROP CONSTRAINT IF EXISTS onboarding_documents_source_template_id_fkey;
