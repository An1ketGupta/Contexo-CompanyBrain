-- Remove the retired automatic Zoom integration while preserving previously
-- ingested transcript documents and their access grants.

DROP TABLE IF EXISTS zoom_meeting_participants;

DELETE FROM integrations
WHERE provider = 'zoom';

ALTER TABLE document_shares
  ALTER COLUMN granted_via SET DEFAULT 'direct_share';

UPDATE document_shares
SET granted_via = 'legacy_attendee_auto_share'
WHERE granted_via = 'zoom_attendee_auto_share';
