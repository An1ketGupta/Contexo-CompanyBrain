"""Confluence Cloud integration via Atlassian OAuth 3LO.

Atlassian's OAuth is awkward in two ways worth flagging:

  1. After the code exchange we DON'T have the per-Confluence-site base URL
     in hand. We have to call GET /oauth/token/accessible-resources to find
     which Atlassian "clouds" (sites) the user authorised — the resulting
     cloudId is what goes into every subsequent API URL.

  2. Per-site API URLs look like:
         https://api.atlassian.com/ex/confluence/{cloudId}/wiki/api/v2/...
     NOT https://{site}.atlassian.net/wiki/...  — the latter requires the
     user's session cookie, not an OAuth bearer.

`resources` JSONB shape:
    [
      {"cloud_id": "abc", "cloud_name": "Acme",
       "space_id":  "1234", "space_key": "ENG",
       "space_name": "Engineering"},
    ]

Delta sync: `sync_cursor` holds an ISO timestamp; per cycle we ask
    GET /wiki/api/v2/spaces/{space-id}/pages?limit=50&sort=-modified-date
and stop walking once we see a page older than the cursor. The v2 endpoint
doesn't support a server-side `since` filter, so client-side termination is
the cheapest option that still avoids re-ingesting an entire space.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup

from app.config import get_settings
from app.services.integrations import _unified

log = logging.getLogger(__name__)

PROVIDER = "confluence"

_ATLASSIAN_AUTH = "https://auth.atlassian.com"
_ATLASSIAN_API = "https://api.atlassian.com"

_SCOPES = [
    "read:confluence-content.all",
    "read:confluence-content.summary",
    "read:confluence-space.summary",
    "read:confluence-content-permission",
    "search:confluence",
    "offline_access",
]

# Attachment MIME → our parser label. Anything missing here is skipped, which
# is fine: the lean-v1 deal is page bodies + the document attachments people
# upload to wiki pages, not images / videos / archives.
_ATTACHMENT_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "text/plain": "txt",
    "text/markdown": "md",
}


_unified.register_refresh(
    PROVIDER,
    token_url=f"{_ATLASSIAN_AUTH}/oauth/token",
    client_id_attr="atlassian_client_id",
    client_secret_attr="atlassian_client_secret",
)


# ── OAuth ───────────────────────────────────────────────────────────────────


def build_auth_url(*, state: str) -> str:
    settings = get_settings()
    params = {
        "audience": "api.atlassian.com",
        "client_id": settings.atlassian_client_id,
        "scope": " ".join(_SCOPES),
        "redirect_uri": settings.atlassian_oauth_redirect_uri,
        "state": state,
        "response_type": "code",
        # `consent` matches Atlassian's docs for installs that need refresh
        # tokens — without it offline_access is sometimes dropped on re-auth.
        "prompt": "consent",
    }
    return f"{_ATLASSIAN_AUTH}/authorize?{urlencode(params)}"


async def exchange_code(*, code: str) -> dict[str, Any]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(
            f"{_ATLASSIAN_AUTH}/oauth/token",
            json={
                "grant_type": "authorization_code",
                "client_id": settings.atlassian_client_id,
                "client_secret": settings.atlassian_client_secret,
                "code": code,
                "redirect_uri": settings.atlassian_oauth_redirect_uri,
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"atlassian_token_exchange_failed: {resp.status_code} {resp.text[:200]}"
        )
    return resp.json()


async def _accessible_resources(token: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.get(
            f"{_ATLASSIAN_API}/oauth/token/accessible-resources",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
    if resp.status_code != 200:
        return []
    return list(resp.json() or [])


async def store_credentials(
    *, org_id: str, user_id: str, token_payload: dict[str, Any]
) -> None:
    expires_in = int(token_payload.get("expires_in") or 3600)
    expiry = datetime.now(UTC) + timedelta(seconds=expires_in)
    scopes = (token_payload.get("scope") or "").split()
    access_token = token_payload["access_token"]

    # Fetch the user's accessible Confluence sites so the picker UI has
    # something to show without a second round-trip on the next page load.
    clouds = await _accessible_resources(access_token)
    confluence_clouds = [
        {
            "cloud_id": c["id"],
            "name": c.get("name") or c.get("url") or c["id"],
            "url": c.get("url"),
            "scopes": c.get("scopes") or [],
        }
        for c in clouds
        if "confluence" in " ".join(c.get("scopes") or []).lower()
        or any("confluence" in s for s in c.get("scopes") or [])
    ]
    if not confluence_clouds:
        # Fall back to all clouds — Atlassian sometimes returns scopes
        # without a `confluence` substring even when Confluence is reachable.
        confluence_clouds = [
            {"cloud_id": c["id"], "name": c.get("name"), "url": c.get("url")}
            for c in clouds
        ]

    await _unified.upsert_row(
        org_id=org_id,
        provider=PROVIDER,
        connected_by=user_id,
        access_token=access_token,
        refresh_token=token_payload.get("refresh_token"),
        token_expiry=expiry,
        scopes=scopes,
        metadata={"clouds": confluence_clouds},
    )


async def disconnect(*, org_id: str) -> None:
    await _unified.delete_row(org_id=org_id, provider=PROVIDER)


# ── Resource enumeration ────────────────────────────────────────────────────


async def _token_and_row(org_id: str) -> tuple[str, dict[str, Any]] | None:
    settings = get_settings()
    row = await _unified.get_row(org_id=org_id, provider=PROVIDER)
    if not row:
        return None
    token = await _unified.ensure_fresh_token(row, settings=settings)
    return token, row


def _cloud_base(cloud_id: str) -> str:
    return f"{_ATLASSIAN_API}/ex/confluence/{cloud_id}/wiki"


async def list_spaces(*, org_id: str, cloud_id: str) -> list[dict[str, Any]]:
    """Return spaces in a given Confluence cloud that the integration sees.

    Confluence v2 paginates with `_links.next`; for the picker UI we just
    take the first 100, which is more than enough for a one-time install.
    """
    found = await _token_and_row(org_id)
    if not found:
        return []
    token, _ = found
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.get(
            f"{_cloud_base(cloud_id)}/api/v2/spaces",
            params={"limit": "100"},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
    if resp.status_code != 200:
        log.warning("confluence_list_spaces_failed %s %s", resp.status_code, resp.text[:200])
        return []
    return [
        {
            "space_id": s["id"],
            "space_key": s.get("key"),
            "name": s.get("name") or s.get("key") or s["id"],
            "type": s.get("type"),
        }
        for s in (resp.json().get("results") or [])
    ]


async def update_resources(
    *, org_id: str, resources: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for r in resources or []:
        if not r.get("cloud_id") or not r.get("space_id"):
            continue
        cleaned.append(
            {
                "cloud_id": str(r["cloud_id"]),
                "cloud_name": (r.get("cloud_name") or "")[:200],
                "space_id": str(r["space_id"]),
                "space_key": (r.get("space_key") or "")[:50],
                "space_name": (r.get("space_name") or "")[:200],
            }
        )

    from app.database import get_service_client
    row = await _unified.get_row(org_id=org_id, provider=PROVIDER)
    if not row:
        raise RuntimeError("confluence_not_connected")
    svc = get_service_client()
    import asyncio as _asyncio
    await _asyncio.to_thread(
        lambda: svc.table("integrations")
        .update({"resources": cleaned})
        .eq("id", row["id"])
        .execute()
    )
    return cleaned


# ── Polling sync ────────────────────────────────────────────────────────────


async def poll_all_integrations() -> dict[str, Any]:
    rows = await _unified.list_org_rows(provider=PROVIDER)
    synced, errors = 0, 0
    for row in rows:
        try:
            await sync_org(org_id=row["org_id"])
            synced += 1
        except Exception as exc:
            errors += 1
            log.warning("confluence_sync_failed org=%s err=%s", row["org_id"], exc)
            await _unified.mark_error(
                org_id=row["org_id"], provider=PROVIDER, error=str(exc)
            )
    return {"synced": synced, "errors": errors}


async def sync_org(*, org_id: str) -> dict[str, Any]:
    settings = get_settings()
    row = await _unified.get_row(org_id=org_id, provider=PROVIDER)
    if not row:
        return {"status": "no-integration"}

    resources = row.get("resources") or []
    if not resources:
        return {"status": "no-resources"}

    token = await _unified.ensure_fresh_token(row, settings=settings)
    since = row.get("sync_cursor") or "1970-01-01T00:00:00Z"

    total_ingested = 0
    latest_modified = since

    for res in resources:
        cloud_id = res.get("cloud_id")
        space_id = res.get("space_id")
        if not cloud_id or not space_id:
            continue
        try:
            ingested, newest = await _sync_space(
                org_id=org_id,
                user_id=row.get("connected_by"),
                token=token,
                cloud_id=cloud_id,
                space_id=space_id,
                space_name=res.get("space_name"),
                since=since,
            )
            total_ingested += ingested
            if newest > latest_modified:
                latest_modified = newest
        except Exception as exc:
            log.warning(
                "confluence_space_sync_failed org=%s space=%s err=%s",
                org_id, space_id, exc,
            )

    await _unified.mark_synced(
        org_id=org_id, provider=PROVIDER, cursor=latest_modified
    )
    return {"status": "ok", "ingested": total_ingested}


async def _sync_space(
    *,
    org_id: str,
    user_id: str | None,
    token: str,
    cloud_id: str,
    space_id: str,
    space_name: str | None,
    since: str,
) -> tuple[int, str]:
    """Walk pages sorted newest-first; stop when we hit `since`."""
    ingested = 0
    newest = since
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    # Pull up to 200 pages per tick — large enough for most spaces' churn,
    # small enough not to starve other orgs.
    url = (
        f"{_cloud_base(cloud_id)}/api/v2/spaces/{space_id}/pages"
        f"?limit=50&sort=-modified-date"
    )
    pages_walked = 0
    MAX_PAGES = 4

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        while url and pages_walked < MAX_PAGES:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 401:
                raise RuntimeError("confluence_token_revoked")
            if resp.status_code != 200:
                raise RuntimeError(
                    f"confluence_pages_failed: {resp.status_code} {resp.text[:200]}"
                )
            payload = resp.json()
            results = payload.get("results") or []
            should_stop = False
            for page in results:
                modified = page.get("version", {}).get("createdAt") or page.get("createdAt") or ""
                if modified and modified > newest:
                    newest = modified
                if modified and modified <= since:
                    # Sorted newest-first, so the first one we see <= since
                    # means the rest are too — stop walking.
                    should_stop = True
                    break
                try:
                    await _ingest_page(
                        org_id=org_id,
                        user_id=user_id,
                        token=token,
                        cloud_id=cloud_id,
                        page=page,
                        space_name=space_name,
                    )
                    ingested += 1
                except Exception as exc:
                    log.warning(
                        "confluence_page_ingest_failed page=%s err=%s",
                        page.get("id"), exc,
                    )

            if should_stop:
                break
            next_link = (payload.get("_links") or {}).get("next")
            url = (
                f"{_ATLASSIAN_API}{next_link}" if next_link and next_link.startswith("/")
                else next_link
            )
            pages_walked += 1

    return ingested, newest


# Confluence storage format is XHTML with embedded macros (<ac:structured-macro
# …>). For embedding we want flat prose. BeautifulSoup gives us text-only
# extraction; the regex below collapses runs of whitespace introduced by
# stripped block elements.
_WS_RE = re.compile(r"\s+")


def _storage_to_text(storage_xml: str) -> str:
    if not storage_xml:
        return ""
    # `lxml-xml` parser balks on the ac:/ri: namespaces without prefixes —
    # use the lenient `html.parser` and let BS4 ignore unknown elements.
    soup = BeautifulSoup(storage_xml, "html.parser")
    # Drop common macros that don't contribute reading prose.
    for tag in soup.find_all(["script", "style", "ac:placeholder"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    text = html.unescape(text)
    # Collapse repeated blank lines but preserve paragraph breaks.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def _ingest_page(
    *,
    org_id: str,
    user_id: str | None,
    token: str,
    cloud_id: str,
    page: dict[str, Any],
    space_name: str | None,
) -> None:
    page_id = page["id"]
    title = page.get("title") or "Untitled"
    (
        (page.get("_links") or {}).get("webui")
        or f"/spaces/{page.get('spaceId')}/pages/{page_id}"
    )
    # Fetch body in storage format (Confluence's canonical XHTML).
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        body_resp = await client.get(
            f"{_cloud_base(cloud_id)}/api/v2/pages/{page_id}",
            params={"body-format": "storage"},
            headers=headers,
        )
    if body_resp.status_code != 200:
        log.warning("confluence_body_fetch_failed page=%s s=%s", page_id, body_resp.status_code)
        return
    storage = ((body_resp.json().get("body") or {}).get("storage") or {}).get("value") or ""
    text = _storage_to_text(storage)

    full_title = f"{space_name}: {title}" if space_name else title
    source_url = (page.get("_links") or {}).get("webui")

    if text:
        await _unified.queue_text_ingest(
            org_id=org_id,
            source=PROVIDER,
            external_id=page_id,
            name=full_title,
            file_type="md",
            text=text,
            user_id=user_id,
            source_url=source_url,
        )

    # Attachments: best-effort PDF/DOCX/etc. Skip silently if absent.
    try:
        await _ingest_attachments(
            org_id=org_id,
            user_id=user_id,
            token=token,
            cloud_id=cloud_id,
            page_id=page_id,
            page_title=full_title,
        )
    except Exception as exc:
        log.warning(
            "confluence_attachments_failed page=%s err=%s", page_id, exc
        )


async def _ingest_attachments(
    *,
    org_id: str,
    user_id: str | None,
    token: str,
    cloud_id: str,
    page_id: str,
    page_title: str,
) -> None:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.get(
            f"{_cloud_base(cloud_id)}/api/v2/pages/{page_id}/attachments",
            params={"limit": "20"},
            headers=headers,
        )
    if resp.status_code != 200:
        return
    for att in resp.json().get("results") or []:
        media_type = att.get("mediaType") or ""
        file_type = _ATTACHMENT_TYPES.get(media_type)
        if not file_type:
            continue
        att_id = att["id"]
        name = att.get("title") or f"{page_title} attachment"
        # The attachment download path is stable: /wiki/download/attachments/...
        # Confluence v2 returns it on `_links.download` (a relative URL).
        rel = (att.get("_links") or {}).get("download")
        if not rel:
            continue
        download_url = f"{_cloud_base(cloud_id)}{rel}" if rel.startswith("/") else rel
        try:
            await _unified.queue_binary_ingest(
                org_id=org_id,
                source=PROVIDER,
                external_id=f"att-{att_id}",
                name=name,
                file_type=file_type,
                download_url=download_url,
                auth_header=f"Bearer {token}",
                user_id=user_id,
            )
        except Exception as exc:
            log.warning("confluence_attachment_queue_failed att=%s err=%s", att_id, exc)
