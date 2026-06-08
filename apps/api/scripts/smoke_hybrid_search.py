"""Hybrid-retrieval smoke test against a real org's data.

Prereq:
  1. Migrations 004 and 005 applied (supabase db push)
  2. Some documents ingested for the org

Usage:
  uv run python scripts/smoke_hybrid_search.py --org-id <uuid> --query "..."
  uv run python scripts/smoke_hybrid_search.py --org-id <uuid> --query "..." --mode vector
  uv run python scripts/smoke_hybrid_search.py --org-id <uuid> --query "..." --mode fts
  uv run python scripts/smoke_hybrid_search.py --org-id <uuid> --query "..." --compare
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import get_service_client
from app.services.retrieval import (
    SearchHit,
    fts_search,
    hybrid_search,
    vector_search,
)


def _print_hits(label: str, hits: list[SearchHit]) -> None:
    print(f"\n── {label} ({len(hits)} hits) ───────────────────────────────")
    if not hits:
        print("  (none)")
        return
    for i, h in enumerate(hits, 1):
        head = f"[{i}] score={h.similarity:.4f}  {h.document_name}"
        if h.page_number is not None:
            head += f", p.{h.page_number}"
        if h.section_heading:
            head += f"  §{h.section_heading}"
        if h.match_sources:
            head += f"  via={'+'.join(h.match_sources)}"
        if h.vector_rank is not None or h.fts_rank is not None:
            head += f"  (vec_rank={h.vector_rank}, fts_rank={h.fts_rank})"
        print(head)
        excerpt = (h.snippet or h.content).replace("\n", " ")
        if len(excerpt) > 220:
            excerpt = excerpt[:220] + "…"
        print(f"     {excerpt}")


async def _run(query: str, org_id: str, k: int, doc_id: str | None, mode: str, compare: bool) -> None:
    client = get_service_client()
    print(f"🔎 query={query!r}  org={org_id}  k={k}  doc={doc_id}  mode={mode}")

    if compare:
        v, f, h = await asyncio.gather(
            vector_search(query, org_id, client, k=k, document_id=doc_id),
            fts_search(query, org_id, client, k=k, document_id=doc_id),
            hybrid_search(query, org_id, client, k=k, document_id=doc_id),
        )
        _print_hits("VECTOR", v)
        _print_hits("FTS", f)
        _print_hits("HYBRID", h)
        return

    if mode == "vector":
        hits = await vector_search(query, org_id, client, k=k, document_id=doc_id)
    elif mode == "fts":
        hits = await fts_search(query, org_id, client, k=k, document_id=doc_id)
    else:
        hits = await hybrid_search(query, org_id, client, k=k, document_id=doc_id)
    _print_hits(mode.upper(), hits)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--query", required=True)
    p.add_argument("--org-id", required=True)
    p.add_argument("--k", type=int, default=10)
    p.add_argument("--doc", dest="document_id", default=None)
    p.add_argument("--mode", choices=["hybrid", "vector", "fts"], default="hybrid")
    p.add_argument(
        "--compare",
        action="store_true",
        help="Show vector / fts / hybrid side by side for quality checks.",
    )
    args = p.parse_args()
    asyncio.run(_run(args.query, args.org_id, args.k, args.document_id, args.mode, args.compare))


if __name__ == "__main__":
    main()
