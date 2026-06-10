-- ── Day 14: Drive (#86) + Notion (#87) + Email Forward-to-Brain (#83) ───────
-- Three integration paths share three patterns:
--   1. Persisted OAuth/auth state per org (encrypted-at-rest in the column).
--   2. A polling marker (last_synced_at) so the cron picks up only deltas.
--   3. A documents.source column so admins can see *how* a doc arrived.

-- ── 1. documents.source — provenance tag ───────────────────────────────────
-- Existing rows default to 'upload' (the only path we had pre-Day-14).
-- 'google_drive' / 'notion' / 'email_forward' / 'api' are added by the
-- respective ingestion paths; future integrations extend the CHECK without
-- backfilling.
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'upload'
    CHECK (source IN ('upload', 'google_drive', 'notion', 'email_forward', 'api'));

-- Lets the integration polling jobs filter out their own previously-ingested
-- pages cheaply (rather than scanning every doc).
CREATE INDEX IF NOT EXISTS idx_documents_org_source
  ON documents (org_id, source);

-- external_id: stable identifier from the source system (Drive file id, Notion
-- page id, inbound message-id). NULL for normal uploads. Used as a dedupe key
-- so re-polling the same Notion page upserts rather than duplicating.
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS external_id TEXT;

-- Unique per-org per-source so the same Notion page id can exist in two
-- separate orgs (multi-tenant correctness) but not twice in the same org.
CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_external
  ON documents (org_id, source, external_id)
  WHERE external_id IS NOT NULL;


-- ── 2. organizations.inbound_email (#83) ───────────────────────────────────
-- Each org gets a unique address. Slug derived from organizations.slug to
-- keep it readable ("brain-acme-corp@inbound.companybrain.app") but with a
-- random suffix appended at creation time to make it un-guessable so a
-- malicious actor can't blast forwarded emails into another org's brain.
ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS inbound_email TEXT UNIQUE;


-- ── 3. drive_integrations (#86) ────────────────────────────────────────────
-- One row per (org, connecting user). The access_token expires regularly;
-- we always use refresh_token to mint a fresh one and persist it back so
-- the next sync runs without a refresh round-trip.
CREATE TABLE IF NOT EXISTS drive_integrations (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL UNIQUE REFERENCES organizations(id) ON DELETE CASCADE,
  connected_by    UUID REFERENCES users(id) ON DELETE SET NULL,
  -- Both tokens are encrypted at rest at the Supabase column level if pgsodium
  -- is enabled; otherwise plaintext but never returned over the API surface.
  access_token    TEXT NOT NULL,
  refresh_token   TEXT NOT NULL,
  token_expiry    TIMESTAMPTZ,
  -- Drive folder IDs the cron should scan on every poll. Empty array = whole
  -- "My Drive" (we discourage this in the UI — too noisy).
  folder_ids      TEXT[] NOT NULL DEFAULT '{}',
  last_synced_at  TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE drive_integrations ENABLE ROW LEVEL SECURITY;

-- Admins-only on read so token material doesn't leak to org members.
-- The role check rides on a subquery against users — common pattern in
-- this codebase (see 002_rls_policies.sql for the auth_org_id helper).
CREATE POLICY "drive_integrations_select_admin" ON drive_integrations
  FOR SELECT USING (
    org_id = auth_org_id()
    AND EXISTS (
      SELECT 1 FROM users
      WHERE users.id = auth.uid() AND users.role = 'admin'
    )
  );


-- ── 4. notion_integrations (#87) ────────────────────────────────────────────
-- Notion OAuth returns a non-expiring bot token per workspace install. Page
-- ids we sync go into a JSONB array of {id, title} so the UI can render the
-- list without a separate fetch.
CREATE TABLE IF NOT EXISTS notion_integrations (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL UNIQUE REFERENCES organizations(id) ON DELETE CASCADE,
  connected_by      UUID REFERENCES users(id) ON DELETE SET NULL,
  access_token      TEXT NOT NULL,
  workspace_id      TEXT NOT NULL,
  workspace_name    TEXT,
  bot_id            TEXT,
  -- [{id, title}] — denormalised so the UI can list selected pages without
  -- a round trip to the Notion API.
  selected_pages    JSONB NOT NULL DEFAULT '[]',
  last_synced_at    TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE notion_integrations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "notion_integrations_select_admin" ON notion_integrations
  FOR SELECT USING (
    org_id = auth_org_id()
    AND EXISTS (
      SELECT 1 FROM users
      WHERE users.id = auth.uid() AND users.role = 'admin'
    )
  );
