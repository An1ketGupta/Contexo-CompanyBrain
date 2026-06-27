-- ── 062 — Recruiting Agent production hardening.
--
-- Three concerns:
--   1. integrations.mapping_cache             cached ATS offices/depts/teams
--   2. idempotency_keys                       Idempotency-Key support for
--                                             ATS publish (24h TTL)
--   3. recruiting_audit_log                   append-only audit trail of every
--                                             ATS / Notion / Slack / email
--                                             side-effect run during publish
--
-- Why each:
--   • mapping_cache lives on the unified `integrations` row keyed by
--     (org_id, provider). Refreshed via POST /integrations/ats/{p}/refresh-mapping
--     or implicitly on the first publish after connect. Avoids per-publish
--     ATS round-trip on hot path.
--   • idempotency_keys prevents double-creating a Greenhouse job when the
--     publish call retries from the browser or a flaky reverse proxy. A small
--     dedicated table is preferred over leaning on PG advisory locks because
--     we want to *return the cached response*, not just dedupe the write.
--   • recruiting_audit_log is admin-readable and survives a requisition delete
--     (no FK cascade) so compliance can prove what we did to which ATS.


-- ── 0. job_requisitions remembers location + department ─────────────────────
-- The Generate form already collects these; we just weren't persisting them.
-- The publish flow needs them to resolve_mapping() against the ATS taxonomy.
ALTER TABLE job_requisitions
  ADD COLUMN IF NOT EXISTS location TEXT,
  ADD COLUMN IF NOT EXISTS department TEXT;


-- ── 1. Cached ATS taxonomy on the integrations row ───────────────────────────
ALTER TABLE integrations
  ADD COLUMN IF NOT EXISTS mapping_cache JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS mapping_cached_at TIMESTAMPTZ;

COMMENT ON COLUMN integrations.mapping_cache IS
  'Provider-specific ATS taxonomy snapshot: {offices:[{id,name,location}], departments:[{id,name}], locations:[{id,name}], teams:[{id,name}], job_templates:[{id,name}]}. Refreshed on connect and via POST /integrations/ats/{provider}/refresh-mapping.';


-- ── 2. Idempotency keys ──────────────────────────────────────────────────────
-- Cleanup: a small EXPIRE-like sweep via cron deletes rows older than 24h.
-- We keep the row 24h to absorb retries from upstream proxies; longer would
-- only matter for fault-tolerant clients that we don't have.
CREATE TABLE IF NOT EXISTS idempotency_keys (
  org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  key             TEXT NOT NULL,
  endpoint        TEXT NOT NULL,                       -- e.g. 'recruiting.publish'
  request_hash    TEXT NOT NULL,                       -- sha256 of canonical request
  response_status INTEGER,                              -- HTTP status from the cached response
  response_body   JSONB,                                -- cached response body
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at      TIMESTAMPTZ NOT NULL DEFAULT (now() + INTERVAL '24 hours'),
  PRIMARY KEY (org_id, key)
);

CREATE INDEX IF NOT EXISTS idx_idempotency_keys_expires
  ON idempotency_keys(expires_at);

ALTER TABLE idempotency_keys ENABLE ROW LEVEL SECURITY;

-- The table is service-role only; no user reads. Locking down explicitly so
-- nobody accidentally exposes a different key for the same org.
DROP POLICY IF EXISTS "idempotency_keys_none" ON idempotency_keys;
CREATE POLICY "idempotency_keys_none" ON idempotency_keys
  FOR ALL USING (false);


-- ── 3. Recruiting audit log ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS recruiting_audit_log (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  requisition_id  UUID,                                 -- soft FK; survives requisition delete
  actor_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
  action          TEXT NOT NULL
    CHECK (action IN (
      'publish_attempt',
      'ats_publish',
      'notion_create',
      'slack_notify',
      'hiring_manager_email',
      'edit',
      'delete'
    )),
  ats_platform    TEXT,
  status          TEXT NOT NULL
    CHECK (status IN ('success', 'failure', 'skipped')),
  status_code     INTEGER,
  request_summary JSONB NOT NULL DEFAULT '{}',
  response_summary JSONB NOT NULL DEFAULT '{}',
  error_message   TEXT,
  duration_ms     INTEGER,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_recruiting_audit_org_created
  ON recruiting_audit_log(org_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_recruiting_audit_req
  ON recruiting_audit_log(requisition_id);

ALTER TABLE recruiting_audit_log ENABLE ROW LEVEL SECURITY;

-- Admins can read the org's full audit log. Writes are service-role only —
-- this table is append-only by application code, never by the user.
DROP POLICY IF EXISTS "recruiting_audit_select_admin" ON recruiting_audit_log;
CREATE POLICY "recruiting_audit_select_admin" ON recruiting_audit_log
  FOR SELECT USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE id = auth.uid() AND role = 'admin')
  );


-- ── 4. Sweeper for expired idempotency keys ──────────────────────────────────
-- Called from an Inngest cron (or directly from a worker on first miss) so the
-- table stays small. No-op when there's nothing to sweep.
CREATE OR REPLACE FUNCTION sweep_expired_idempotency_keys() RETURNS INTEGER AS $$
  WITH deleted AS (
    DELETE FROM idempotency_keys
    WHERE expires_at < now()
    RETURNING 1
  )
  SELECT COUNT(*)::INTEGER FROM deleted;
$$ LANGUAGE sql;
