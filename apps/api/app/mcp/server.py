"""MCP Streamable HTTP transport (Production Roadmap 2.9).

We implement JSON-RPC 2.0 over a single POST endpoint at `/mcp`. This is the
2025-03 Streamable HTTP transport — the same shape every modern MCP client
uses for remote servers (Claude.ai, Claude Desktop "URL" connections, the
Anthropic API's `mcp_servers` config, ChatGPT, Cursor, Cline, etc.).

We deliberately do NOT depend on the `mcp` Python SDK. That SDK is opinionated
about its own stdio transport, and adapting it to FastAPI/Streamable HTTP
adds dead weight. The wire spec is small enough that a hand-rolled handler is
both simpler and easier to audit.

Auth: `Authorization: Bearer cb_live_...` — same API keys as the public REST
API. Verified per request; the verified key seeds an ApiKeyContext that
scopes every tool call to one org.

Methods supported:
  * initialize             — handshake; returns capabilities + serverInfo
  * notifications/initialized — fire-and-forget acknowledgement
  * tools/list             — advertise tools/{name,description,inputSchema}
  * tools/call             — invoke a tool, return its content
  * ping                   — liveness check

Notifications (no id):
  * notifications/cancelled — accepted and ignored (our tools don't honour
    cancel mid-execution; the HTTP request bounds the lifetime anyway)

Every invocation is audited to `mcp_tool_invocations` so admins can see what
external AI is doing in their workspace.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.database import get_service_client
from app.mcp.tools import TOOL_HANDLERS, TOOL_SCHEMAS
from app.services.api_keys import ApiKeyContext, verify_key

log = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp"])

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "NirnayaIQ", "version": "1.0"}


# ── Auth ───────────────────────────────────────────────────────────────────


async def _auth(authorization: str | None = Header(default=None)) -> ApiKeyContext:
    try:
        return await verify_key(authorization)
    except ValueError as exc:
        log.info("mcp_auth_rejected reason=%s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


# ── JSON-RPC plumbing ─────────────────────────────────────────────────────


def _ok(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


# ── Tool invocation + audit ───────────────────────────────────────────────


async def _audit(
    *,
    ctx: ApiKeyContext,
    tool_name: str,
    arguments: dict[str, Any],
    status_str: str,
    error: str | None,
    result_text: str | None,
    latency_ms: int,
) -> None:
    """Append-only invocation log. Best-effort — failure here must never
    block the tool response."""
    svc = get_service_client()
    row = {
        "org_id": ctx.org_id,
        "api_key_id": ctx.id,
        "tool_name": tool_name,
        "arguments": arguments,
        "result_summary": (result_text or "")[:2000],
        "status": status_str,
        "error": (error or "")[:500] or None,
        "latency_ms": latency_ms,
        "created_at": datetime.now(UTC).isoformat(),
    }
    try:
        await asyncio.to_thread(
            lambda: svc.table("mcp_tool_invocations").insert(row).execute()
        )
    except Exception as exc:
        log.warning("mcp_audit_failed: %s", exc)


async def _dispatch_tool_call(
    *, ctx: ApiKeyContext, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return {
            "isError": True,
            "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
        }
    start = time.monotonic()
    text: str | None = None
    err: str | None = None
    try:
        text = await handler(arguments or {}, ctx)
    except ValueError as exc:
        err = str(exc)
    except PermissionError as exc:
        err = f"permission_denied: {exc}"
    except Exception as exc:
        log.exception("mcp_tool_failed name=%s", name)
        err = f"internal_error: {exc}"
    latency_ms = int((time.monotonic() - start) * 1000)
    await _audit(
        ctx=ctx,
        tool_name=name,
        arguments=arguments or {},
        status_str="ok" if err is None else "error",
        error=err,
        result_text=text,
        latency_ms=latency_ms,
    )
    if err is not None:
        return {
            "isError": True,
            "content": [{"type": "text", "text": err}],
        }
    return {
        "isError": False,
        "content": [{"type": "text", "text": text or ""}],
    }


# ── Method dispatch ───────────────────────────────────────────────────────


async def _handle_message(
    *, ctx: ApiKeyContext, msg: dict[str, Any]
) -> dict[str, Any] | None:
    """Returns a JSON-RPC response dict, or None for notifications."""
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    # Notifications carry no id and expect no response.
    is_notification = "id" not in msg

    if method == "initialize":
        return _ok(
            req_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": (
                    "NirnayaIQ exposes the organisation's knowledge base as a "
                    "set of tools. Call `search_company_knowledge` for any "
                    "question that might be answered by internal documents."
                ),
            },
        )

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None  # nothing to send back

    if method == "ping":
        return _ok(req_id, {})

    if method == "tools/list":
        return _ok(req_id, {"tools": TOOL_SCHEMAS})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not name:
            return _err(req_id, -32602, "tools/call requires 'name'")
        if not isinstance(arguments, dict):
            return _err(req_id, -32602, "tools/call 'arguments' must be an object")
        result = await _dispatch_tool_call(
            ctx=ctx, name=name, arguments=arguments
        )
        return _ok(req_id, result)

    # Unknown method — only return an error response if this isn't a notification.
    if is_notification:
        return None
    return _err(req_id, -32601, f"Method not found: {method}")


# ── HTTP endpoint ─────────────────────────────────────────────────────────


@router.post("")
async def mcp_post(
    request: Request,
    ctx: ApiKeyContext = Depends(_auth),
) -> Any:
    """Single endpoint that accepts a JSON-RPC message (or batch).

    Returns:
      * A JSON-RPC response object for a request
      * 204 No Content for a notification (no body returned)
      * A JSON array of responses for a batch with at least one request
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Body must be valid JSON.",
        ) from exc

    if isinstance(body, list):
        # Batch: handle each, suppress None responses (notifications).
        responses = await asyncio.gather(
            *(_handle_message(ctx=ctx, msg=m) for m in body if isinstance(m, dict)),
            return_exceptions=False,
        )
        out = [r for r in responses if r is not None]
        if not out:
            return _no_content()
        return out

    if not isinstance(body, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Body must be a JSON-RPC object or array.",
        )

    if body.get("jsonrpc") != "2.0":
        return _err(body.get("id"), -32600, "Invalid Request: jsonrpc must be '2.0'")

    response = await _handle_message(ctx=ctx, msg=body)
    if response is None:
        return _no_content()
    return response


@router.get("")
async def mcp_get(
    ctx: ApiKeyContext = Depends(_auth),
) -> dict[str, Any]:
    """Server-initiated stream endpoint (Streamable HTTP).

    Returns a no-op JSON because we have no server-pushed events to send
    (tool calls are always client-initiated). Some clients probe GET to
    confirm the endpoint speaks MCP; respond with metadata so probes
    succeed without spinning up an SSE channel we'd never use.
    """
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "serverInfo": SERVER_INFO,
        "transport": "streamable-http",
        "tools": [t["name"] for t in TOOL_SCHEMAS],
    }


def _no_content() -> Any:
    from fastapi import Response

    return Response(status_code=status.HTTP_204_NO_CONTENT)
