"""Full-text search via PostgreSQL tsvector + GIN.

Pairs with vector_search; identical return shape (plus a `snippet` field) so
the hybrid layer can fuse the two branches uniformly. The SQL function uses
`websearch_to_tsquery` — handles quoted phrases, OR, -exclusions and arbitrary
punctuation, so we never have to sanitize user input on the Python side.
"""
from __future__ import annotations

import asyncio
import logging

from supabase import Client

from .vector_search import Retriever, SearchHit

log = logging.getLogger(__name__)


class FTSRetriever(Retriever):
    """PostgreSQL FTS adapter — same `Retriever` protocol as VectorRetriever."""

    async def search(
        self,
        *,
        query: str,
        org_id: str,
        client: Client,
        k: int = 10,
        document_id: str | None = None,
        document_ids: list[str] | None = None,
    ) -> list[SearchHit]:
        query = query.strip()
        if not query:
            return []
        if document_ids is not None and len(document_ids) == 0:
            return []

        params: dict = {
            "query_text": query,
            "match_org_id": org_id,
            "match_count": k,
        }
        if document_id is not None:
            params["match_document_id"] = document_id
        if document_ids:
            params["match_document_ids"] = document_ids

        try:
            response = await asyncio.to_thread(
                lambda: client.rpc("fts_search", params).execute()
            )
        except Exception as exc:
            log.error("fts_search RPC failed for org=%s: %s", org_id, exc)
            raise

        rows = response.data or []
        return [_row_to_hit(r) for r in rows]


def _row_to_hit(row: dict) -> SearchHit:
    return SearchHit(
        chunk_id=row["chunk_id"],
        content=row["content"],
        document_id=row["document_id"],
        document_name=row["document_name"],
        chunk_index=row["chunk_index"],
        page_number=row.get("page_number"),
        section_heading=row.get("section_heading"),
        similarity=float(row["similarity"]),
        snippet=row.get("snippet"),
    )


_default_fts: FTSRetriever | None = None


def get_fts_retriever() -> FTSRetriever:
    global _default_fts
    if _default_fts is None:
        _default_fts = FTSRetriever()
    return _default_fts


async def fts_search(
    query: str,
    org_id: str,
    client: Client,
    *,
    k: int = 10,
    document_id: str | None = None,
    document_ids: list[str] | None = None,
) -> list[SearchHit]:
    """Convenience wrapper around the default FTS retriever."""
    return await get_fts_retriever().search(
        query=query,
        org_id=org_id,
        client=client,
        k=k,
        document_id=document_id,
        document_ids=document_ids,
    )
