-- Drop the storage behind the removed Coverage and Knowledge-gaps admin pages.
--
-- Removed features and their tables:
--   * /admin/coverage        → coverage_scores          (migration 024)
--   * /admin/knowledge-gaps  → knowledge_gaps           (migration 027)
--                              document_drafts          (migration 027)
--                              knowledge_gap_reports    (migration 078)
--                              knowledge_gap_clusters   (migration 078)
--
-- The gap-detection pipeline, its alert email, the KnowledgeCurator's stub
-- drafting, and every reader of these tables (executive briefings, the weekly
-- digest, scheduled reports) were removed in the same change, so nothing
-- reads or writes them.
--
-- Deliberately NOT dropped:
--   * chunk_citations / documents.citation_count / last_cited_at — still power
--     the hybrid-search retrieval boost and the chat citations panel.
--   * knowledge_health_reports — the KnowledgeCurator still writes it and
--     /admin/knowledge-health still reads it.
--   * documents.gap_flag_count — surfaced on /admin/health; unwritten since
--     migration 020, but that predates this change.
--   * The documents_source_check constraint value 'knowledge_gap_stub' —
--     existing rows created by the old approval flow still carry it, and
--     narrowing the CHECK would invalidate them.

DROP TABLE IF EXISTS knowledge_gap_clusters;
DROP TABLE IF EXISTS knowledge_gap_reports;
DROP TABLE IF EXISTS document_drafts;
DROP TABLE IF EXISTS knowledge_gaps;
DROP TABLE IF EXISTS coverage_scores;
