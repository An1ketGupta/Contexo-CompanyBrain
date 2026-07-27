"""Contexo MCP server (Production Roadmap 2.9).

Exposes the company knowledge base as a Model Context Protocol server so any
MCP-aware client (Claude.ai, Claude Desktop, ChatGPT, Cursor, Cline, etc.)
can call "search Contexo" as a native tool.

Mounted at /mcp on the existing FastAPI app. Transport is Streamable HTTP
(the post-2024 MCP transport that replaces stdio+SSE for remote servers).
"""
from app.mcp.server import router as router  # re-export

__all__ = ["router"]
