-- ── 036: Unified integrations table for OneDrive/SharePoint, Confluence,
--        GitHub, and Dropbox (the post-Slack wave).
--
-- Existing per-provider tables (drive_integrations, notion_integrations,
-- gmail_integrations, slack_integrations) stay where they are — migrating
-- live OAuth installs sideways is unnecessary risk for a unification that
-- only future providers benefit from.
--
-- Two reasons for the unified shape:
--   1. Four new providers in one sprint × per-provider tables = 4 migrations,
--      4 routers' worth of SELECT/UPDATE plumbing. The JSONB metadata column
--      collapses that into one shape.
--   2. We finally have a column to hang webhook secrets, delta cursors, and
--      provider-specific install metadata off of without ALTERing a table
--      every time a new field shows up.

CREATE TABLE IF NOT EXISTS integrations (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  -- 'onedrive' covers OneDrive Business + SharePoint sites (same Graph token,
  -- separate selected-resources entries in metadata.resources). 'github'
  -- covers GitHub App installs. Future providers extend the CHECK.
  provider        TEXT NOT NULL CHECK (provider IN (
    'onedrive', 'confluence', 'github', 'dropbox'
  )),
  -- NULL = org-scoped (admin-installed, shared across all members).
  -- Non-null = user-scoped (per-user OAuth, only that user's row). All four
  -- v1 providers here are org-scoped; the column is here so future per-user
  -- additions (e.g. ChatGPT plugin) don't need another migration.
  scope_user_id   UUID REFERENCES users(id) ON DELETE CASCADE,
  connected_by    UUID REFERENCES users(id) ON DELETE SET NULL,
  -- OAuth/token material. Plaintext to stay consistent with the existing
  -- drive/notion/gmail/slack tables — Supabase column-level encryption (if
  -- pgsodium is enabled) covers at-rest; RLS covers reads. A follow-up sprint
  -- can swap to app-layer Fernet for all 8 provider tables in one shot.
  access_token    TEXT NOT NULL,
  refresh_token   TEXT,
  token_expiry    TIMESTAMPTZ,
  scopes          TEXT[] NOT NULL DEFAULT '{}',
  -- Provider-specific install info: tenant/site IDs (Microsoft), cloud_id
  -- (Atlassian), installation_id + app_slug (GitHub App), team_id +
  -- root_namespace_id (Dropbox), display name, account email, etc.
  metadata        JSONB NOT NULL DEFAULT '{}',
  -- User-picked resources to sync: SharePoint sites + drives, Confluence
  -- spaces, GitHub repos, Dropbox folders. Shape per provider is documented
  -- in the service module. Stored as JSONB so the UI can render without a
  -- third-party round trip.
  resources       JSONB NOT NULL DEFAULT '[]',
  -- Delta sync state. Microsoft Graph hands us a deltaLink URL; Confluence
  -- needs an ISO timestamp for CQL `lastmodified`; GitHub gives `since`;
  -- Dropbox gives a cursor string. Storing as a free-form string keeps the
  -- column polymorphic without a separate table per provider.
  sync_cursor     TEXT,
  last_synced_at  TIMESTAMPTZ,
  last_error      TEXT,
  last_error_at   TIMESTAMPTZ,
  -- Per-install webhook secret for HMAC verification when push subscriptions
  -- are wired up. Minted on first connect; rotates on disconnect+reconnect.
  webhook_secret  TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per (org, provider) for org-scoped installs; (org, provider, user)
-- for per-user. Partial unique indexes split the two cases so we don't have
-- to coalesce NULLs to a sentinel.
CREATE UNIQUE INDEX IF NOT EXISTS uq_integrations_org_provider
  ON integrations (org_id, provider)
  WHERE scope_user_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_integrations_org_provider_user
  ON integrations (org_id, provider, scope_user_id)
  WHERE scope_user_id IS NOT NULL;

-- The polling cron sweeps by (provider, last_synced_at). Index supports both.
CREATE INDEX IF NOT EXISTS idx_integrations_provider_synced
  ON integrations (provider, last_synced_at);

-- Keep updated_at honest.
CREATE OR REPLACE FUNCTION touch_integrations_updated_at()
  RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS integrations_touch_updated_at ON integrations;
CREATE TRIGGER integrations_touch_updated_at
  BEFORE UPDATE ON integrations
  FOR EACH ROW EXECUTE FUNCTION touch_integrations_updated_at();

-- ── RLS ─────────────────────────────────────────────────────────────────────
ALTER TABLE integrations ENABLE ROW LEVEL SECURITY;

-- Admins read all org integrations. The per-user case (scope_user_id = me)
-- is handled by the same policy via the OR — when we ship a user-scoped
-- provider, members will be able to read their own row only.
CREATE POLICY "integrations_select" ON integrations
  FOR SELECT USING (
    org_id = auth_org_id()
    AND (
      EXISTS (
        SELECT 1 FROM users
        WHERE users.id = auth.uid() AND users.role = 'admin'
      )
      OR scope_user_id = auth.uid()
    )
  );

-- All writes are admin-only for org-scoped rows (the v1 providers). User-
-- scoped writes restricted to the row owner.
CREATE POLICY "integrations_write_admin" ON integrations
  FOR ALL USING (
    org_id = auth_org_id()
    AND (
      (scope_user_id IS NULL AND EXISTS (
        SELECT 1 FROM users
        WHERE users.id = auth.uid() AND users.role = 'admin'
      ))
      OR scope_user_id = auth.uid()
    )
  );


-- ── Extend documents.source to include the four new providers ──────────────
-- Drop the old CHECK constraint (Postgres doesn't support ALTER CHECK in
-- place) and re-add with the extra provider values.
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_source_check;
ALTER TABLE documents
  ADD CONSTRAINT documents_source_check CHECK (
    source IN (
      'upload', 'google_drive', 'notion', 'email_forward', 'api',
      'slack', 'onedrive', 'sharepoint', 'confluence', 'github', 'dropbox'
    )
  );

-- The unique constraint on (org_id, source, external_id) from migration 016
-- still applies — same dedup story for the new providers.
