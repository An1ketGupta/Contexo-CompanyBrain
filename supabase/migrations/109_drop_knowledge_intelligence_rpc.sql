-- Drop the knowledge_intelligence aggregates RPC (added in migration 014).
--
-- Its only caller was GET /usage/knowledge-intelligence, which backed the
-- admin "Knowledge intelligence" page at /insights. That page has been
-- removed, leaving this function dead.
--
-- Everything else from migration 014 stays: chunk_citations,
-- documents.citation_count / last_cited_at, and bump_document_citations are
-- still used by the hybrid-search retrieval boost, the health-score recency
-- signal, and the chat citations panel.

DROP FUNCTION IF EXISTS knowledge_intelligence(UUID);
