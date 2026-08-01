-- Retire the Executive Briefing feature (KB synthesis -> Google Doc -> email).
--
-- Drops executive_briefings and everything scoped to it (index and RLS
-- policies cascade). The feature's API routes, service, and UI page are
-- removed in the same change; nothing else reads this table.
--
-- Does NOT touch the other tables created in 053 (rfp_responses,
-- calendar_meetings, …) — those belong to features that are still active.
-- The morning `briefings` feature (migration 057) is unrelated and unaffected.

DROP TABLE IF EXISTS executive_briefings CASCADE;
