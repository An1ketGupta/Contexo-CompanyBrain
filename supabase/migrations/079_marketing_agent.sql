-- ── 079: Marketing Agent ─────────────────────────────────────────────────
--
-- Third "big" agent in the family (after Sales/RFP and Recruiting/Interview
-- Kit). A marketer picks an objective ("launch new pricing tier", "drive
-- demos for HR persona", "compete against Lattice"), selects channels, and
-- optionally names competitors. The MarketingAgent grounds in the org's KB
-- (positioning, brand voice, prior campaigns, competitor battlecards) and
-- emits 5 editable artifacts:
--   * positioning         — ICP, problem, category, differentiation, taglines
--   * messaging_pillars   — 3–5 weighted pillars with proof points
--   * competitive_angle   — per-competitor counter + win themes (optional)
--   * channel_plan        — per-channel positioning lens + 2–3 draft variants
--   * content_brief       — long-form outline + keywords + length guidance
--
-- One row per brief. JSONB columns for the artifacts — same shape decision
-- as interview_kits: the brief is one editable document, not a transactional
-- multi-table flow, so RLS-at-one-row + LLM-emits-one-document keeps the
-- write path simple.
--
-- Idempotent: every CREATE/ALTER guarded by IF NOT EXISTS / DROP IF EXISTS.

CREATE TABLE IF NOT EXISTS marketing_briefs (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                 UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  created_by             UUID NOT NULL REFERENCES users(id),
  -- Cross-link to agent_runs so admins can navigate run → brief.
  run_id                 UUID REFERENCES agent_runs(id) ON DELETE SET NULL,
  -- The marketer's intent in their own words. Drives all 5 artifact prompts.
  objective              TEXT NOT NULL,
  -- Optional ICP / segment hint. Blank → agent infers from KB.
  audience_hint          TEXT,
  -- Subset of {'blog','linkedin','x','email','landing','ads'}. The channel_plan
  -- only emits drafts for the selected channels.
  channels               TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  -- Optional comma-list of named competitors. Empty → competitive_angle is
  -- skipped (the artifact column stays []).
  competitors            TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  -- Optional scoping to a marketing-specific KB collection. NULL = org-wide.
  collection_id          UUID REFERENCES collections(id) ON DELETE SET NULL,

  -- Artifact 1 — positioning. Shape:
  --   {audience, problem, category, differentiation,
  --    value_props: [{name, statement}], taglines: ["...", "...", "..."]}
  positioning            JSONB NOT NULL DEFAULT '{}'::jsonb,
  -- Artifact 2 — messaging pillars. Shape:
  --   [{name, statement, proof_points: ["..."], weight: 0..1}]
  messaging_pillars      JSONB NOT NULL DEFAULT '[]'::jsonb,
  -- Artifact 3 — competitive angle (one row per named competitor):
  --   [{competitor, their_pitch, our_counter, win_themes: ["..."],
  --     gotchas: ["..."]}]
  competitive_angle      JSONB NOT NULL DEFAULT '[]'::jsonb,
  -- Artifact 4 — channel plan (one row per selected channel):
  --   [{channel: 'blog'|'linkedin'|'x'|'email'|'landing'|'ads',
  --     lens, cta, timing,
  --     drafts: [{title?, body, hook?, length_hint?}]}]
  channel_plan           JSONB NOT NULL DEFAULT '[]'::jsonb,
  -- Artifact 5 — long-form content brief. Shape:
  --   {working_title, target_length_words, target_keywords: ["..."],
  --    outline: [{heading, key_points: ["..."]}],
  --    internal_link_ideas: ["..."], distribution_notes}
  content_brief          JSONB NOT NULL DEFAULT '{}'::jsonb,
  -- KB documents the agent grounded the brief on. Same shape as the
  -- interview_kits / RFP `sources` so the UI's "Based on:" panel is one
  -- shared component.
  sources                JSONB NOT NULL DEFAULT '[]'::jsonb,

  status                 TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'generating', 'ready', 'published', 'failed')),
  error_message          TEXT,
  generated_at           TIMESTAMPTZ,
  published_at           TIMESTAMPTZ,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_marketing_briefs_org_created
  ON marketing_briefs(org_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_marketing_briefs_org_status
  ON marketing_briefs(org_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_marketing_briefs_created_by
  ON marketing_briefs(org_id, created_by, created_at DESC);

-- Touch updated_at on every UPDATE so the marketer sees "last edited 3 min
-- ago" without us hand-stamping it on every PATCH.
CREATE OR REPLACE FUNCTION marketing_briefs_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_marketing_briefs_touch_updated_at ON marketing_briefs;
CREATE TRIGGER trg_marketing_briefs_touch_updated_at
  BEFORE UPDATE ON marketing_briefs
  FOR EACH ROW EXECUTE FUNCTION marketing_briefs_touch_updated_at();

ALTER TABLE marketing_briefs ENABLE ROW LEVEL SECURITY;

-- Reads: any org member. Marketing is org-shared work (sales reads the
-- positioning, recruiting reads the messaging pillars for employer brand).
CREATE POLICY "marketing_briefs_select" ON marketing_briefs
  FOR SELECT USING (org_id = auth_org_id());

CREATE POLICY "marketing_briefs_insert" ON marketing_briefs
  FOR INSERT WITH CHECK (org_id = auth_org_id() AND created_by = auth.uid());

CREATE POLICY "marketing_briefs_update" ON marketing_briefs
  FOR UPDATE USING (
    org_id = auth_org_id()
    AND (created_by = auth.uid() OR EXISTS (
      SELECT 1 FROM users WHERE id = auth.uid() AND role = 'admin'
    ))
  );

CREATE POLICY "marketing_briefs_delete" ON marketing_briefs
  FOR DELETE USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE id = auth.uid() AND role = 'admin')
  );

COMMENT ON TABLE marketing_briefs IS
  '079: MarketingAgent output — positioning, pillars, competitive angle, channel plan, content brief.';
