"""MCP tool definitions + dispatcher (Production Roadmap 2.9).

Each tool is a thin adapter onto an existing FastAPI service so we don't
duplicate retrieval / ingestion / agent logic. Adding a tool means:
  1. Define schema in TOOL_SCHEMAS (drives `tools/list` advertising).
  2. Add a handler in TOOL_HANDLERS that returns plain text or JSON.

Return shape rule:
  Handlers return a string (formatted for the LLM consumer) OR a dict that
  gets json.dumps'd. The MCP server wraps either into a content array of
  text blocks per the spec. We deliberately do not return structured
  tool-result types — most MCP clients today only render text content.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from app.database import get_service_client
from app.services.api_keys import ApiKeyContext
from app.services.retrieval.hybrid_search import hybrid_search

log = logging.getLogger(__name__)


# ── Tool schemas (advertised in tools/list) ────────────────────────────────


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "search_company_knowledge",
        "description": (
            "Search the organisation's internal knowledge base for documents, "
            "passages, and citations relevant to a query. Returns ranked "
            "passages with source document names and excerpt snippets. Use "
            "this for any question about company policies, processes, product "
            "details, or internal context."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search query.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of passages to return (1-20). Default 8.",
                    "minimum": 1,
                    "maximum": 20,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_documents",
        "description": (
            "List documents in the organisation's knowledge base. Use this to "
            "discover what content exists before searching. Returns document "
            "names, types, and creation dates."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of documents to return (1-100). Default 25.",
                    "minimum": 1,
                    "maximum": 100,
                },
                "status": {
                    "type": "string",
                    "description": "Filter by processing status (e.g. 'ready', 'processing', 'failed').",
                },
            },
        },
    },
    {
        "name": "get_document_summary",
        "description": (
            "Fetch the auto-generated summary and table of contents for a "
            "single document by id. Use this when a search result references "
            "a document and the caller needs the high-level shape before "
            "drilling into chunks."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "description": "UUID of the document."},
            },
            "required": ["document_id"],
        },
    },
    {
        "name": "list_agents",
        "description": (
            "List available Contexo background agents that can be triggered "
            "via the run_agent tool. Returns each agent's type, description, "
            "and input schema."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "run_agent",
        "description": (
            "Trigger a Contexo background agent. Returns the agent_run_id; "
            "the caller polls the company's web UI to inspect the result, or "
            "the agent posts its output to the configured channel."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_type": {
                    "type": "string",
                    "description": "Agent type to run (use list_agents to discover).",
                },
                "input": {
                    "type": "object",
                    "description": "Agent-specific input payload. See list_agents for schema.",
                },
            },
            "required": ["agent_type", "input"],
        },
    },
]


# ── Handlers ───────────────────────────────────────────────────────────────


async def _t_search(args: dict[str, Any], ctx: ApiKeyContext) -> str:
    query = (args.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    limit = int(args.get("limit") or 8)
    limit = max(1, min(limit, 20))

    results = await hybrid_search(
        org_id=ctx.org_id,
        query=query,
        k=limit,
    )
    if not results:
        return f'No results for "{query}".'

    lines: list[str] = [f"## {len(results)} results for: {query}", ""]
    for i, r in enumerate(results, 1):
        doc_name = r.get("document_name") or "Unknown"
        page = r.get("page_number")
        section = r.get("section_heading") or ""
        snippet = (r.get("content") or r.get("excerpt") or "")[:600]
        loc = f", p.{page}" if page else ""
        if section:
            loc += f" — §{section}"
        lines.append(f"### {i}. {doc_name}{loc}")
        lines.append(snippet)
        lines.append("")
    return "\n".join(lines).strip()


async def _t_list_documents(args: dict[str, Any], ctx: ApiKeyContext) -> str:
    import asyncio

    limit = int(args.get("limit") or 25)
    limit = max(1, min(limit, 100))
    status_filter = args.get("status")
    svc = get_service_client()

    def _q() -> list[dict[str, Any]]:
        q = (
            svc.table("documents")
            .select("id, name, file_type, status, chunk_count, created_at")
            .eq("org_id", ctx.org_id)
            .order("created_at", desc=True)
            .limit(limit)
        )
        if status_filter:
            q = q.eq("status", status_filter)
        return q.execute().data or []

    docs = await asyncio.to_thread(_q)
    if not docs:
        return "No documents in this workspace."
    lines = [f"## {len(docs)} documents", ""]
    for d in docs:
        lines.append(
            f"- `{d['id']}` · **{d['name']}** ({d.get('file_type') or '?'}) · "
            f"{d.get('chunk_count') or 0} chunks · {d.get('status') or '?'}"
        )
    return "\n".join(lines)


async def _t_get_document_summary(args: dict[str, Any], ctx: ApiKeyContext) -> str:
    import asyncio

    doc_id = (args.get("document_id") or "").strip()
    if not doc_id:
        raise ValueError("document_id is required")
    svc = get_service_client()

    def _q() -> dict[str, Any] | None:
        res = (
            svc.table("documents")
            .select("id, name, file_type, summary, toc, chunk_count, status, created_at")
            .eq("id", doc_id)
            .eq("org_id", ctx.org_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    doc = await asyncio.to_thread(_q)
    if not doc:
        return f"Document `{doc_id}` not found in this workspace."
    parts = [
        f"# {doc['name']}",
        f"- Type: {doc.get('file_type') or '?'}",
        f"- Status: {doc.get('status') or '?'}",
        f"- Chunks: {doc.get('chunk_count') or 0}",
        "",
    ]
    if doc.get("summary"):
        parts += ["## Summary", doc["summary"], ""]
    if doc.get("toc"):
        parts += ["## Table of contents", json.dumps(doc["toc"], indent=2)[:2000]]
    return "\n".join(parts)


async def _t_list_agents(_args: dict[str, Any], _ctx: ApiKeyContext) -> str:
    from app.services.agent_registry import agent_registry_public_view

    view = agent_registry_public_view()
    return json.dumps(view, indent=2)[:8000]


async def _t_run_agent(args: dict[str, Any], ctx: ApiKeyContext) -> str:
    import asyncio
    import uuid

    from app.services.agent_registry import (
        AGENT_REGISTRY,
        AgentInputError,
        dispatch_api_agent,
        precreate_agent_run,
        validate_agent_input,
    )

    agent_type = (args.get("agent_type") or "").strip()
    if not agent_type or agent_type not in AGENT_REGISTRY:
        raise ValueError(
            f"agent_type must be one of: {', '.join(sorted(AGENT_REGISTRY.keys()))}"
        )
    raw_input = args.get("input") or {}
    if not isinstance(raw_input, dict):
        raise ValueError("input must be an object")

    try:
        clean = validate_agent_input(agent_type, raw_input)
    except AgentInputError as exc:
        raise ValueError(str(exc)) from exc

    run_id = str(uuid.uuid4())
    await precreate_agent_run(
        run_id=run_id,
        org_id=ctx.org_id,
        agent_type=agent_type,
        triggered_by="mcp",
        triggered_by_user_id=None,
        input_data={
            **clean,
            "_api_context": {
                "run_id": run_id,
                "webhook_url": None,
                "api_key_id": ctx.id,
            },
        },
    )
    await dispatch_api_agent(
        org_id=ctx.org_id,
        agent_type=agent_type,
        agent_input=clean,
        output_channels=[],
        webhook_url=None,
        api_key_id=ctx.id,
        approval_id=None,
        run_id=run_id,
    )
    return json.dumps(
        {
            "agent_run_id": run_id,
            "status": "running",
            "poll_url": f"/v1/agent-runs/{run_id}",
        }
    )


TOOL_HANDLERS: dict[
    str, Callable[[dict[str, Any], ApiKeyContext], Awaitable[str]]
] = {
    "search_company_knowledge": _t_search,
    "list_documents": _t_list_documents,
    "get_document_summary": _t_get_document_summary,
    "list_agents": _t_list_agents,
    "run_agent": _t_run_agent,
}
