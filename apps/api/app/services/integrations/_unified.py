"""Shared scaffolding for providers that use the unified `integrations` table.

The earlier integrations (Drive, Notion, Gmail, Slack) each got their own
provider table. Newer integrations share the `integrations` table introduced
in migration 036. This module hides that row shape behind a small set of
provider-agnostic helpers.

Shape recap:
    integrations(
      org_id, provider, scope_user_id,
      access_token, refresh_token, token_expiry, scopes,
      metadata JSONB,         -- install info (tenant, cloud_id, installation_id…)
      resources JSONB,        -- user-picked sites/spaces/repos/folders
      sync_cursor TEXT,       -- delta marker (deltaLink, cursor, ISO ts…)
      last_synced_at, last_error, last_error_at,
      webhook_secret
    )

Anything provider-specific (refresh URL, file-type mapping, page extraction)
stays in the provider module; this file is intentionally provider-agnostic.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.database import get_service_client
from app.services.integrations.text_ingest import upsert_external_document

log = logging.getLogger(__name__)

# Per-provider configuration the helpers need to refresh tokens without each
# service module re-implementing the same dance. Values are filled in by the
# provider modules at import time via `register_refresh()`.
_REFRESH_CONFIG: dict[str, dict[str, Any]] = {}


def register_refresh(
    provider: str,
    *,
    token_url: str,
    client_id_attr: str,
    client_secret_attr: str,
    extra_params: dict[str, str] | None = None,
    auth_style: str = "form",  # "form" | "basic"
) -> None:
    """Provider modules call this at import time to teach `refresh_token()`
    how to mint a new access_token. Separating it from the call site means
    a future provider can be wired up by adding one register_refresh() call,
    not a new branch inside refresh().
    """
    _REFRESH_CONFIG[provider] = {
        "token_url": token_url,
        "client_id_attr": client_id_attr,
        "client_secret_attr": client_secret_attr,
        "extra_params": extra_params or {},
        "auth_style": auth_style,
    }


# ── Row helpers ─────────────────────────────────────────────────────────────


async def get_row(*, org_id: str, provider: str, scope_user_id: str | None = None) -> dict[str, Any] | None:
    """Fetch a single integrations row.

    Org-scoped lookups pass scope_user_id=None and match WHERE scope_user_id
    IS NULL — the partial unique index guarantees ≤1 result. User-scoped
    callers pass the user id explicitly.
    """
    svc = get_service_client()

    def _run() -> dict[str, Any] | None:
        q = (
            svc.table("integrations")
            .select("*")
            .eq("org_id", org_id)
            .eq("provider", provider)
        )
        if scope_user_id is None:
            q = q.is_("scope_user_id", "null")
        else:
            q = q.eq("scope_user_id", scope_user_id)
        res = q.maybe_single().execute()
        return res.data if res else None

    return await asyncio.to_thread(_run)


async def upsert_row(
    *,
    org_id: str,
    provider: str,
    connected_by: str,
    access_token: str,
    refresh_token: str | None = None,
    token_expiry: datetime | None = None,
    scopes: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    scope_user_id: str | None = None,
    mint_webhook_secret: bool = True,
) -> dict[str, Any]:
    """Insert-or-update on (org_id, provider, scope_user_id).

    Preserves prior refresh_token + resources on re-consent, the way a user
    expects when reconnecting to add a scope. Webhook secret is minted once
    on first install and only rotated by explicit disconnect.
    """
    svc = get_service_client()
    existing = await get_row(org_id=org_id, provider=provider, scope_user_id=scope_user_id)

    new_refresh = refresh_token or (existing or {}).get("refresh_token") or None
    secret_val = (existing or {}).get("webhook_secret")
    if mint_webhook_secret and not secret_val:
        secret_val = secrets.token_urlsafe(32)

    row: dict[str, Any] = {
        "org_id": org_id,
        "provider": provider,
        "scope_user_id": scope_user_id,
        "connected_by": connected_by,
        "access_token": access_token,
        "refresh_token": new_refresh,
        "token_expiry": token_expiry.isoformat() if token_expiry else None,
        "scopes": scopes or [],
        "metadata": metadata or (existing or {}).get("metadata") or {},
        "webhook_secret": secret_val,
    }

    def _run() -> dict[str, Any]:
        if existing:
            res = (
                svc.table("integrations")
                .update(row)
                .eq("id", existing["id"])
                .execute()
            )
            return (res.data or [{}])[0] if res and res.data else {**existing, **row}
        res = svc.table("integrations").insert(row).execute()
        return (res.data or [{}])[0] if res and res.data else row

    return await asyncio.to_thread(_run)


async def update_fields(
    *,
    org_id: str,
    provider: str,
    fields: dict[str, Any],
    scope_user_id: str | None = None,
) -> None:
    """Targeted field update — used for cursor advances, error markers, etc."""
    svc = get_service_client()

    def _run() -> None:
        q = (
            svc.table("integrations")
            .update(fields)
            .eq("org_id", org_id)
            .eq("provider", provider)
        )
        if scope_user_id is None:
            q = q.is_("scope_user_id", "null")
        else:
            q = q.eq("scope_user_id", scope_user_id)
        q.execute()

    await asyncio.to_thread(_run)


async def delete_row(*, org_id: str, provider: str, scope_user_id: str | None = None) -> None:
    svc = get_service_client()

    def _run() -> None:
        q = (
            svc.table("integrations")
            .delete()
            .eq("org_id", org_id)
            .eq("provider", provider)
        )
        if scope_user_id is None:
            q = q.is_("scope_user_id", "null")
        else:
            q = q.eq("scope_user_id", scope_user_id)
        q.execute()

    await asyncio.to_thread(_run)


async def find_row_by_metadata(
    *, provider: str, key: str, value: str
) -> dict[str, Any] | None:
    """Look up an integrations row by a metadata field.

    For inbound webhooks that arrive independent of any org-scoped request
    and must self-resolve which org they belong to from an
    account/tenant id carried in the payload.
    """
    svc = get_service_client()

    def _run() -> dict[str, Any] | None:
        res = (
            svc.table("integrations")
            .select("*")
            .eq("provider", provider)
            .eq(f"metadata->>{key}", value)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    return await asyncio.to_thread(_run)


async def list_org_rows(*, provider: str) -> list[dict[str, Any]]:
    """Used by polling crons to iterate every connected org for a provider."""
    svc = get_service_client()

    def _run() -> list[dict[str, Any]]:
        res = (
            svc.table("integrations")
            .select("*")
            .eq("provider", provider)
            .execute()
        )
        return list(res.data or [])

    return await asyncio.to_thread(_run)


# ── Token refresh ───────────────────────────────────────────────────────────


def _is_expiring(token_expiry: str | None, *, skew_seconds: int = 60) -> bool:
    """Refresh slightly before the wall-clock expiry so a sync that takes a
    minute doesn't blow up on a token that expires mid-run."""
    if not token_expiry:
        return False  # No expiry recorded → treat as long-lived (Notion-style)
    try:
        exp = datetime.fromisoformat(token_expiry.replace("Z", "+00:00"))
    except ValueError:
        return True
    return exp <= datetime.now(UTC) + timedelta(seconds=skew_seconds)


async def ensure_fresh_token(row: dict[str, Any], *, settings: Any) -> str:
    """Return a non-expired access_token, refreshing via refresh_token if needed.

    Persists the new tokens back to the row so subsequent calls in the same
    sync cycle skip the round-trip. Raises RuntimeError on hard refresh
    failure (revoked token, etc.) — callers should mark the integration with
    last_error and surface to the UI rather than crashing the cron.
    """
    if not _is_expiring(row.get("token_expiry")):
        return row["access_token"]

    provider = row["provider"]
    conf = _REFRESH_CONFIG.get(provider)
    if not conf:
        # Provider didn't register a refresh URL — must be a long-lived token.
        return row["access_token"]

    refresh_token = row.get("refresh_token")
    if not refresh_token:
        raise RuntimeError(f"{provider}_no_refresh_token")

    client_id = getattr(settings, conf["client_id_attr"], "")
    client_secret = getattr(settings, conf["client_secret_attr"], "")

    data: dict[str, str] = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        **conf["extra_params"],
    }
    headers = {"Accept": "application/json"}

    if conf["auth_style"] == "basic":
        import base64
        creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers["Authorization"] = f"Basic {creds}"
    else:
        data["client_id"] = client_id
        data["client_secret"] = client_secret

    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(conf["token_url"], data=data, headers=headers)

    if resp.status_code != 200:
        raise RuntimeError(
            f"{provider}_refresh_failed: {resp.status_code} {resp.text[:200]}"
        )

    payload = resp.json()
    new_access = payload["access_token"]
    new_refresh = payload.get("refresh_token") or refresh_token  # most providers omit on refresh
    expires_in = int(payload.get("expires_in") or 3600)
    new_expiry = datetime.now(UTC) + timedelta(seconds=expires_in)

    await update_fields(
        org_id=row["org_id"],
        provider=provider,
        scope_user_id=row.get("scope_user_id"),
        fields={
            "access_token": new_access,
            "refresh_token": new_refresh,
            "token_expiry": new_expiry.isoformat(),
        },
    )
    return new_access


# ── Sync state helpers ──────────────────────────────────────────────────────


async def mark_synced(
    *,
    org_id: str,
    provider: str,
    cursor: str | None = None,
    scope_user_id: str | None = None,
) -> None:
    fields: dict[str, Any] = {
        "last_synced_at": datetime.now(UTC).isoformat(),
        "last_error": None,
        "last_error_at": None,
    }
    if cursor is not None:
        fields["sync_cursor"] = cursor
    await update_fields(
        org_id=org_id, provider=provider, scope_user_id=scope_user_id, fields=fields
    )


async def mark_error(
    *,
    org_id: str,
    provider: str,
    error: str,
    scope_user_id: str | None = None,
) -> None:
    await update_fields(
        org_id=org_id,
        provider=provider,
        scope_user_id=scope_user_id,
        fields={
            "last_error": error[:500],
            "last_error_at": datetime.now(UTC).isoformat(),
        },
    )


# ── External binary ingestion (PDF/DOCX/XLSX/PPTX from a URL) ───────────────
#
# Providers can supply a download URL and auth header for a binary file. The
# shared event keeps the "download bytes → run pipeline" work out of polling
# and webhook requests.


async def queue_binary_ingest(
    *,
    org_id: str,
    source: str,
    external_id: str,
    name: str,
    file_type: str,         # 'pdf' | 'docx' | 'xlsx' | 'pptx' | 'txt' | 'md'
    download_url: str,
    auth_header: str,
    user_id: str | None,
    extra_event_data: dict[str, Any] | None = None,
) -> str:
    """Upsert the documents row and fire `doc/process-binary-external`.

    Returns the upserted document id so the caller can track ingest state.
    The actual download happens inside the Inngest worker so a long fetch
    doesn't tie up the polling cron's per-org budget.
    """
    doc_id = await upsert_external_document(
        org_id=org_id,
        source=source,
        external_id=external_id,
        name=name,
        file_type=file_type,
        user_id=user_id,
    )

    import inngest

    from app.inngest.client import get_inngest_client

    client = get_inngest_client()
    event_data: dict[str, Any] = {
        "doc_id": doc_id,
        "org_id": org_id,
        "file_type": file_type,
        "download_url": download_url,
        "auth_header": auth_header,
    }
    if extra_event_data:
        event_data.update(extra_event_data)

    await client.send(
        inngest.Event(
            name="doc/process-binary-external",
            data=event_data,
            id=f"{source}-{doc_id}-{external_id[-12:]}",
        )
    )
    return doc_id


async def download_and_process_binary(
    *,
    doc_id: str,
    org_id: str,
    file_type: str,
    download_url: str,
    auth_header: str,
) -> dict[str, Any]:
    """Inngest worker entry point for doc/process-binary-external.

    Downloads the file from the provider and runs the regular ingestion
    pipeline on its bytes — no Storage round-trip, since the source of
    truth lives in the integration's API.
    """
    from app.services.ingestion import PipelineError, mark_status, process_document

    headers = {"Authorization": auth_header} if auth_header else {}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0), follow_redirects=True) as client:
            resp = await client.get(download_url, headers=headers)
        if resp.status_code != 200:
            await mark_status(
                doc_id,
                "failed",
                error_reason=f"Download failed ({resp.status_code})",
            )
            return {"status": "failed", "http_status": resp.status_code}

        # Soft cap at 50 MB to match the upload limit. Anything bigger is
        # almost certainly a file we can't usefully chunk.
        body = resp.content
        if len(body) > 50 * 1024 * 1024:
            await mark_status(
                doc_id,
                "failed",
                error_reason="File exceeds 50 MB ingestion limit.",
            )
            return {"status": "failed", "reason": "too_large"}

        stats = await process_document(
            doc_id=doc_id,
            org_id=org_id,
            file_bytes=body,
            file_type=file_type,
        )

        if stats.embedded == 0:
            await mark_status(
                doc_id,
                "failed",
                error_reason="All chunks failed to embed.",
                embedding_stats={
                    "embedded": 0,
                    "failed": stats.failed,
                    "total": stats.chunk_count,
                },
            )
            return {"status": "failed", "embedded": 0}

        emb_meta = (
            {"embedded": stats.embedded, "failed": stats.failed, "total": stats.chunk_count}
            if stats.failed
            else None
        )
        await mark_status(
            doc_id,
            "ready",
            chunk_count=stats.chunk_count,
            embedding_stats=emb_meta,
        )
        return {
            "status": "ok",
            "embedded": stats.embedded,
            "failed": stats.failed,
            "total": stats.chunk_count,
        }
    except PipelineError as exc:
        await mark_status(doc_id, "failed", error_reason=str(exc))
        return {"status": "failed", "error": str(exc)}
    except Exception as exc:
        log.exception("download_and_process_binary failed for %s", doc_id)
        await mark_status(doc_id, "failed", error_reason=f"{type(exc).__name__}: {exc}")
        return {"status": "failed", "error": str(exc)}
