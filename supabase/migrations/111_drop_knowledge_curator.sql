-- Drop the storage behind the removed weekly Knowledge Curator agent.
--
-- Removed feature and its storage:
--   * /admin/knowledge-health          → knowledge_health_reports   (migration 051)
--                                        organizations.curator_*    (migration 051)
--
-- The whole curator surface went in the same change: the weekly Inngest cron
-- (`curator-weekly-scan`, Mon 08:00 UTC) and its per-org runner, the
-- knowledge_curator service (outdated docs / broken links / merge clusters),
-- the /admin/knowledge-health router and its six endpoints, the admin page,
-- and the five Next.js proxy routes. Nothing reads or writes these any more.
--
-- Deliberately NOT dropped:
--   * documents.health_score / health_label — the *daily* recompute
--     (`recompute-document-health`, 02:00 UTC) writes these and /admin/health
--     reads them. Different feature that happens to share the "health" name.
--   * The find_similar_documents RPC — the curator called it at a lower
--     threshold, but upload-time duplicate detection still uses it.
--   * scheduled_reports rows with report_type='knowledge_health' — that email
--     digest computes its own snapshot from `documents` and never touched the
--     curator.

-- Orphaned in-app notifications: their link_url points at the deleted page,
-- so leaving them would put dead links in admins' notification bells.
DELETE FROM notifications WHERE type = 'knowledge_health_report';

DROP TABLE IF EXISTS knowledge_health_reports;

ALTER TABLE organizations
  DROP COLUMN IF EXISTS curator_enabled,
  DROP COLUMN IF EXISTS curator_outdated_days,
  DROP COLUMN IF EXISTS curator_check_broken_links,
  DROP COLUMN IF EXISTS curator_merge_threshold;
