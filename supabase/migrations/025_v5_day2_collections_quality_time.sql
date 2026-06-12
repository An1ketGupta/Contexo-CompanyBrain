-- ── V5 Day 2 — Collections, output-quality signals, time savings, org enrichment

-- ── #35  Collections ────────────────────────────────────────────────────────
-- Tag-based saved views over the documents library. A collection has no FK
-- onto `documents` — instead it stores the list of tags that defines it, and
-- the chat surface translates a collection_id into `scoped_tags` at lookup
-- time. This means: deleting a collection cannot orphan documents, renaming
-- a tag globally cascades (collection picks up the new tag set on next read),
-- and there's no junction table to keep in sync on every doc upload.

CREATE TABLE IF NOT EXISTS collections (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name          TEXT NOT NULL,
  description   TEXT,
  -- Tags that make up the collection. A document is "in" the collection if
  -- ANY of its tags overlap with this list (Postgres `&&` operator). Lower-
  -- cased on write so the comparison matches documents.tags storage.
  tag_filters   TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  -- Cosmetics for the chip / settings row. Hex string (#rrggbb) — we don't
  -- enforce a regex in SQL because the frontend's picker already restricts it.
  color         TEXT NOT NULL DEFAULT '#6366f1',
  icon          TEXT,
  created_by    UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- Names should be unique within an org so the chip row doesn't render two
  -- "HR" pills. Names are case-insensitive: "HR" and "hr" collide.
  CONSTRAINT collections_unique_name_per_org UNIQUE (org_id, name)
);

CREATE INDEX IF NOT EXISTS idx_collections_org ON collections(org_id);

ALTER TABLE collections ENABLE ROW LEVEL SECURITY;

-- Member read; admin write. Mirrors the templates / webhook policy pattern.
DROP POLICY IF EXISTS collections_select ON collections;
CREATE POLICY collections_select ON collections
  FOR SELECT USING (org_id = auth_org_id());

DROP POLICY IF EXISTS collections_insert ON collections;
CREATE POLICY collections_insert ON collections
  FOR INSERT WITH CHECK (
    org_id = auth_org_id()
    AND (SELECT role FROM users WHERE id = auth.uid()) = 'admin'
  );

DROP POLICY IF EXISTS collections_update ON collections;
CREATE POLICY collections_update ON collections
  FOR UPDATE USING (
    org_id = auth_org_id()
    AND (SELECT role FROM users WHERE id = auth.uid()) = 'admin'
  );

DROP POLICY IF EXISTS collections_delete ON collections;
CREATE POLICY collections_delete ON collections
  FOR DELETE USING (
    org_id = auth_org_id()
    AND (SELECT role FROM users WHERE id = auth.uid()) = 'admin'
  );

-- Optional pointer from a conversation to its source collection, so the chat
-- UI can render "Pinned to <Collection>" instead of a generic tag pill list.
-- The tag snapshot lives in conversations.scoped_tags (already exists); this
-- column is just for display. SET NULL on delete: a deleted collection
-- shouldn't break the conversation — it falls back to showing the tags.
ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS scoped_collection_id UUID
    REFERENCES collections(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_conversations_scoped_collection
  ON conversations(scoped_collection_id)
  WHERE scoped_collection_id IS NOT NULL;


-- ── #59  Output quality signal — copy = used ───────────────────────────────
-- We don't add a new prompts/buttons; the existing copy button on every
-- assistant message becomes the signal. Each click increments copy_count and
-- forwards a "output_used" score onto the Langfuse trace that produced the
-- answer, so quality slicing can join click-data to retrieval traces.
ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS copy_count       INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS first_copied_at  TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS last_copied_at   TIMESTAMPTZ;

-- Lookups for "top copied" and "copy rate by intent" are most-recent-first
-- and bounded by date — a partial index keeps the cost off non-copied rows.
CREATE INDEX IF NOT EXISTS idx_messages_copy_count
  ON messages(org_id, last_copied_at DESC)
  WHERE copy_count > 0;


-- ── #73  Time savings — minutes per query ──────────────────────────────────
-- Stored on the assistant message because (a) we already write the assistant
-- row at the end of every turn and (b) intent + content-length are exactly
-- what we need to derive the estimate. No separate query_logs table — this
-- repo never created one and a new table is overkill for one column.
ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS time_saved_minutes INTEGER NOT NULL DEFAULT 0;

-- Per-org rollup queries SUM where role='assistant' AND time_saved_minutes>0,
-- typically filtered by created_at window. Partial index limits the scan to
-- the rows that actually contribute to the sum.
CREATE INDEX IF NOT EXISTS idx_messages_time_saved
  ON messages(org_id, created_at DESC)
  WHERE time_saved_minutes > 0;


-- ── #97  Organization metadata enrichment ──────────────────────────────────
-- Captured once via a 3-step modal on first admin login. Used to personalize
-- system_extra, suggest relevant templates, and seed AI instructions if the
-- org hasn't customized them yet.
ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS industry                 TEXT,
  ADD COLUMN IF NOT EXISTS company_size             TEXT,
  -- One-of: 'hr_policies', 'sales_enablement', 'customer_support',
  -- 'engineering', 'general'. Not enforced in SQL because the set is allowed
  -- to grow without a migration.
  ADD COLUMN IF NOT EXISTS primary_use_case         TEXT,
  ADD COLUMN IF NOT EXISTS onboarding_completed_at  TIMESTAMPTZ;
