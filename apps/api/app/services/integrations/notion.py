"""Notion integration (Day 14 / #87).

Notion's OAuth gives a long-lived bot token per workspace. The polling cron
walks each selected page, checks last_edited_time against our last_synced_at,
and re-ingests changed pages. We extract block text via the public REST
API rather than the official SDK so we don't pull yet another dep.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import get_settings
from app.database import get_service_client
from app.services.integrations.text_ingest import upsert_external_document

log = logging.getLogger(__name__)

SOURCE_TAG = "notion"
_NOTION_API = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"

# Block types we extract text from. The list mirrors what users actually
# write in docs (paragraphs, lists, headings, quotes, callouts) and skips
# embeds/databases/images that don't contribute prose context.
_TEXT_BLOCK_TYPES = {
    "paragraph",
    "heading_1",
    "heading_2",
    "heading_3",
    "bulleted_list_item",
    "numbered_list_item",
    "to_do",
    "toggle",
    "quote",
    "callout",
    "code",
}


# ── OAuth ───────────────────────────────────────────────────────────────────

def build_auth_url(*, state: str) -> str:
    settings = get_settings()
    params = {
        "client_id": settings.notion_client_id,
        "response_type": "code",
        "owner": "user",
        "redirect_uri": settings.notion_oauth_redirect_uri,
        "state": state,
    }
    return f"https://api.notion.com/v1/oauth/authorize?{urlencode(params)}"


async def exchange_code(*, code: str) -> dict[str, Any]:
    settings = get_settings()
    creds = f"{settings.notion_client_id}:{settings.notion_client_secret}".encode("utf-8")
    auth = base64.b64encode(creds).decode("ascii")
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(
            f"{_NOTION_API}/oauth/token",
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/json",
                "Notion-Version": _NOTION_VERSION,
            },
            json={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.notion_oauth_redirect_uri,
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Notion token exchange failed: {resp.status_code} {resp.text[:200]}")
    return resp.json()


async def store_credentials(*, org_id: str, user_id: str, token_payload: dict[str, Any]) -> None:
    svc = get_service_client()
    row = {
        "org_id": org_id,
        "connected_by": user_id,
        "access_token": token_payload["access_token"],
        "workspace_id": token_payload.get("workspace_id") or "",
        "workspace_name": token_payload.get("workspace_name"),
        "bot_id": token_payload.get("bot_id"),
    }

    def _run() -> None:
        existing = (
            svc.table("notion_integrations").select("id").eq("org_id", org_id)
            .maybe_single().execute()
        )
        if existing and existing.data:
            svc.table("notion_integrations").update(row).eq("id", existing.data["id"]).execute()
        else:
            svc.table("notion_integrations").insert(row).execute()

    await asyncio.to_thread(_run)


async def disconnect(*, org_id: str) -> None:
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("notion_integrations").delete().eq("org_id", org_id).execute()
    )


# ── Page selection ──────────────────────────────────────────────────────────

async def list_accessible_pages(*, org_id: str, query: str | None = None) -> list[dict[str, Any]]:
    """Search the workspace for pages the integration has access to.

    The user picks pages from this list in the UI. We don't sync the entire
    workspace by default — that gets noisy fast for org wikis.
    """
    token = await _access_token(org_id)
    if not token:
        return []
    body: dict[str, Any] = {
        "filter": {"value": "page", "property": "object"},
        "page_size": 50,
    }
    if query:
        body["query"] = query
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(
            f"{_NOTION_API}/search",
            headers=_headers(token),
            json=body,
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Notion search failed: {resp.status_code}")
    results = resp.json().get("results", [])
    out: list[dict[str, Any]] = []
    for r in results:
        out.append(
            {
                "id": r["id"],
                "title": _page_title(r),
                "url": r.get("url"),
                "last_edited_time": r.get("last_edited_time"),
            }
        )
    return out


async def update_selected_pages(
    *, org_id: str, pages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    svc = get_service_client()
    # Sanitize — only keep the fields the UI surfaces.
    normalized = [
        {"id": p["id"], "title": p.get("title") or "Untitled"} for p in pages if p.get("id")
    ]
    await asyncio.to_thread(
        lambda: svc.table("notion_integrations")
        .update({"selected_pages": normalized}).eq("org_id", org_id).execute()
    )
    return normalized


# ── Polling sync ────────────────────────────────────────────────────────────

async def poll_all_integrations() -> dict[str, Any]:
    svc = get_service_client()
    rows = await asyncio.to_thread(
        lambda: svc.table("notion_integrations").select("org_id").execute()
    )
    synced = 0
    errors = 0
    for row in rows.data or []:
        try:
            await sync_org(org_id=row["org_id"])
            synced += 1
        except Exception as exc:
            errors += 1
            log.warning("notion_sync_failed", org_id=row["org_id"], error=str(exc))
    return {"synced": synced, "errors": errors}


async def sync_org(*, org_id: str) -> dict[str, Any]:
    svc = get_service_client()
    integ = await asyncio.to_thread(
        lambda: svc.table("notion_integrations")
        .select("access_token, selected_pages, last_synced_at, connected_by")
        .eq("org_id", org_id).maybe_single().execute()
    )
    if not integ or not integ.data:
        return {"status": "no-integration"}
    token = integ.data["access_token"]
    selected = integ.data.get("selected_pages") or []
    last_synced = integ.data.get("last_synced_at") or "1970-01-01T00:00:00Z"

    ingested = 0
    for page in selected:
        page_id = page["id"]
        try:
            page_data = await _fetch_page(token, page_id)
            edited = page_data.get("last_edited_time") or ""
            if edited and edited <= last_synced:
                continue  # nothing new
            text = await _extract_page_text(token, page_id)
            if not text.strip():
                continue
            doc_id = await upsert_external_document(
                org_id=org_id,
                source=SOURCE_TAG,
                external_id=page_id,
                name=page.get("title") or _page_title(page_data) or "Notion page",
                file_type="md",
                user_id=integ.data.get("connected_by"),
            )
            import inngest
            from app.inngest.client import get_inngest_client
            client = get_inngest_client()
            await client.send(
                inngest.Event(
                    name="doc/process-text",
                    data={"doc_id": doc_id, "org_id": org_id, "text": text},
                    id=f"notion-{doc_id}-{edited}",
                )
            )
            ingested += 1
        except Exception as exc:
            log.warning("notion_page_sync_failed", page_id=page_id, error=str(exc))

    await asyncio.to_thread(
        lambda: svc.table("notion_integrations")
        .update({"last_synced_at": datetime.now(timezone.utc).isoformat()})
        .eq("org_id", org_id).execute()
    )
    return {"status": "ok", "ingested": ingested}


# ── Low-level helpers ──────────────────────────────────────────────────────

async def _access_token(org_id: str) -> str | None:
    svc = get_service_client()
    integ = await asyncio.to_thread(
        lambda: svc.table("notion_integrations").select("access_token").eq("org_id", org_id)
        .maybe_single().execute()
    )
    return (integ.data or {}).get("access_token") if integ else None


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": _NOTION_VERSION,
    }


async def _fetch_page(token: str, page_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.get(f"{_NOTION_API}/pages/{page_id}", headers=_headers(token))
    if resp.status_code != 200:
        raise RuntimeError(f"page fetch failed: {resp.status_code}")
    return resp.json()


async def _extract_page_text(token: str, page_id: str) -> str:
    """Walk top-level blocks, extract plain text. We deliberately don't
    recurse — Notion's API would let us, but at the cost of N+1 requests
    per page, and for V1 the top-level prose is plenty of context."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        resp = await client.get(
            f"{_NOTION_API}/blocks/{page_id}/children",
            params={"page_size": "100"},
            headers=_headers(token),
        )
    if resp.status_code != 200:
        raise RuntimeError(f"blocks fetch failed: {resp.status_code}")
    blocks = resp.json().get("results", [])

    parts: list[str] = []
    for block in blocks:
        btype = block.get("type")
        if btype not in _TEXT_BLOCK_TYPES:
            continue
        rich = (block.get(btype) or {}).get("rich_text") or []
        line = "".join(rt.get("plain_text") or "" for rt in rich).strip()
        if line:
            parts.append(line)
    return "\n".join(parts)


def _page_title(page: dict[str, Any]) -> str:
    props = page.get("properties") or {}
    for v in props.values():
        if v.get("type") == "title":
            rich = v.get("title") or []
            t = "".join(rt.get("plain_text") or "" for rt in rich).strip()
            if t:
                return t
    return "Untitled"


# ── Write surface (Agent Day 4): create a page under a parent ───────────────

# Notion caps a single rich_text run at 2000 characters. Anything longer
# becomes multiple paragraph blocks. We don't try to detect headings or
# preserve markdown structure — the chat output is plain prose, and a faithful
# 1:1 dump beats a half-formatted render that misplaces emphasis.
_NOTION_TEXT_RUN_CAP = 2000


def _text_to_blocks(text: str) -> list[dict[str, Any]]:
    """Convert plain text → Notion paragraph blocks.

    Each blank-line-separated paragraph becomes one block. Paragraphs longer
    than 2000 chars get split (Notion's per-run limit), with the splits kept
    inside one block via multiple rich_text runs so the rendered paragraph
    stays visually intact.
    """
    paragraphs = [p.strip() for p in (text or "").split("\n\n") if p.strip()]
    if not paragraphs:
        # Notion requires at least one block on page create; an empty page is
        # a footgun on the API side. Insert a single empty paragraph.
        return [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": []},
            }
        ]
    out: list[dict[str, Any]] = []
    for para in paragraphs:
        runs: list[dict[str, Any]] = []
        for i in range(0, len(para), _NOTION_TEXT_RUN_CAP):
            chunk = para[i : i + _NOTION_TEXT_RUN_CAP]
            runs.append({"type": "text", "text": {"content": chunk}})
        out.append(
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": runs},
            }
        )
    return out


async def create_page(
    *,
    org_id: str,
    parent_page_id: str,
    title: str,
    content: str,
) -> dict[str, Any]:
    """Create a Notion page under `parent_page_id` with the given content.

    The bot needs to have been shared on the parent page in Notion — Notion
    doesn't let an integration write to a page it can't see. If that's not
    the case the API returns 404 "Could not find page with ID", which we
    translate to PermissionError so the worker doesn't waste retries.
    """
    token = await _access_token(org_id)
    if not token:
        raise PermissionError("notion_not_connected")

    blocks = _text_to_blocks(content)
    payload: dict[str, Any] = {
        "parent": {"page_id": parent_page_id},
        "properties": {
            "title": {
                "title": [{"type": "text", "text": {"content": (title or "Untitled")[:200]}}]
            }
        },
        # Notion caps children on create at 100 blocks. For a generated email
        # / SOP / announcement that's plenty; if we ever push longer outputs
        # we'll need to follow up with PATCH /blocks/{id}/children.
        "children": blocks[:100],
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        resp = await client.post(
            f"{_NOTION_API}/pages",
            headers=_headers(token),
            json=payload,
        )

    if resp.status_code in (401, 403):
        raise PermissionError("notion_token_revoked")
    if resp.status_code == 404:
        # Bot can't see this parent page — the user needs to share the page
        # with the integration. Surface a distinct code so the UI can prompt.
        raise PermissionError("notion_parent_not_shared")
    if resp.status_code >= 400:
        raise RuntimeError(f"notion_create_failed: {resp.status_code} {resp.text[:200]}")

    body = resp.json()
    return {
        "page_id": body.get("id"),
        "url": body.get("url"),
        "title": title,
    }


async def list_write_targets(*, org_id: str, query: str | None = None) -> list[dict[str, Any]]:
    """Return pages the integration can use as a parent for new pages.

    This is just `list_accessible_pages` — Notion's permission model means
    any page the bot can read, it can also create children under. We keep
    the function name distinct from `list_accessible_pages` because the
    UI semantics differ: this list is the parent picker for "create page",
    not the doc-selection list for the sync flow.
    """
    return await list_accessible_pages(org_id=org_id, query=query)
