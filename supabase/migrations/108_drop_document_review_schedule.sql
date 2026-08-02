-- ── Drop the document review schedule (reverses part 1 of migration 015) ──
--
-- The "Set review schedule" feature is removed: the per-document review
-- cadence dialog, the Monday `check-document-reviews` cron and its
-- knowledge_refresh email, the in-chat "may be outdated" banner, the admin
-- Health mark_for_review / request_owner_review bulk actions, and the
-- morning-briefing "docs due for review" section all went with it. Nothing
-- reads or writes these columns anymore.
--
-- What we KEEP from 015: the webhooks + webhook_deliveries tables (part 2).
-- They are an unrelated feature that merely shipped in the same migration.

-- The partial index goes first — dropping a column would take it with it,
-- but being explicit keeps the intent readable.
DROP INDEX IF EXISTS idx_documents_review_due;

ALTER TABLE documents DROP COLUMN IF EXISTS review_frequency_days;
ALTER TABLE documents DROP COLUMN IF EXISTS review_due_at;
ALTER TABLE documents DROP COLUMN IF EXISTS last_reviewed_at;

-- SECURITY DEFINER helper that recomputed the review timer. Its only caller
-- was POST /documents/{id}/mark-reviewed, which no longer exists.
DROP FUNCTION IF EXISTS mark_document_reviewed(UUID);

-- In-app notifications the cron and the bulk action produced. Their link_url
-- points at a review queue that no longer renders, so leaving them would give
-- users dead bell entries.
DELETE FROM notifications
WHERE type IN ('review_reminder', 'document_review_requested');
