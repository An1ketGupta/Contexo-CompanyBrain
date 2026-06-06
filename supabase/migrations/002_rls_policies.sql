-- ── Row Level Security ────────────────────────────────────────
-- Enable RLS on every table. All policies are org-scoped via users.org_id.

ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE users        ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents    ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks       ENABLE ROW LEVEL SECURITY;
ALTER TABLE embeddings   ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages     ENABLE ROW LEVEL SECURITY;

-- Helper: returns the org_id for the currently authenticated user
CREATE OR REPLACE FUNCTION auth_org_id()
RETURNS UUID
LANGUAGE sql STABLE
AS $$
  SELECT org_id FROM users WHERE id = auth.uid();
$$;

-- ── Organizations ─────────────────────────────────────────────
-- Users can only see/edit their own org
CREATE POLICY "org_select" ON organizations
  FOR SELECT USING (id = auth_org_id());

CREATE POLICY "org_update" ON organizations
  FOR UPDATE USING (id = auth_org_id());

-- ── Users ─────────────────────────────────────────────────────
CREATE POLICY "users_select" ON users
  FOR SELECT USING (org_id = auth_org_id());

-- Only admin can insert/update other users
CREATE POLICY "users_insert" ON users
  FOR INSERT WITH CHECK (
    org_id = auth_org_id()
    AND (SELECT role FROM users WHERE id = auth.uid()) = 'admin'
  );

CREATE POLICY "users_update_self" ON users
  FOR UPDATE USING (id = auth.uid());

-- ── Documents ─────────────────────────────────────────────────
CREATE POLICY "documents_select" ON documents
  FOR SELECT USING (org_id = auth_org_id());

CREATE POLICY "documents_insert" ON documents
  FOR INSERT WITH CHECK (org_id = auth_org_id());

CREATE POLICY "documents_update" ON documents
  FOR UPDATE USING (org_id = auth_org_id());

CREATE POLICY "documents_delete" ON documents
  FOR DELETE USING (org_id = auth_org_id());

-- ── Chunks ────────────────────────────────────────────────────
CREATE POLICY "chunks_select" ON chunks
  FOR SELECT USING (org_id = auth_org_id());

-- Chunks are only written by the service role (Inngest background jobs)
-- No INSERT/UPDATE/DELETE policies for regular users

-- ── Embeddings ────────────────────────────────────────────────
CREATE POLICY "embeddings_select" ON embeddings
  FOR SELECT USING (org_id = auth_org_id());

-- Embeddings are only written by the service role

-- ── Conversations ─────────────────────────────────────────────
CREATE POLICY "conversations_select" ON conversations
  FOR SELECT USING (org_id = auth_org_id() AND user_id = auth.uid());

CREATE POLICY "conversations_insert" ON conversations
  FOR INSERT WITH CHECK (org_id = auth_org_id() AND user_id = auth.uid());

CREATE POLICY "conversations_update" ON conversations
  FOR UPDATE USING (org_id = auth_org_id() AND user_id = auth.uid());

CREATE POLICY "conversations_delete" ON conversations
  FOR DELETE USING (org_id = auth_org_id() AND user_id = auth.uid());

-- ── Messages ──────────────────────────────────────────────────
CREATE POLICY "messages_select" ON messages
  FOR SELECT USING (org_id = auth_org_id());

CREATE POLICY "messages_insert" ON messages
  FOR INSERT WITH CHECK (org_id = auth_org_id());

CREATE POLICY "messages_update" ON messages
  FOR UPDATE USING (org_id = auth_org_id());
