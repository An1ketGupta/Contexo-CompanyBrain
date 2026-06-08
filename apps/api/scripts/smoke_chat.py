"""Chat orchestrator smoke test against a real org's data + real Gemini.

Prereq:
  1. Migrations 004 + 005 + 006 applied
  2. GEMINI_API_KEY in .env
  3. The target org has at least one processed document

Usage:
  uv run python scripts/smoke_chat.py --org-id <uuid> --message "Write an email asking about our vacation policy"
  uv run python scripts/smoke_chat.py --org-id <uuid> --message "..." --stream
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import get_service_client
from app.services.llm.task_chain import (
    ErrorEvent,
    FinalEvent,
    SearchedEvent,
    SearchingEvent,
    SourcesEvent,
    TokenEvent,
    execute_task,
)


async def _run(message: str, org_id: str, stream: bool) -> None:
    client = get_service_client()
    print(f"🧠  org={org_id}  stream={stream}")
    print(f"💬  task: {message}\n")

    async for event in execute_task(
        user_message=message,
        org_id=org_id,
        db_client=client,
        stream=stream,
    ):
        if isinstance(event, SearchingEvent):
            print(f"🔎 searching: {event.query}")
        elif isinstance(event, SearchedEvent):
            print(f"   → {event.hit_count} hits")
        elif isinstance(event, SourcesEvent):
            print(f"\n📚 sources ({len(event.sources)}):")
            for s in event.sources:
                tag = s["document_name"]
                if s.get("page_number") is not None:
                    tag += f", p.{s['page_number']}"
                print(f"   • {tag}")
            print()
        elif isinstance(event, TokenEvent):
            print(event.text, end="", flush=True)
        elif isinstance(event, FinalEvent):
            if not stream:
                # Non-stream path: print the whole thing now.
                print("─── Final output ──────────────────────────────")
                print(event.text)
            print(f"\n\n✓ done — tool_calls={event.tool_calls_made}")
        elif isinstance(event, ErrorEvent):
            print(f"\n❌ error: {event.message}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--org-id", required=True)
    p.add_argument("--message", required=True)
    p.add_argument("--stream", action="store_true", help="Print tokens as they arrive")
    args = p.parse_args()
    asyncio.run(_run(args.message, args.org_id, args.stream))


if __name__ == "__main__":
    main()
