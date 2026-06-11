-- ── V4 Day 4: knowledge-base coverage score cache ──────────────────────────
--
-- Coverage score is a per-org "how complete is this knowledge base?" rollup
-- against eight canonical business categories (HR, product, sales, …). The
-- computation is hybrid keyword + embedding similarity over a sample of
-- chunks — cheap but not free (8 embedding calls + RPCs on every refresh),
-- so we cache the result rather than re-derive on every page load.
--
-- Why a DB row, not Redis:
--   * No Upstash adapter exists in the codebase yet — pulling one in just
--     for coverage adds an env var and a runtime dependency for one feature.
--   * The shape is small and structured; Postgres is fine.
--   * Observable: admins can SELECT * FROM coverage_scores to debug.
--   * Free-tier-friendly: no extra infra.
--
-- Staleness is read-side: the service checks computed_at < now() - 1h and
-- recomputes. Writes also fire on document-ready (eager invalidation in
-- the ingestion pipeline) so freshly uploaded docs show up immediately.

CREATE TABLE IF NOT EXISTS coverage_scores (
  -- One row per org. PK is org_id so UPSERT (ON CONFLICT DO UPDATE) collapses
  -- concurrent recomputes safely.
  org_id        UUID PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
  -- Full payload returned by the API. Shape is fixed by app/services/coverage.py
  -- and described there; we don't enforce it in SQL because shape evolution
  -- shouldn't need a migration.
  score_json    JSONB NOT NULL,
  -- Overall score (0.0–1.0) lifted out of score_json for cheap admin queries
  -- like "list orgs whose coverage is < 0.5".
  overall_score REAL NOT NULL CHECK (overall_score >= 0 AND overall_score <= 1),
  computed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_coverage_scores_computed_at
  ON coverage_scores (computed_at DESC);

ALTER TABLE coverage_scores ENABLE ROW LEVEL SECURITY;

-- Admins read their org's row. Service role bypasses RLS for writes — there
-- is no INSERT/UPDATE policy because authenticated users should never write
-- to this table directly (the compute path runs under service role).
DROP POLICY IF EXISTS coverage_scores_select ON coverage_scores;
CREATE POLICY coverage_scores_select ON coverage_scores
  FOR SELECT
  USING (org_id = auth_org_id());
