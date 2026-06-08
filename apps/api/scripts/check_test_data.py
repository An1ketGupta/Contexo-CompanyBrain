"""Inspect what test data exists in the database.

Shows every org with its document / chunk / embedding counts so you can pick
the right org_id for smoke testing. Highlights the org most likely to be
useful (most chunks).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import get_service_client


async def main() -> None:
    svc = get_service_client()

    orgs_result = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select("id, name, plan, created_at")
        .order("created_at", desc=True)
        .execute()
    )
    orgs = orgs_result.data or []

    if not orgs:
        print("📦 No organizations in DB — sign up first via http://localhost:3000/signup")
        return

    print(f"📦 Organizations ({len(orgs)}):\n")
    rows: list[dict] = []

    for org in orgs:
        oid = org["id"]
        docs = await asyncio.to_thread(
            lambda: svc.table("documents").select("id, status", count="exact").eq("org_id", oid).execute()
        )
        chunks = await asyncio.to_thread(
            lambda: svc.table("chunks").select("id", count="exact").eq("org_id", oid).limit(1).execute()
        )
        embeddings = await asyncio.to_thread(
            lambda: svc.table("embeddings").select("id", count="exact").eq("org_id", oid).limit(1).execute()
        )

        doc_rows = docs.data or []
        ready = sum(1 for d in doc_rows if d.get("status") == "ready")
        failed = sum(1 for d in doc_rows if d.get("status") == "failed")
        in_flight = sum(1 for d in doc_rows if d.get("status") in ("pending", "processing"))

        rows.append({
            "id": oid,
            "name": org["name"],
            "docs_total": getattr(docs, "count", None) or len(doc_rows),
            "docs_ready": ready,
            "docs_failed": failed,
            "docs_in_flight": in_flight,
            "chunks": getattr(chunks, "count", None) or 0,
            "embeddings": getattr(embeddings, "count", None) or 0,
        })

    # Sort by usable data — embeddings first, then chunks, then docs
    rows.sort(key=lambda r: (r["embeddings"], r["chunks"], r["docs_total"]), reverse=True)

    best = rows[0] if rows[0]["embeddings"] > 0 else None

    for r in rows:
        marker = "👉 " if r is best else "   "
        flags = []
        if r["docs_in_flight"] > 0:
            flags.append(f"{r['docs_in_flight']} in-flight")
        if r["docs_failed"] > 0:
            flags.append(f"{r['docs_failed']} failed")
        flag_str = f"  [{', '.join(flags)}]" if flags else ""

        print(
            f"{marker}{r['name']:<20} {r['id']}"
            f"  docs={r['docs_total']} (ready={r['docs_ready']})"
            f"  chunks={r['chunks']}  embeddings={r['embeddings']}{flag_str}"
        )

    print()
    if best:
        print(f"✅ Suggested org for smoke testing: {best['id']}")
        print(
            f"\n  uv run python scripts/smoke_hybrid_search.py "
            f"--org-id {best['id']} --query 'your question' --compare"
        )
    else:
        print("❌ No org has embeddings yet.")
        print("   Sign up at http://localhost:3000, upload a PDF, and wait for Inngest to finish.")
        print("   Watch the Inngest Dev Server for the `process-document` run to complete.")


asyncio.run(main())
