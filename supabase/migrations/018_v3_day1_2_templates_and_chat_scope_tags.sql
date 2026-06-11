-- ── V3 Day 1 + Day 2: prompt templates + tag-scoped chat + multi-doc RPC ───
-- Day 1 (Empty-state banner + onboarding checklist) is fully derived from
-- existing state — no schema changes needed. We compute onboarding step
-- completion from documents.count + messages.count per user, and persist only
-- the "dismissed" bit in organizations.metadata->'onboarding' (which the
-- backend writes via JSONB merge, no migration required).
--
-- Day 2 needs two things:
--   • a `prompt_templates` table for the template library (#20)
--   • a `scoped_tags TEXT[]` column on `conversations` so a thread can be
--     pinned to a tag set at creation, mirroring how `scoped_document_id`
--     already pins it to one doc (#19).

-- ── 1. Conversation tag scope ──────────────────────────────────────────────
-- Locked at creation, same semantics as scoped_document_id: the LLM's earlier
-- answers stay coherent with what was retrievable. Empty array = unscoped
-- (whole org). NOT NULL DEFAULT [] so callers never have to special-case nulls.
ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS scoped_tags TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[];

-- GIN for the array-overlap operator the retrieval step uses to resolve
-- a tag set to a document_id set. Conversations are small per org but this
-- index also doubles as a way to find "all chats scoped to X tag" if we ever
-- expose that filter in the UI.
CREATE INDEX IF NOT EXISTS idx_conversations_scoped_tags_gin
  ON conversations USING GIN (scoped_tags);

-- ── 2. Prompt templates ────────────────────────────────────────────────────
-- Three visibility tiers, fused at read time by a single SELECT with three
-- predicates OR-ed:
--   • builtin → org_id NULL, visible to every org (curated by us)
--   • shared  → org_id set, visible to every member of that org
--   • private → org_id set, is_shared=false, visible only to created_by
--
-- We keep the table denormalized rather than splitting builtin/shared/private
-- into three tables because the read path is one query and the cardinality is
-- tiny (a single org will have ≤ a few hundred templates in the real world).
CREATE TABLE IF NOT EXISTS prompt_templates (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  -- NULL for built-in / global templates. Set for org/user-owned rows.
  org_id          UUID REFERENCES organizations(id) ON DELETE CASCADE,
  -- NULL for built-ins (no human author). Set for user creations.
  created_by      UUID REFERENCES users(id) ON DELETE SET NULL,
  title           TEXT NOT NULL CHECK (char_length(title) BETWEEN 1 AND 120),
  description     TEXT CHECK (description IS NULL OR char_length(description) <= 280),
  template_text   TEXT NOT NULL CHECK (char_length(template_text) BETWEEN 1 AND 8000),
  category        TEXT NOT NULL DEFAULT 'Other'
    CHECK (category IN (
      'Email','Job Description','Announcement','Policy Q&A',
      'Meeting Prep','Customer Response','Slack Reply','Other'
    )),
  -- Org-wide visibility flag. Builtin rows are always treated as shared
  -- regardless of this value; we still store true for clarity.
  is_shared       BOOLEAN NOT NULL DEFAULT false,
  is_builtin      BOOLEAN NOT NULL DEFAULT false,
  use_count       INTEGER NOT NULL DEFAULT 0,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- Enforce the visibility rules at the DB level so a buggy backend can't
  -- mint an unowned non-builtin template. Either it's a builtin (org_id NULL,
  -- is_builtin true) or it has an owner (org_id + created_by NOT NULL).
  CONSTRAINT prompt_templates_ownership_chk CHECK (
    (is_builtin = true  AND org_id IS NULL)
    OR
    (is_builtin = false AND org_id IS NOT NULL AND created_by IS NOT NULL)
  )
);

CREATE INDEX IF NOT EXISTS idx_prompt_templates_org_category
  ON prompt_templates(org_id, category);
CREATE INDEX IF NOT EXISTS idx_prompt_templates_created_by
  ON prompt_templates(created_by);
CREATE INDEX IF NOT EXISTS idx_prompt_templates_builtin
  ON prompt_templates(is_builtin) WHERE is_builtin = true;
-- Sort key for the popover — most used first, ties broken by recency.
CREATE INDEX IF NOT EXISTS idx_prompt_templates_use_count
  ON prompt_templates(use_count DESC, created_at DESC);

-- updated_at maintenance — touch on every change so PATCH responses can be
-- treated as authoritative without re-reading.
CREATE OR REPLACE FUNCTION touch_prompt_templates_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prompt_templates_updated_at ON prompt_templates;
CREATE TRIGGER trg_prompt_templates_updated_at
  BEFORE UPDATE ON prompt_templates
  FOR EACH ROW
  EXECUTE FUNCTION touch_prompt_templates_updated_at();

-- ── 3. RLS — visibility (read) + write authority ───────────────────────────
ALTER TABLE prompt_templates ENABLE ROW LEVEL SECURITY;

-- Builtins are world-readable (org_id NULL); org-shared rows are readable
-- by any member; private rows only by their creator.
-- We rely on the existing `auth_org_id()` SECURITY DEFINER function
-- (migration 003) to resolve the caller's org without re-triggering RLS
-- recursion. `auth.uid()` is fine for created_by checks — it's a JWT
-- subject lookup, no table touch.

DROP POLICY IF EXISTS prompt_templates_select ON prompt_templates;
CREATE POLICY prompt_templates_select ON prompt_templates
  FOR SELECT
  USING (
    is_builtin = true
    OR (
      org_id = auth_org_id()
      AND (is_shared = true OR created_by = auth.uid())
    )
  );

-- Insert: caller must be writing into their own org as themselves, and cannot
-- mint a builtin (the migration is the only path that creates builtins).
DROP POLICY IF EXISTS prompt_templates_insert ON prompt_templates;
CREATE POLICY prompt_templates_insert ON prompt_templates
  FOR INSERT
  WITH CHECK (
    is_builtin = false
    AND org_id = auth_org_id()
    AND created_by = auth.uid()
  );

-- Update: creator can edit their own; admins can edit any shared template in
-- their org. Builtins are immutable from the API surface.
-- The role check uses a sub-SELECT against the user's own row (auth.uid() ==
-- self), which never recurses into prompt_templates' own policies, so no
-- SECURITY DEFINER wrapper needed.
DROP POLICY IF EXISTS prompt_templates_update ON prompt_templates;
CREATE POLICY prompt_templates_update ON prompt_templates
  FOR UPDATE
  USING (
    is_builtin = false
    AND org_id = auth_org_id()
    AND (
      created_by = auth.uid()
      OR (SELECT role FROM public.users WHERE id = auth.uid()) = 'admin'
    )
  )
  WITH CHECK (
    is_builtin = false
    AND org_id = auth_org_id()
  );

DROP POLICY IF EXISTS prompt_templates_delete ON prompt_templates;
CREATE POLICY prompt_templates_delete ON prompt_templates
  FOR DELETE
  USING (
    is_builtin = false
    AND org_id = auth_org_id()
    AND (
      created_by = auth.uid()
      OR (SELECT role FROM public.users WHERE id = auth.uid()) = 'admin'
    )
  );

-- ── 4. Seed built-in templates ─────────────────────────────────────────────
-- Curated set. Slack Reply earns its slot because the product already ships a
-- Slack integration (017); Customer Response is the second most common
-- "task-execution" prompt category after Email in B2B SaaS knowledge tools.
-- Idempotent: we INSERT … SELECT … WHERE NOT EXISTS keyed on (title,
-- is_builtin=true, org_id IS NULL) — builtins have no other natural key
-- and we want re-runs to no-op cleanly. Edits to existing builtin text
-- ship via a follow-up UPDATE in a new migration, never by replacement.
WITH seed(title, description, template_text, category) AS (VALUES
  ('Write a client email',
   'Professional email using your company voice',
   'Write a professional email to [person or role] about [topic]. Match our company voice. Reference our [relevant policy or document] when helpful. Keep the tone [formal / friendly] and end with a clear next step.',
   'Email'),
  ('Draft a job description',
   'Full JD grounded in your company culture',
   'Write a complete job description for a [seniority] [role title] at our company. Include: role summary, 6–8 key responsibilities, must-have qualifications, nice-to-have skills, and a short paragraph about our culture that draws from our existing team docs.',
   'Job Description'),
  ('Company-wide announcement',
   'Internal announcement of a change or update',
   'Draft a company-wide announcement about [topic / change]. Tone: [warm-formal / casual]. Cover: what is changing, why it matters, when it takes effect, and what employees should do next. Cite any policy doc the change references.',
   'Announcement'),
  ('Answer a policy question',
   'Concise policy answer with citations',
   'Answer this employee question about our policies: [question]. Be concise and direct. Use bullets if the answer has multiple parts. Cite the specific policy section the answer comes from.',
   'Policy Q&A'),
  ('Meeting prep brief',
   'Pre-meeting briefing under 300 words',
   'Prepare a briefing for an upcoming meeting about [topic]. Include: relevant background from our knowledge base, the 3–5 key points to land, likely tough questions, and any prior decisions that bear on this. Cap at 300 words.',
   'Meeting Prep'),
  ('Reply to a customer',
   'Thoughtful customer reply grounded in your docs',
   'Draft a reply to this customer message: [paste message]. Use our standard tone, address every question they raised, reference our product docs / policies where relevant, and end with a clear next step or invitation to follow up.',
   'Customer Response'),
  ('Slack reply',
   'Casual Slack reply in your team voice',
   'Write a Slack reply to: [paste thread or question]. Keep it casual, brief (2–4 sentences), use our team voice, and link to relevant docs when it makes the reply more useful than text alone.',
   'Slack Reply')
)
INSERT INTO prompt_templates (
  org_id, created_by, title, description, template_text, category,
  is_shared, is_builtin
)
SELECT NULL, NULL, s.title, s.description, s.template_text, s.category, true, true
FROM seed s
WHERE NOT EXISTS (
  SELECT 1 FROM prompt_templates pt
  WHERE pt.is_builtin = true AND pt.org_id IS NULL AND pt.title = s.title
);

-- ── 5. Extend search RPCs to accept a doc-id set ───────────────────────────
-- Tag-scoped chat resolves `tags → document_ids` on the Python side via the
-- existing GIN on `documents.tags`, then needs to pass that doc-id array
-- into the retrievers. Rather than add a per-tag branch inside the SQL (the
-- chunks table doesn't carry tags directly — they're on documents — so a
-- JOIN would be required), we extend the existing `match_document_id`
-- single-doc filter with a parallel `match_document_ids` array filter.
--
-- Why both? `match_document_id` is the document-pinning path (#100), already
-- used everywhere and tuned. The array path piggybacks on it without
-- disturbing existing call sites — both default to NULL meaning "no filter".
-- When both are supplied, BOTH apply (intersection); in practice the chat
-- router only ever sends one or the other.

DROP FUNCTION IF EXISTS vector_search(vector, uuid, int, uuid);

CREATE FUNCTION vector_search(
  query_embedding    vector(768),
  match_org_id       uuid,
  match_count        int    DEFAULT 10,
  match_document_id  uuid   DEFAULT NULL,
  match_document_ids uuid[] DEFAULT NULL
)
RETURNS TABLE (
  chunk_id        uuid,
  content         text,
  document_id     uuid,
  document_name   text,
  chunk_index     int,
  page_number     int,
  section_heading text,
  similarity      float
)
LANGUAGE plpgsql
VOLATILE
SECURITY INVOKER
SET search_path = public
AS $$
BEGIN
  SET LOCAL ivfflat.probes = 10;

  RETURN QUERY
  SELECT
    c.id                                              AS chunk_id,
    c.content                                         AS content,
    c.document_id                                     AS document_id,
    d.name                                            AS document_name,
    c.chunk_index                                     AS chunk_index,
    c.page_number                                     AS page_number,
    c.section_heading                                 AS section_heading,
    (1 - (e.embedding <=> query_embedding))::float    AS similarity
  FROM embeddings e
  JOIN chunks   c ON c.id = e.chunk_id
  JOIN documents d ON d.id = c.document_id
  WHERE e.org_id = match_org_id
    AND (match_document_id IS NULL OR c.document_id = match_document_id)
    AND (match_document_ids IS NULL OR c.document_id = ANY(match_document_ids))
  ORDER BY e.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;

GRANT EXECUTE ON FUNCTION vector_search(vector, uuid, int, uuid, uuid[]) TO authenticated;
GRANT EXECUTE ON FUNCTION vector_search(vector, uuid, int, uuid, uuid[]) TO service_role;

DROP FUNCTION IF EXISTS fts_search(text, uuid, int, uuid);

CREATE FUNCTION fts_search(
  query_text         text,
  match_org_id       uuid,
  match_count        int    DEFAULT 10,
  match_document_id  uuid   DEFAULT NULL,
  match_document_ids uuid[] DEFAULT NULL
)
RETURNS TABLE (
  chunk_id        uuid,
  content         text,
  document_id     uuid,
  document_name   text,
  chunk_index     int,
  page_number     int,
  section_heading text,
  similarity      float,
  snippet         text
)
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
  q tsquery := websearch_to_tsquery('english', query_text);
BEGIN
  RETURN QUERY
  SELECT
    c.id                                         AS chunk_id,
    c.content                                    AS content,
    c.document_id                                AS document_id,
    d.name                                       AS document_name,
    c.chunk_index                                AS chunk_index,
    c.page_number                                AS page_number,
    c.section_heading                            AS section_heading,
    ts_rank_cd(c.content_tsv, q)::float          AS similarity,
    ts_headline('english', c.content, q,
      'MaxFragments=2, MaxWords=20, MinWords=8, '
      'StartSel=<mark>, StopSel=</mark>')        AS snippet
  FROM chunks c
  JOIN documents d ON d.id = c.document_id
  WHERE c.org_id = match_org_id
    AND c.content_tsv @@ q
    AND (match_document_id IS NULL OR c.document_id = match_document_id)
    AND (match_document_ids IS NULL OR c.document_id = ANY(match_document_ids))
  ORDER BY similarity DESC
  LIMIT match_count;
END;
$$;

GRANT EXECUTE ON FUNCTION fts_search(text, uuid, int, uuid, uuid[]) TO authenticated;
GRANT EXECUTE ON FUNCTION fts_search(text, uuid, int, uuid, uuid[]) TO service_role;
