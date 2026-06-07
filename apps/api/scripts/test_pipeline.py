"""Manual end-to-end harness for the ingestion pipeline.

Usage:
    uv run python scripts/test_pipeline.py --doc-id <uuid>
    uv run python scripts/test_pipeline.py --file ./sample.pdf --org-id <uuid>

The first form downloads a file already uploaded via the app and runs the full
pipeline (parse → chunk → embed → store), then prints stats. Use it to verify
Day 6+7 against real documents before Day 8 wires up Inngest.

The second form processes a local file. It uses a synthetic doc_id and a real
org_id; it inserts a 'documents' row first so the foreign keys hold.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from pathlib import Path

# Make app/ importable when this is run from apps/api/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import get_service_client  # noqa: E402
from app.services.ingestion import (  # noqa: E402
    PipelineError,
    download_from_storage,
    mark_status,
    process_document,
)


async def _run_with_doc_id(doc_id: str) -> None:
    client = get_service_client()
    result = await asyncio.to_thread(
        lambda: client.table("documents")
        .select("id, org_id, file_path, file_type, name")
        .eq("id", doc_id)
        .maybe_single()
        .execute()
    )
    if not result.data:
        print(f"❌ No document found with id={doc_id}")
        return
    doc = result.data
    print(f"📄 {doc['name']}  ({doc['file_type']}, org={doc['org_id']})")

    await mark_status(doc_id, "processing", client=client)
    try:
        file_bytes = await download_from_storage(doc["file_path"], client=client)
        print(f"⬇️  downloaded {len(file_bytes):,} bytes")
        stats = await process_document(
            doc_id=doc_id,
            org_id=doc["org_id"],
            file_bytes=file_bytes,
            file_type=doc["file_type"],
            client=client,
        )
        await mark_status(doc_id, "ready", chunk_count=stats["chunk_count"], client=client)
        print(f"✅ {stats}")
    except PipelineError as exc:
        await mark_status(doc_id, "failed", error_reason=str(exc), client=client)
        print(f"❌ pipeline failed: {exc}")


async def _run_with_local_file(file_path: Path, org_id: str) -> None:
    file_type = file_path.suffix.lstrip(".").lower()
    if file_type not in {"pdf", "docx", "txt", "md"}:
        print(f"❌ Unsupported file type: .{file_type}")
        return

    client = get_service_client()
    doc_id = str(uuid.uuid4())
    file_bytes = file_path.read_bytes()
    print(f"📄 {file_path.name}  ({file_type}, {len(file_bytes):,} bytes)")

    await asyncio.to_thread(
        lambda: client.table("documents")
        .insert(
            {
                "id": doc_id,
                "org_id": org_id,
                "name": file_path.name,
                "file_path": f"local/{file_path.name}",
                "file_type": file_type,
                "file_size_bytes": len(file_bytes),
                "status": "processing",
            }
        )
        .execute()
    )

    try:
        stats = await process_document(
            doc_id=doc_id,
            org_id=org_id,
            file_bytes=file_bytes,
            file_type=file_type,
            client=client,
        )
        await mark_status(doc_id, "ready", chunk_count=stats["chunk_count"], client=client)
        print(f"✅ {stats}")
    except PipelineError as exc:
        await mark_status(doc_id, "failed", error_reason=str(exc), client=client)
        print(f"❌ pipeline failed: {exc}")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--doc-id", help="UUID of an already-uploaded document")
    group.add_argument("--file", help="Path to a local file to process")
    parser.add_argument("--org-id", help="org_id (required when using --file)")
    args = parser.parse_args()

    if args.doc_id:
        asyncio.run(_run_with_doc_id(args.doc_id))
    else:
        if not args.org_id:
            parser.error("--org-id is required when --file is given")
        asyncio.run(_run_with_local_file(Path(args.file), args.org_id))


if __name__ == "__main__":
    main()
