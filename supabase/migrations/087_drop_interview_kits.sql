-- ── 087: Drop Interview Kit Agent ─────────────────────────────────────────
--
-- The Interview Kit feature (introduced in 078) is being removed entirely.
-- Knowledge Gap v2 from 078 is untouched — it's a separate feature that
-- happened to ship in the same migration file.

DROP TRIGGER IF EXISTS trg_interview_kits_touch_updated_at ON interview_kits;
DROP FUNCTION IF EXISTS interview_kits_touch_updated_at();
DROP TABLE IF EXISTS interview_kits;
