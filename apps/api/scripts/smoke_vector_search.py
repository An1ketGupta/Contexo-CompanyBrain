"""Vector-search smoke test against a real org's embeddings.

Prereq:
  1. Migration `004_search_functions.sql` is applied (supabase db push)
  2. Some documents are ingested for the org (via test_pipeline.py)

Usage:
  uv run python scripts/smoke_vector_search.py --org-id <uuid> --query "your question"
  uv run python scripts/smoke_vector_search.py --org-id <uuid> --query "..." --k 5
  uv run python scripts/smoke_vector_search.py --org-id <uuid> --query "..." --doc <doc-uuid>
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import get_service_client
from app.services.retrieval import vector_search


async def _run(query: str, org_id: str, k: int, document_id: str | None) -> None:
    client = get_service_client()
    print(f"🔎 query: {query!r}  org={org_id}  k={k}  doc={document_id}")
    hits = await vector_search(
        query=query,
        org_id=org_id,
        client=client,
        k=k,
        document_id=document_id,
    )
    if not hits:
        print("(no matches — verify the org has ingested docs and migration 004 is applied)")
        return
    for i, h in enumerate(hits, 1):
        head = f"[{i}] {h.similarity:.3f}  {h.document_name}"
        if h.page_number is not None:
            head += f", p.{h.page_number}"
        if h.section_heading:
            head += f"  §{h.section_heading}"
        print(head)
        excerpt = h.content.replace("\n", " ")
        if len(excerpt) > 220:
            excerpt = excerpt[:220] + "…"
        print(f"     {excerpt}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--query", required=True)
    p.add_argument("--org-id", required=True)
    p.add_argument("--k", type=int, default=10)
    p.add_argument("--doc", dest="document_id", default=None)
    args = p.parse_args()
    asyncio.run(_run(args.query, args.org_id, args.k, args.document_id))


if __name__ == "__main__":
    main()
