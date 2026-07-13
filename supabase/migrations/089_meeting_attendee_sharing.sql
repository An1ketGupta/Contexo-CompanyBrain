-- 089 — Automatic transcript sharing with opted-in meeting attendees.
--
-- Migrations 084/086 made meeting transcripts private-by-default, visible
-- only to the host (created_by) until they explicitly publish org-wide.
-- That's all-or-nothing: the host can't grant just their fellow attendees
-- access without exposing the transcript to the entire company. This adds
-- a per-user grant so a transcript can be shared with exactly the people
-- who were on the call, automatically, without an org-wide publish.
--
-- document_shares is the actual grant. Deliberately not a document copy —
-- one set of chunks/embeddings stays the source of truth, shares just add
-- readers.
CREATE TABLE document_shares (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id  UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  org_id       UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  granted_via  TEXT NOT NULL DEFAULT 'zoom_attendee_auto_share',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (document_id, user_id)
);

CREATE INDEX idx_document_shares_document ON document_shares (document_id);
CREATE INDEX idx_document_shares_user ON document_shares (user_id);

ALTER TABLE document_shares ENABLE ROW LEVEL SECURITY;

-- Users can see grants made to them (e.g. a future "shared with me" list).
-- Written only by the service role (Inngest ingest path) — no INSERT/UPDATE/
-- DELETE policy for regular users, same posture as chunks/embeddings.
CREATE POLICY document_shares_select ON document_shares
  FOR SELECT USING (org_id = auth_org_id() AND user_id = auth.uid());

-- Buffer of who joined which Zoom meeting, populated by the
-- meeting.participant_joined webhook, consumed once when
-- recording.transcript_completed arrives for the same meeting_uuid. Rows
-- accumulate with no purge job (same accepted tradeoff as archived chunks —
-- small rows, safe to leave). No RLS policies: service-role-only table,
-- never read by a user-scoped client.
CREATE TABLE zoom_meeting_participants (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  meeting_uuid  TEXT NOT NULL,
  email         TEXT NOT NULL,
  joined_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (meeting_uuid, email)
);

CREATE INDEX idx_zoom_meeting_participants_meeting ON zoom_meeting_participants (meeting_uuid);

ALTER TABLE zoom_meeting_participants ENABLE ROW LEVEL SECURITY;

-- ── Extend the visibility gate with "shared with me" ─────────────────────────
-- Every place that currently checks (visibility = 'org' OR created_by = auth.uid())
-- gains an OR EXISTS(document_shares) branch. Four call sites, same as when
-- 086 extended 084's predicate: documents RLS, meeting_summaries RLS, and
-- both search RPCs (SECURITY INVOKER, called by service_role too — auth.uid()
-- is NULL there, so the EXISTS is harmlessly false for background callers,
-- same reasoning 084 documented for the existing OR clause).

DROP POLICY IF EXISTS "documents_select" ON documents;
CREATE POLICY "documents_select" ON documents
  FOR SELECT USING (
    org_id = auth_org_id()
    AND (
      visibility = 'org'
      OR created_by = auth.uid()
      OR EXISTS (
        SELECT 1 FROM document_shares ds
        WHERE ds.document_id = documents.id AND ds.user_id = auth.uid()
      )
    )
  );

DROP POLICY IF EXISTS meeting_summaries_select_org ON meeting_summaries;
CREATE POLICY meeting_summaries_select_org ON meeting_summaries
  FOR SELECT USING (
    org_id = auth_org_id()
    AND EXISTS (
      SELECT 1 FROM documents d
      WHERE d.id = meeting_summaries.source_document_id
        AND (
          d.visibility = 'org'
          OR d.created_by = auth.uid()
          OR EXISTS (
            SELECT 1 FROM document_shares ds
            WHERE ds.document_id = d.id AND ds.user_id = auth.uid()
          )
        )
    )
  );

DROP FUNCTION IF EXISTS vector_search(vector, uuid, int, uuid, uuid[]);

CREATE FUNCTION vector_search(
  query_embedding    vector(768),
  match_org_id       uuid,
  match_count        int    DEFAULT 10,
  match_document_id  uuid   DEFAULT NULL,
  match_document_ids uuid[] DEFAULT NULL
)
RETURNS TABLE (
  chunk_id            uuid,
  content             text,
  document_id         uuid,
  document_name       text,
  document_version_id uuid,
  version_number      int,
  chunk_index         int,
  page_number         int,
  section_heading     text,
  similarity          float
)
LANGUAGE plpgsql
VOLATILE
SECURITY INVOKER
SET search_path = public
AS $$
BEGIN
  SET LOCAL ivfflat.probes = 10;

  RETURN QUERY
  SELECT
    c.id                                              AS chunk_id,
    c.content                                         AS content,
    c.document_id                                     AS document_id,
    d.name                                            AS document_name,
    c.document_version_id                             AS document_version_id,
    dv.version_number                                 AS version_number,
    c.chunk_index                                     AS chunk_index,
    c.page_number                                     AS page_number,
    c.section_heading                                 AS section_heading,
    (1 - (e.embedding <=> query_embedding))::float    AS similarity
  FROM embeddings e
  JOIN chunks   c  ON c.id = e.chunk_id
  JOIN documents d ON d.id = c.document_id
  LEFT JOIN document_versions dv ON dv.id = c.document_version_id
  WHERE e.org_id = match_org_id
    AND c.is_archived = false
    AND (
      d.visibility = 'org'
      OR d.created_by = auth.uid()
      OR EXISTS (
        SELECT 1 FROM document_shares ds
        WHERE ds.document_id = d.id AND ds.user_id = auth.uid()
      )
    )
    AND (match_document_id IS NULL OR c.document_id = match_document_id)
    AND (match_document_ids IS NULL OR c.document_id = ANY(match_document_ids))
  ORDER BY e.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;

GRANT EXECUTE ON FUNCTION vector_search(vector, uuid, int, uuid, uuid[]) TO authenticated;
GRANT EXECUTE ON FUNCTION vector_search(vector, uuid, int, uuid, uuid[]) TO service_role;

DROP FUNCTION IF EXISTS fts_search(text, uuid, int, uuid, uuid[]);

CREATE FUNCTION fts_search(
  query_text         text,
  match_org_id       uuid,
  match_count        int    DEFAULT 10,
  match_document_id  uuid   DEFAULT NULL,
  match_document_ids uuid[] DEFAULT NULL
)
RETURNS TABLE (
  chunk_id            uuid,
  content             text,
  document_id         uuid,
  document_name       text,
  document_version_id uuid,
  version_number      int,
  chunk_index         int,
  page_number         int,
  section_heading     text,
  similarity          float,
  snippet             text
)
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
  q tsquery := websearch_to_tsquery('english', query_text);
BEGIN
  RETURN QUERY
  SELECT
    c.id                                         AS chunk_id,
    c.content                                    AS content,
    c.document_id                                AS document_id,
    d.name                                       AS document_name,
    c.document_version_id                        AS document_version_id,
    dv.version_number                            AS version_number,
    c.chunk_index                                AS chunk_index,
    c.page_number                                AS page_number,
    c.section_heading                            AS section_heading,
    ts_rank_cd(c.content_tsv, q)::float          AS similarity,
    ts_headline('english', c.content, q,
      'MaxFragments=2, MaxWords=20, MinWords=8, '
      'StartSel=<mark>, StopSel=</mark>')        AS snippet
  FROM chunks c
  JOIN documents d ON d.id = c.document_id
  LEFT JOIN document_versions dv ON dv.id = c.document_version_id
  WHERE c.org_id = match_org_id
    AND c.is_archived = false
    AND c.content_tsv @@ q
    AND (
      d.visibility = 'org'
      OR d.created_by = auth.uid()
      OR EXISTS (
        SELECT 1 FROM document_shares ds
        WHERE ds.document_id = d.id AND ds.user_id = auth.uid()
      )
    )
    AND (match_document_id IS NULL OR c.document_id = match_document_id)
    AND (match_document_ids IS NULL OR c.document_id = ANY(match_document_ids))
  ORDER BY similarity DESC
  LIMIT match_count;
END;
$$;

GRANT EXECUTE ON FUNCTION fts_search(text, uuid, int, uuid, uuid[]) TO authenticated;
GRANT EXECUTE ON FUNCTION fts_search(text, uuid, int, uuid, uuid[]) TO service_role;
