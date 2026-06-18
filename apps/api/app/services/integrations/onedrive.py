"""OneDrive Business + SharePoint integration via Microsoft Graph.

One OAuth install gives us access to both:
    * /me/drive               → connecting admin's OneDrive Business
    * /sites/{id}/drives/{id} → SharePoint document libraries

`resources` JSONB shape in the integrations row:
    [
      {"kind": "drive", "drive_id": "b!xxxx", "name": "OneDrive — Acme"},
      {"kind": "site",  "site_id":  "acme.sharepoint.com,xxx,yyy",
                       "drive_id": "b!yyyy", "name": "Marketing"},
    ]

Delta sync per drive: `/drives/{drive-id}/root/delta?token=...`. Microsoft
hands back a `@odata.deltaLink` we save as the per-drive cursor inside
`metadata.delta_links` (one map keyed by drive_id since one install can sync
multiple drives — the integration-level `sync_cursor` column is too narrow).

Token endpoint: v2.0. Scopes requested at install carry `offline_access` so
we get a refresh_token; Microsoft refreshes return new refresh tokens too,
which `_unified.ensure_fresh_token` already handles.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import get_settings
from app.services.integrations import _unified

log = logging.getLogger(__name__)

PROVIDER = "onedrive"

_GRAPH = "https://graph.microsoft.com/v1.0"
_AUTH_BASE = "https://login.microsoftonline.com"

# Files.Read.All + Sites.Read.All are the minimum to enumerate + read across
# both OneDrive Business and SharePoint document libraries. offline_access is
# required to get a refresh_token (Microsoft v2 endpoint doesn't issue one
# without it).
_SCOPES = [
    "offline_access",
    "Files.Read.All",
    "Sites.Read.All",
    "User.Read",
]

# Map Graph file MIME types (or extensions) to our internal labels for the
# parser. We don't try to ingest OneNote (.onetoc2/.one) — they need a
# separate Graph endpoint and a different parser. Image-only PDFs and CAD
# files fall through to the unsupported branch and are silently skipped.
_SUPPORTED_EXT = {
    "pdf": "pdf",
    "docx": "docx",
    "xlsx": "xlsx",
    "pptx": "pptx",
    "txt": "txt",
    "md": "md",
    "markdown": "md",
}


# Refresh wiring — _unified.ensure_fresh_token() reads this map.
_unified.register_refresh(
    PROVIDER,
    token_url=f"{_AUTH_BASE}/common/oauth2/v2.0/token",
    client_id_attr="microsoft_client_id",
    client_secret_attr="microsoft_client_secret",
)


# ── OAuth ───────────────────────────────────────────────────────────────────


def build_auth_url(*, state: str) -> str:
    """Microsoft v2 consent URL. `tenant=common` lets any work/school account
    consent; admins can pin to a specific tenant via the env var if they
    want to restrict to one Azure AD."""
    settings = get_settings()
    tenant = settings.microsoft_tenant or "common"
    params = {
        "client_id": settings.microsoft_client_id,
        "response_type": "code",
        "redirect_uri": settings.microsoft_oauth_redirect_uri,
        "response_mode": "query",
        "scope": " ".join(_SCOPES),
        "state": state,
        # Force account picker so the admin can't accidentally consent under
        # the wrong identity if they're signed into multiple Microsoft accounts.
        "prompt": "select_account",
    }
    return f"{_AUTH_BASE}/{tenant}/oauth2/v2.0/authorize?{urlencode(params)}"


async def exchange_code(*, code: str) -> dict[str, Any]:
    settings = get_settings()
    tenant = settings.microsoft_tenant or "common"
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(
            f"{_AUTH_BASE}/{tenant}/oauth2/v2.0/token",
            data={
                "client_id": settings.microsoft_client_id,
                "client_secret": settings.microsoft_client_secret,
                "code": code,
                "redirect_uri": settings.microsoft_oauth_redirect_uri,
                "grant_type": "authorization_code",
                "scope": " ".join(_SCOPES),
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"microsoft_token_exchange_failed: {resp.status_code} {resp.text[:200]}"
        )
    return resp.json()


async def store_credentials(
    *, org_id: str, user_id: str, token_payload: dict[str, Any]
) -> None:
    """Persist tokens + the connecting admin's identity so the UI can label
    the install with the right account."""
    expires_in = int(token_payload.get("expires_in") or 3600)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    scopes = (token_payload.get("scope") or "").split()

    # Fetch /me so we can show "OneDrive — alice@acme.com" rather than the
    # raw tenant id in the UI. Best-effort: don't fail the install if /me
    # returns a transient error.
    display: dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            me = await client.get(
                f"{_GRAPH}/me",
                headers={"Authorization": f"Bearer {token_payload['access_token']}"},
            )
        if me.status_code == 200:
            data = me.json()
            display = {
                "user_principal_name": data.get("userPrincipalName"),
                "display_name": data.get("displayName"),
                "tenant_id": data.get("id"),
            }
    except Exception:
        pass

    await _unified.upsert_row(
        org_id=org_id,
        provider=PROVIDER,
        connected_by=user_id,
        access_token=token_payload["access_token"],
        refresh_token=token_payload.get("refresh_token"),
        token_expiry=expiry,
        scopes=scopes,
        metadata={
            "account": display,
            # delta_links holds per-drive `@odata.deltaLink` URLs so a re-sync
            # only pulls files changed since the last cycle. Indexed by drive
            # id because a single install can sync many drives.
            "delta_links": {},
        },
    )


async def disconnect(*, org_id: str) -> None:
    await _unified.delete_row(org_id=org_id, provider=PROVIDER)


# ── Resource enumeration (drive + site picker) ──────────────────────────────


async def _client_for(org_id: str) -> tuple[str, dict[str, Any]] | None:
    settings = get_settings()
    row = await _unified.get_row(org_id=org_id, provider=PROVIDER)
    if not row:
        return None
    token = await _unified.ensure_fresh_token(row, settings=settings)
    return token, row


async def list_personal_drive(*, org_id: str) -> dict[str, Any] | None:
    """The admin's own OneDrive Business — useful as a single 'me' option in
    the picker UI."""
    found = await _client_for(org_id)
    if not found:
        return None
    token, _ = found
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.get(
            f"{_GRAPH}/me/drive",
            headers={"Authorization": f"Bearer {token}"},
        )
    if resp.status_code != 200:
        return None
    drive = resp.json()
    owner = (drive.get("owner") or {}).get("user", {})
    return {
        "kind": "drive",
        "drive_id": drive["id"],
        "name": owner.get("displayName") or "OneDrive",
        "type": drive.get("driveType"),
    }


async def search_sites(*, org_id: str, query: str | None = None) -> list[dict[str, Any]]:
    """Search SharePoint sites the admin can read.

    Empty query returns the tenant's followed sites — most orgs only have a
    handful of canonical sites, so an empty list with `?$filter` would be
    confusing. Passing 'search=*' returns all visible sites which is what
    you usually want for a one-time install picker."""
    found = await _client_for(org_id)
    if not found:
        return []
    token, _ = found
    params = {
        "search": query or "*",
        "$select": "id,displayName,webUrl,siteCollection",
        "$top": "25",
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.get(
            f"{_GRAPH}/sites",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
    if resp.status_code != 200:
        log.warning("sharepoint_search_failed: %s %s", resp.status_code, resp.text[:200])
        return []
    sites = resp.json().get("value") or []
    return [
        {
            "site_id": s["id"],
            "name": s.get("displayName") or s.get("name") or s["id"],
            "url": s.get("webUrl"),
        }
        for s in sites
    ]


async def list_site_drives(*, org_id: str, site_id: str) -> list[dict[str, Any]]:
    found = await _client_for(org_id)
    if not found:
        return []
    token, _ = found
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.get(
            f"{_GRAPH}/sites/{site_id}/drives",
            headers={"Authorization": f"Bearer {token}"},
        )
    if resp.status_code != 200:
        return []
    return [
        {
            "drive_id": d["id"],
            "name": d.get("name") or d["id"],
            "kind": "site",
            "site_id": site_id,
        }
        for d in (resp.json().get("value") or [])
    ]


async def update_resources(
    *, org_id: str, resources: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Replace the selected drive/site list. Sanitised before persisting so
    a malicious payload can't smuggle arbitrary keys into JSONB."""
    cleaned: list[dict[str, Any]] = []
    for r in resources or []:
        kind = r.get("kind")
        drive_id = r.get("drive_id")
        if kind not in ("drive", "site") or not drive_id:
            continue
        cleaned.append(
            {
                "kind": kind,
                "drive_id": drive_id,
                "site_id": r.get("site_id"),
                "name": (r.get("name") or "Drive")[:200],
            }
        )
    svc_row = await _unified.get_row(org_id=org_id, provider=PROVIDER)
    if not svc_row:
        raise RuntimeError("onedrive_not_connected")

    from app.database import get_service_client
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("integrations")
        .update({"resources": cleaned})
        .eq("id", svc_row["id"])
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
            log.warning("onedrive_sync_failed org=%s err=%s", row["org_id"], exc)
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

    access_token = await _unified.ensure_fresh_token(row, settings=settings)
    delta_links: dict[str, str] = (row.get("metadata") or {}).get("delta_links") or {}

    total_ingested = 0
    for res in resources:
        drive_id = res.get("drive_id")
        if not drive_id:
            continue
        try:
            new_link, ingested = await _sync_drive(
                org_id=org_id,
                user_id=row.get("connected_by"),
                access_token=access_token,
                drive_id=drive_id,
                resource=res,
                delta_link=delta_links.get(drive_id),
            )
            delta_links[drive_id] = new_link
            total_ingested += ingested
        except Exception as exc:
            log.warning(
                "onedrive_drive_sync_failed org=%s drive=%s err=%s",
                org_id, drive_id, exc,
            )

    # Persist delta links + mark synced. delta_links live inside metadata so
    # we update via the same JSONB blob instead of using sync_cursor (which
    # is single-valued).
    metadata = dict(row.get("metadata") or {})
    metadata["delta_links"] = delta_links
    await _unified.update_fields(
        org_id=org_id,
        provider=PROVIDER,
        fields={"metadata": metadata},
    )
    await _unified.mark_synced(org_id=org_id, provider=PROVIDER)
    return {"status": "ok", "ingested": total_ingested}


async def _sync_drive(
    *,
    org_id: str,
    user_id: str | None,
    access_token: str,
    drive_id: str,
    resource: dict[str, Any],
    delta_link: str | None,
) -> tuple[str, int]:
    """Walk Graph's delta endpoint for one drive. Returns (newDeltaLink, n_ingested).

    Graph's delta API is paginated via `@odata.nextLink` and terminates with
    `@odata.deltaLink` — that final URL is what we save for the next cycle.
    We never sync the entire drive in one cron tick: if there are many pages,
    nextLink walks them within this tick, but a freshly-installed drive will
    take a few cron cycles to backfill which is fine.
    """
    url = delta_link or f"{_GRAPH}/drives/{drive_id}/root/delta"
    headers = {"Authorization": f"Bearer {access_token}"}

    ingested = 0
    final_delta = delta_link or url
    pages_walked = 0
    # Hard cap pages per tick so an enormous backfill doesn't starve other
    # orgs on the shared cron. 10 pages × 200 items default = ~2000 files.
    MAX_PAGES_PER_TICK = 10

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        while url and pages_walked < MAX_PAGES_PER_TICK:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 401:
                raise RuntimeError("onedrive_token_revoked")
            if resp.status_code == 410:
                # Delta token expired — Graph wants us to re-init from scratch.
                # Drop the saved deltaLink and let the next tick start over.
                log.info("onedrive_delta_expired drive=%s, resetting", drive_id)
                return f"{_GRAPH}/drives/{drive_id}/root/delta", ingested
            if resp.status_code != 200:
                raise RuntimeError(
                    f"onedrive_delta_failed: {resp.status_code} {resp.text[:200]}"
                )
            payload = resp.json()
            for item in payload.get("value") or []:
                if item.get("deleted"):
                    # File was deleted in Graph — we leave our document row
                    # alone (admins can delete from the documents UI). Trying
                    # to track external deletions doubles the schema surface
                    # for little user benefit at v1.
                    continue
                if "folder" in item:
                    continue
                file_info = item.get("file") or {}
                name = item.get("name") or "Untitled"
                ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                file_type = _SUPPORTED_EXT.get(ext)
                if not file_type:
                    # Skip unsupported types silently — admins see the count
                    # of ingested vs total in the UI status panel.
                    continue
                # Graph hands us either a direct download URL (preferred —
                # short-lived, no auth needed) or we hit /content with our
                # bearer token. Prefer the former because it offloads bytes
                # from our own proxy.
                download_url = item.get("@microsoft.graph.downloadUrl")
                auth_header = ""
                if not download_url:
                    download_url = f"{_GRAPH}/drives/{drive_id}/items/{item['id']}/content"
                    auth_header = f"Bearer {access_token}"

                source = "sharepoint" if resource.get("kind") == "site" else "onedrive"
                try:
                    await _unified.queue_binary_ingest(
                        org_id=org_id,
                        source=source,
                        external_id=item["id"],
                        name=name,
                        file_type=file_type,
                        download_url=download_url,
                        auth_header=auth_header,
                        user_id=user_id,
                    )
                    ingested += 1
                except Exception as exc:
                    log.warning(
                        "onedrive_queue_failed item=%s err=%s",
                        item.get("id"), exc,
                    )

            next_link = payload.get("@odata.nextLink")
            delta = payload.get("@odata.deltaLink")
            if delta:
                final_delta = delta
                url = None
            elif next_link:
                url = next_link
                pages_walked += 1
            else:
                url = None

    return final_delta, ingested
