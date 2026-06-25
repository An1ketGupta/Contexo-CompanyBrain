-- ── Production Roadmap 1.7 — Pinned Context Templates ─────────────────────
--
-- Power users (sales reps, HR managers, ops leads) repeatedly start fresh
-- conversations with the same preamble: "I'm replying to enterprise clients,"
-- "I'm in the Singapore office," "I'm working on the Q3 roadmap." That string
-- already exists per-conversation as `conversations.pinned_context`
-- (migration 050) — this migration just lets a user save & re-apply it.
--
-- Why extend `prompt_templates` instead of a new table:
--   * Read path, CRUD surface, sharing semantics, and RLS policies are
--     identical to prompt templates. Splitting tables would mean copying
--     all of that and adding a UNION at the read site.
--   * The "context template" is a strict sub-shape: title + description +
--     pinned_context + visibility. The differences from a prompt template
--     are exactly which TEXT field carries the payload.
--
-- Two columns added:
--   * is_context_template   — discriminator. The template list UI filters
--     by this so the chat input's "Apply context" picker doesn't surface
--     full-prompt templates by mistake.
--   * pinned_context        — the actual preamble. Capped at 2000 chars
--     to match the conversations.pinned_context CHECK in migration 050;
--     applying a saved template can never produce an over-cap value.
--
-- The pre-existing ownership CHECK constraint
-- (prompt_templates_ownership_chk) is preserved as-is. The new fields are
-- orthogonal to ownership: a context template is still either builtin
-- (org_id NULL) or user-owned (org_id + created_by set).
--
-- A new CHECK constraint enforces the discriminator integrity:
--   * If is_context_template=true, pinned_context must be non-null.
--   * If is_context_template=false, pinned_context must be null.
-- That keeps the SELECT path simple — UI can render `pinned_context`
-- directly without re-checking is_context_template.

ALTER TABLE prompt_templates
  ADD COLUMN IF NOT EXISTS is_context_template BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE prompt_templates
  ADD COLUMN IF NOT EXISTS pinned_context TEXT
    CHECK (pinned_context IS NULL OR char_length(pinned_context) <= 2000);

-- Discriminator integrity. Idempotent: drop-if-exists before re-add so the
-- migration is safe to re-run on a partially-applied schema.
ALTER TABLE prompt_templates
  DROP CONSTRAINT IF EXISTS prompt_templates_context_payload_chk;
ALTER TABLE prompt_templates
  ADD CONSTRAINT prompt_templates_context_payload_chk CHECK (
    (is_context_template = false AND pinned_context IS NULL)
    OR
    (is_context_template = true  AND pinned_context IS NOT NULL
                                 AND char_length(pinned_context) BETWEEN 1 AND 2000)
  );

-- Picker sort key. Same shape as the existing idx_prompt_templates_use_count
-- but filtered on is_context_template — the chat input's "Apply context"
-- popover queries by visibility + this flag and sorts by most-used. A
-- partial index keeps it small (context templates are a fraction of
-- prompt_templates) and the planner picks it for the partial predicate.
CREATE INDEX IF NOT EXISTS idx_prompt_templates_context_use_count
  ON prompt_templates(use_count DESC, created_at DESC)
  WHERE is_context_template = true;

COMMENT ON COLUMN prompt_templates.is_context_template IS
  'When true, this row is a Pinned Context Template (preamble) rather than a full prompt template. Discriminated by prompt_templates_context_payload_chk.';
COMMENT ON COLUMN prompt_templates.pinned_context IS
  'For context templates only: the preamble copied into conversations.pinned_context when applied. Mutually exclusive with template_text by convention (UI never reads both).';
