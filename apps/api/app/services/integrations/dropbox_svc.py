"""Dropbox / Dropbox Business integration.

Dropbox's OAuth is the most straightforward of the four:
    auth:    https://www.dropbox.com/oauth2/authorize
    token:   https://api.dropboxapi.com/oauth2/token
    api:     https://api.dropboxapi.com/2/...
    content: https://content.dropboxapi.com/2/files/download

Critical quirk: most Dropbox API calls are POST with JSON bodies, even for
operations that are conceptually GETs. File downloads carry the request as a
`Dropbox-API-Arg` header instead of a body, with the response carrying the
bytes — so the standard binary-ingest helper would NOT work without a small
adjustment. We work around it by minting our own short-lived temporary
download link via `/files/get_temporary_link` and feeding THAT to the
binary-ingest path. Temp links are valid for 4 hours and don't require auth,
so we can hand them off without leaking the access token.

`resources` JSONB:
    [
      {"path": "/Marketing/", "name": "Marketing"},
    ]

Per-path delta state lives in `metadata.cursors` as {path: cursor_string},
since the integration-row `sync_cursor` column is single-valued and we may
sync several folders per install.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import get_settings
from app.services.integrations import _unified

log = logging.getLogger(__name__)

PROVIDER = "dropbox"

_DBX_API = "https://api.dropboxapi.com/2"

_SCOPES = [
    "files.content.read",
    "files.metadata.read",
    "account_info.read",
]

_SUPPORTED_EXT = {
    "pdf": "pdf",
    "docx": "docx",
    "xlsx": "xlsx",
    "pptx": "pptx",
    "txt": "txt",
    "md": "md",
}


_unified.register_refresh(
    PROVIDER,
    token_url=f"{_DBX_API.replace('/2', '')}/oauth2/token",
    client_id_attr="dropbox_client_id",
    client_secret_attr="dropbox_client_secret",
)


# ── OAuth ───────────────────────────────────────────────────────────────────


def build_auth_url(*, state: str) -> str:
    settings = get_settings()
    params = {
        "client_id": settings.dropbox_client_id,
        "response_type": "code",
        "redirect_uri": settings.dropbox_oauth_redirect_uri,
        "state": state,
        # Required to get a refresh_token; without this Dropbox issues a
        # short-lived token only, and we'd have to re-auth on every sync.
        "token_access_type": "offline",
        "scope": " ".join(_SCOPES),
    }
    return f"https://www.dropbox.com/oauth2/authorize?{urlencode(params)}"


async def exchange_code(*, code: str) -> dict[str, Any]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(
            f"{_DBX_API.replace('/2', '')}/oauth2/token",
            data={
                "code": code,
                "grant_type": "authorization_code",
                "client_id": settings.dropbox_client_id,
                "client_secret": settings.dropbox_client_secret,
                "redirect_uri": settings.dropbox_oauth_redirect_uri,
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"dropbox_token_exchange_failed: {resp.status_code} {resp.text[:200]}"
        )
    return resp.json()


async def _fetch_account(token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        # Dropbox's `users/get_current_account` is a POST with `null` body.
        resp = await client.post(
            f"{_DBX_API}/users/get_current_account",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            content="null",
        )
    if resp.status_code != 200:
        return {}
    body = resp.json()
    return {
        "account_id": body.get("account_id"),
        "email": body.get("email"),
        "name": (body.get("name") or {}).get("display_name"),
        "team_name": (body.get("team") or {}).get("name"),
    }


async def store_credentials(
    *, org_id: str, user_id: str, token_payload: dict[str, Any]
) -> None:
    expires_in = int(token_payload.get("expires_in") or 3600)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    scope_list = (token_payload.get("scope") or "").split()
    access_token = token_payload["access_token"]
    account = await _fetch_account(access_token)
    await _unified.upsert_row(
        org_id=org_id,
        provider=PROVIDER,
        connected_by=user_id,
        access_token=access_token,
        refresh_token=token_payload.get("refresh_token"),
        token_expiry=expiry,
        scopes=scope_list,
        metadata={"account": account, "cursors": {}},
    )


async def disconnect(*, org_id: str) -> None:
    row = await _unified.get_row(org_id=org_id, provider=PROVIDER)
    if row:
        # Best-effort token revoke. Failure here doesn't block the local
        # delete — admins expect "Disconnect" to free the UI state regardless.
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                await client.post(
                    f"{_DBX_API}/auth/token/revoke",
                    headers={
                        "Authorization": f"Bearer {row['access_token']}",
                        "Content-Type": "application/json",
                    },
                    content="null",
                )
        except Exception:
            pass
    await _unified.delete_row(org_id=org_id, provider=PROVIDER)


# ── Folder browsing (picker) ────────────────────────────────────────────────


async def _token_and_row(org_id: str) -> tuple[str, dict[str, Any]] | None:
    settings = get_settings()
    row = await _unified.get_row(org_id=org_id, provider=PROVIDER)
    if not row:
        return None
    token = await _unified.ensure_fresh_token(row, settings=settings)
    return token, row


async def list_folders(*, org_id: str, path: str = "") -> list[dict[str, Any]]:
    """List folders under a given path (empty string = root).

    Used by the picker UI. Returns only folders — the cron will recurse into
    selected ones for files.
    """
    found = await _token_and_row(org_id)
    if not found:
        return []
    token, _ = found
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(
            f"{_DBX_API}/files/list_folder",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "path": path,
                "recursive": False,
                "include_deleted": False,
                "include_mounted_folders": True,
                "limit": 200,
            },
        )
    if resp.status_code != 200:
        log.warning("dropbox_list_failed %s %s", resp.status_code, resp.text[:200])
        return []
    out: list[dict[str, Any]] = []
    for entry in resp.json().get("entries") or []:
        if entry.get(".tag") != "folder":
            continue
        out.append(
            {
                "path": entry.get("path_display") or entry.get("path_lower"),
                "name": entry.get("name"),
                "id": entry.get("id"),
            }
        )
    return out


async def update_resources(
    *, org_id: str, resources: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for r in resources or []:
        path = r.get("path")
        if not path or not isinstance(path, str):
            continue
        cleaned.append({"path": path[:500], "name": (r.get("name") or path)[:200]})
    row = await _unified.get_row(org_id=org_id, provider=PROVIDER)
    if not row:
        raise RuntimeError("dropbox_not_connected")
    from app.database import get_service_client
    import asyncio as _asyncio
    svc = get_service_client()
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
            log.warning("dropbox_sync_failed org=%s err=%s", row["org_id"], exc)
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
    cursors: dict[str, str] = (row.get("metadata") or {}).get("cursors") or {}

    total = 0
    for res in resources:
        path = res.get("path")
        if not path:
            continue
        try:
            new_cursor, n = await _sync_folder(
                org_id=org_id,
                user_id=row.get("connected_by"),
                token=token,
                path=path,
                cursor=cursors.get(path),
            )
            cursors[path] = new_cursor
            total += n
        except Exception as exc:
            log.warning(
                "dropbox_folder_sync_failed org=%s path=%s err=%s",
                org_id, path, exc,
            )

    metadata = dict(row.get("metadata") or {})
    metadata["cursors"] = cursors
    await _unified.update_fields(
        org_id=org_id, provider=PROVIDER, fields={"metadata": metadata}
    )
    await _unified.mark_synced(org_id=org_id, provider=PROVIDER)
    return {"status": "ok", "ingested": total}


async def _sync_folder(
    *,
    org_id: str,
    user_id: str | None,
    token: str,
    path: str,
    cursor: str | None,
) -> tuple[str, int]:
    """Initial list (no cursor) or delta (with cursor). Returns the new cursor.

    First run on a freshly-picked folder walks the entire tree under it —
    Dropbox's list_folder with recursive=True handles pagination via
    has_more + cursor, and we keep calling /list_folder/continue until
    has_more=False, then save the final cursor.
    """
    ingested = 0
    pages_walked = 0
    MAX_PAGES = 20  # generous; backfills can be slow but bounded

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        if cursor is None:
            # Initial list.
            resp = await client.post(
                f"{_DBX_API}/files/list_folder",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "path": path,
                    "recursive": True,
                    "include_deleted": False,
                    "limit": 500,
                },
            )
        else:
            resp = await client.post(
                f"{_DBX_API}/files/list_folder/continue",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={"cursor": cursor},
            )

        while True:
            if resp.status_code == 401:
                raise RuntimeError("dropbox_token_revoked")
            if resp.status_code != 200:
                raise RuntimeError(
                    f"dropbox_list_failed: {resp.status_code} {resp.text[:200]}"
                )
            payload = resp.json()
            for entry in payload.get("entries") or []:
                if entry.get(".tag") != "file":
                    continue
                name = entry.get("name") or "Untitled"
                ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                file_type = _SUPPORTED_EXT.get(ext)
                if not file_type:
                    continue
                entry_path = entry.get("path_display") or entry.get("path_lower")
                if not entry_path:
                    continue
                # Mint a temp link instead of feeding the auth'd content URL
                # to the binary worker — temp links are valid 4h and avoid
                # putting the bearer in the Inngest event payload.
                try:
                    tlink = await _temporary_link(client, token, entry_path)
                except Exception as exc:
                    log.warning("dropbox_tlink_failed path=%s err=%s", entry_path, exc)
                    continue
                try:
                    await _unified.queue_binary_ingest(
                        org_id=org_id,
                        source=PROVIDER,
                        external_id=entry.get("id") or entry_path,
                        name=name,
                        file_type=file_type,
                        download_url=tlink,
                        auth_header="",
                        user_id=user_id,
                    )
                    ingested += 1
                except Exception as exc:
                    log.warning(
                        "dropbox_queue_failed path=%s err=%s",
                        entry_path, exc,
                    )

            cursor = payload.get("cursor") or cursor
            if not payload.get("has_more"):
                break
            pages_walked += 1
            if pages_walked >= MAX_PAGES:
                # Leave the cursor advanced; next tick picks up where this
                # one left off. Avoids holding the cron hostage to a giant
                # initial backfill.
                break
            resp = await client.post(
                f"{_DBX_API}/files/list_folder/continue",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={"cursor": cursor},
            )

    return cursor or "", ingested


async def _temporary_link(
    client: httpx.AsyncClient, token: str, path: str
) -> str:
    resp = await client.post(
        f"{_DBX_API}/files/get_temporary_link",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"path": path},
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"dropbox_tlink_failed: {resp.status_code} {resp.text[:200]}"
        )
    return resp.json()["link"]
