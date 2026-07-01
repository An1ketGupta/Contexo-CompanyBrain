-- ── Day 15: Developer API (#47) + Slack Bot (#31) ──────────────────────────
-- Both are platform features that turn Nirnaya IQ into something other
-- systems consume. Slack is the highest-leverage distribution channel; the
-- public API turns the product into an API-first platform other tools
-- can plug into.

-- ── 1. api_keys (#47) ──────────────────────────────────────────────────────
-- Auth model: SHA-256(full_key) is the database identifier so verification
-- is a single indexed lookup. The plaintext key is shown ONCE at creation
-- and never persisted. The 12-char `key_prefix` lets us show "cb_live_xxxx…"
-- in the dashboard list view so admins can identify keys without storing
-- anything sensitive.
CREATE TABLE IF NOT EXISTS api_keys (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  created_by    UUID REFERENCES users(id) ON DELETE SET NULL,
  name          TEXT NOT NULL,
  -- SHA-256 hex digest of the full key. 64 chars. We hash with SHA-256
  -- (not bcrypt) because the underlying token already has 256 bits of
  -- entropy (32 bytes from `secrets.token_urlsafe`) — bcrypt's slowness
  -- against brute force buys nothing when the search space is 2^256, and
  -- bcrypt would force us to scan rows on auth instead of indexed lookup.
  key_hash      TEXT NOT NULL UNIQUE,
  -- First 12 chars of the plaintext key, e.g. "cb_live_AbCd". Safe to show.
  key_prefix    TEXT NOT NULL,
  -- 'org' grants the key org-wide access (acts on behalf of the org's
  -- service account). 'user' is scoped to a single user — useful for
  -- per-developer keys that should disappear when the user leaves the org.
  scope         TEXT NOT NULL DEFAULT 'org' CHECK (scope IN ('org', 'user')),
  last_used_at  TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at    TIMESTAMPTZ
);

-- Lookup by hash on every API call — covering index keeps the auth path tight.
CREATE INDEX IF NOT EXISTS idx_api_keys_hash_live
  ON api_keys (key_hash)
  WHERE revoked_at IS NULL;

-- List view: most recent first, scoped by org.
CREATE INDEX IF NOT EXISTS idx_api_keys_org_created
  ON api_keys (org_id, created_at DESC);

ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;

CREATE POLICY "api_keys_select" ON api_keys
  FOR SELECT USING (org_id = auth_org_id());


-- ── 2. slack_integrations (#31) ────────────────────────────────────────────
-- Slack OAuth maps one Slack team -> one of our orgs. We enforce 1:1 so a
-- single Slack workspace can't fan answers across two of our customers.
CREATE TABLE IF NOT EXISTS slack_integrations (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL UNIQUE REFERENCES organizations(id) ON DELETE CASCADE,
  slack_team_id      TEXT NOT NULL UNIQUE,
  slack_team_name    TEXT,
  bot_token          TEXT NOT NULL,         -- xoxb-… — never returned over the API
  bot_user_id        TEXT,
  installed_by       UUID REFERENCES users(id) ON DELETE SET NULL,
  installed_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_slack_integrations_team
  ON slack_integrations (slack_team_id);

ALTER TABLE slack_integrations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "slack_integrations_select_admin" ON slack_integrations
  FOR SELECT USING (
    org_id = auth_org_id()
    AND EXISTS (
      SELECT 1 FROM users
      WHERE users.id = auth.uid() AND users.role = 'admin'
    )
  );
