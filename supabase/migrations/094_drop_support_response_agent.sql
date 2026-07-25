-- Retire the SupportResponse agent feature (customer-support email drafting).
--
-- Drops support_tickets and everything scoped to it (indexes, trigger,
-- function, RLS policies all cascade). Does NOT touch documents.auto_tagged
-- — that column shipped in the same original migration (032) but belongs to
-- the unrelated auto-tagger feature, which is still active.
--
-- agent_runs rows with agent_type = 'support_response' are left in place;
-- agent_type is free-text with no FK/CHECK constraint, so old rows remain
-- as harmless audit history.

DROP TABLE IF EXISTS support_tickets CASCADE;
