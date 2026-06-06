-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- ── Organizations (tenants) ───────────────────────────────────
CREATE TABLE organizations (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name       TEXT NOT NULL,
  slug       TEXT UNIQUE NOT NULL,
  plan       TEXT NOT NULL DEFAULT 'free' CHECK (plan IN ('free', 'starter', 'growth', 'business')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Users ─────────────────────────────────────────────────────
-- id matches auth.users(id) — Supabase Auth owns the password/email
CREATE TABLE users (
  id           UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  org_id       UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  role         TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
  display_name TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Documents ─────────────────────────────────────────────────
CREATE TABLE documents (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name             TEXT NOT NULL,
  file_path        TEXT NOT NULL,
  file_type        TEXT NOT NULL CHECK (file_type IN ('pdf', 'docx', 'txt', 'md')),
  file_size_bytes  INTEGER,
  status           TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'ready', 'failed')),
  chunk_count      INTEGER,
  metadata         JSONB NOT NULL DEFAULT '{}',
  created_by       UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Chunks ────────────────────────────────────────────────────
CREATE TABLE chunks (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  content         TEXT NOT NULL,
  -- Auto-generated tsvector for FTS — never write to this column
  content_tsv     TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
  chunk_index     INTEGER NOT NULL,
  page_number     INTEGER,
  section_heading TEXT,
  token_count     INTEGER,
  metadata        JSONB NOT NULL DEFAULT '{}'
);

-- ── Embeddings ────────────────────────────────────────────────
-- Separate table so we can swap embedding dimensions without touching chunks
CREATE TABLE embeddings (
  id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  chunk_id  UUID NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  org_id    UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  -- 768 dims for Google text-embedding-004; alter to 1536 when switching to OpenAI
  embedding vector(768) NOT NULL
);

-- ── Conversations ─────────────────────────────────────────────
CREATE TABLE conversations (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title      TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Messages ──────────────────────────────────────────────────
CREATE TABLE messages (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  content         TEXT NOT NULL,
  -- Array of {chunk_id, doc_name, page_number, excerpt}
  sources         JSONB,
  feedback        TEXT CHECK (feedback IN ('positive', 'negative')),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Indexes ───────────────────────────────────────────────────
-- Full-text search
CREATE INDEX idx_chunks_content_tsv ON chunks USING GIN (content_tsv);

-- Vector similarity search (IVFFlat — good up to ~1M vectors)
-- lists=100 is a reasonable default; increase as corpus grows
CREATE INDEX idx_embeddings_vector ON embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Multi-tenant queries
CREATE INDEX idx_embeddings_org_id ON embeddings (org_id);
CREATE INDEX idx_documents_org_status ON documents (org_id, status);
CREATE INDEX idx_documents_org_created ON documents (org_id, created_at DESC);
CREATE INDEX idx_chunks_document_id ON chunks (document_id);
CREATE INDEX idx_chunks_org_id ON chunks (org_id);
CREATE INDEX idx_messages_conversation ON messages (conversation_id, created_at);
CREATE INDEX idx_conversations_user ON conversations (user_id, updated_at DESC);
