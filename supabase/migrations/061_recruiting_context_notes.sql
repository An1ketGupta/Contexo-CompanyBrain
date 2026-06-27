-- Recruiting agent: per-hire disambiguation context + KB grounding signal.
--
-- context_notes:
--   Two hires for the same role (e.g. "Senior Engineer" on Platform vs ML)
--   previously pulled identical KB chunks because the facet queries were
--   built from role_request alone. context_notes captures the per-hire
--   specifics (team mission, HM, stack, problems-to-solve) so the queries
--   and the synthesis prompt can differentiate.
--
-- grounded:
--   True when at least one facet's hybrid_search returned usable chunks.
--   False means the JD variants are model-generated without company
--   context — comp ranges / stack are not from the KB. UI warns the user
--   before they publish to ATS.
ALTER TABLE job_requisitions
  ADD COLUMN IF NOT EXISTS context_notes TEXT,
  ADD COLUMN IF NOT EXISTS grounded BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS seniority_level TEXT
    CHECK (seniority_level IN ('intern', 'entry', 'mid', 'senior', 'staff', 'lead')),
  -- Null means "not disclosed" — intentional for roles where comp is negotiated privately.
  ADD COLUMN IF NOT EXISTS disclosed_compensation TEXT,
  -- Free-text interview process: rounds, format, panel, timeline.
  ADD COLUMN IF NOT EXISTS interview_details TEXT;
