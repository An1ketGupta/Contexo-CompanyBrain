-- ── Day 13: Knowledge Refresh (#38) + Outbound Webhooks (#85, #99) ─────────
-- Two independent feature pairs, deployed together because they're both
-- "the knowledge base reaches outward over time" — refresh polices the
-- *contents* against staleness, webhooks broadcast *events* to outside
-- systems. One migration so a single `supabase db push` brings everything
-- in line.

-- ── 1. Knowledge refresh fields on documents (#38) ─────────────────────────
-- review_frequency_days: how often an admin wants to be nudged to look at
-- the doc again. NULL = never auto-review (most uploads). Common values:
-- 90 (quarterly policy docs), 180 (HR handbook), 365 (annual reports).
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS review_frequency_days INTEGER
    CHECK (review_frequency_days IS NULL OR review_frequency_days BETWEEN 7 AND 730);

-- review_due_at: precomputed = last_reviewed_at + review_frequency_days.
-- Stored rather than computed-on-read so the weekly cron can scan an index
-- instead of evaluating an expression per row.
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS review_due_at TIMESTAMPTZ;

-- last_reviewed_at: NULL on a brand-new doc (we treat upload date as the
-- baseline below). Set whenever the admin clicks "Mark as reviewed".
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS last_reviewed_at TIMESTAMPTZ;

-- Partial index — the weekly cron only ever filters by review_due_at < now().
-- Excluding NULL rows shrinks the index to "docs that actually have a
-- review cadence set" which is a small fraction of the doc table.
CREATE INDEX IF NOT EXISTS idx_documents_review_due
  ON documents (org_id, review_due_at)
  WHERE review_due_at IS NOT NULL;

-- Helper: bump the timer. Called from PATCH /documents/{id}/review and from
-- the "Mark as reviewed" endpoint. Atomic so a concurrent admin click won't
-- race the recalculation back to an old value.
CREATE OR REPLACE FUNCTION mark_document_reviewed(target_doc_id UUID)
RETURNS TIMESTAMPTZ
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  freq INTEGER;
  next_due TIMESTAMPTZ;
BEGIN
  SELECT review_frequency_days INTO freq
  FROM documents
  WHERE id = target_doc_id;

  IF freq IS NULL THEN
    -- No cadence configured — just stamp last_reviewed_at and clear due.
    UPDATE documents
    SET last_reviewed_at = now(), review_due_at = NULL
    WHERE id = target_doc_id;
    RETURN NULL;
  END IF;

  next_due := now() + (freq || ' days')::INTERVAL;
  UPDATE documents
  SET last_reviewed_at = now(), review_due_at = next_due
  WHERE id = target_doc_id;
  RETURN next_due;
END;
$$;

REVOKE EXECUTE ON FUNCTION mark_document_reviewed(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION mark_document_reviewed(UUID) TO authenticated;
GRANT EXECUTE ON FUNCTION mark_document_reviewed(UUID) TO service_role;


-- ── 2. Outbound webhooks (#85 Zapier + #99 custom) ─────────────────────────
-- One webhook row per (org, url, event-set). The Inngest delivery worker
-- looks up by (org_id, event-in-events) on every fire, so the GIN index on
-- events is what matters for hot-path lookups.
CREATE TABLE IF NOT EXISTS webhooks (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  created_by        UUID REFERENCES users(id) ON DELETE SET NULL,
  name              VARCHAR(120) NOT NULL,
  url               TEXT NOT NULL,
  -- Subset of: 'document.processed', 'document.failed', 'query.completed'.
  -- Stored as TEXT[] (not an enum) so adding events later doesn't need a
  -- migration; the API layer enforces the allowed set.
  events            TEXT[] NOT NULL DEFAULT '{}',
  -- HMAC secret. Optional — if NULL, deliveries go out unsigned and the
  -- receiver is on the hook for trusting the URL alone (common for Zapier
  -- catch-hooks that don't verify).
  secret            TEXT,
  is_active         BOOLEAN NOT NULL DEFAULT true,
  -- Denormalised "last delivery status" for the UI list view. The full log
  -- lives in webhook_deliveries; these two columns spare the list view from
  -- a per-row join.
  last_status       INTEGER,
  last_triggered_at TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_webhooks_org_active
  ON webhooks (org_id)
  WHERE is_active = true;

CREATE INDEX IF NOT EXISTS idx_webhooks_events_gin
  ON webhooks USING GIN (events);

ALTER TABLE webhooks ENABLE ROW LEVEL SECURITY;

-- Org members can read; only admins can mutate (enforced at API layer with
-- a defence-in-depth check before the call, but the SELECT policy here means
-- a stolen anon key can't list other orgs' webhook URLs).
CREATE POLICY "webhooks_select" ON webhooks
  FOR SELECT USING (org_id = auth_org_id());

CREATE POLICY "webhooks_modify" ON webhooks
  FOR ALL USING (org_id = auth_org_id())
  WITH CHECK (org_id = auth_org_id());


-- webhook_deliveries: one row per attempt. Inngest retries are visible to
-- the admin as a delivery history. We trim old rows from a periodic cleanup
-- (TODO: a separate cron once the table grows large enough to matter).
CREATE TABLE IF NOT EXISTS webhook_deliveries (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  webhook_id      UUID NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
  org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  event           TEXT NOT NULL,
  -- The actual payload we POSTed. JSONB so we can index/query later if
  -- support needs to ask "what did we send?" without grepping logs.
  payload         JSONB NOT NULL DEFAULT '{}',
  status_code     INTEGER,
  response_body   TEXT,            -- truncated; see worker
  error           TEXT,             -- set on transport-level failures
  attempt         INTEGER NOT NULL DEFAULT 1,
  delivered_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_webhook_time
  ON webhook_deliveries (webhook_id, delivered_at DESC);

CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_org_time
  ON webhook_deliveries (org_id, delivered_at DESC);

ALTER TABLE webhook_deliveries ENABLE ROW LEVEL SECURITY;

CREATE POLICY "webhook_deliveries_select" ON webhook_deliveries
  FOR SELECT USING (org_id = auth_org_id());
