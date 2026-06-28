"""ATS integration management — connect / disconnect / status / refresh mapping.

The publish-side of recruiting (`recruiting.py`) assumed that an ATS row already
existed in `integrations`. This module is the missing setup half: the recruiter
pastes their Greenhouse Harvest / Lever API / Ashby Public-API key here, we
validate it with the provider, persist the credentials via the unified
integrations helpers, and warm the mapping cache so the first publish
doesn't pay the taxonomy round-trip.

Auth: admin-only for writes (a non-admin should never connect a production ATS
credential to the workspace). Status reads are member-allowed so a hiring lead
can see whether their ATS is wired up before drafting a JD.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.config import get_settings
from app.core.rate_limiter import oauth_callback_limiter
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.services.integrations import _unified as _v2
from app.services.integrations.ats import ashby, greenhouse, lever
from app.services.integrations.job_boards import naukri
from app.services.recruiting import mapping_resolver

log = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations/ats", tags=["ats"])

# Includes 'naukri' which is technically a job board, not an ATS — they live
# in the same router because the connect / disconnect / status flow is
# identical (API-key auth, taxonomy cache warm). The destination_type field
# on the response distinguishes them for the UI.
AtsProvider = Literal["greenhouse", "lever", "ashby", "naukri"]


def _require_org(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return org_id, user_id, token


async def _require_admin(token: str, user_id: str) -> None:
    client = get_user_client(token)
    me = await asyncio.to_thread(
        lambda: client.table("users").select("role").eq("id", user_id).maybe_single().execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can manage ATS integrations.",
        )


# ── Pydantic ─────────────────────────────────────────────────────────────────


class ConnectAtsRequest(BaseModel):
    api_key: str = Field(..., min_length=10, max_length=500)
    # Greenhouse only — the recruiter's account subdomain (used to construct
    # the post-publish URL). Optional; defaults to "app" on the boarding side.
    board_subdomain: str | None = Field(default=None, max_length=100)
    # Greenhouse only — the user_id to publish "on behalf of". Optional;
    # without it Greenhouse posts as the API-key owner.
    on_behalf_of_user_id: str | None = Field(default=None, max_length=100)
    # Lever — the user_id that owns new postings. Optional but recommended.
    posting_owner_user_id: str | None = Field(default=None, max_length=100)
    # Ashby — the hiring-team default recruiter. Optional.
    hiring_team_member_id: str | None = Field(default=None, max_length=100)
    # Naukri — the recruiter's corporate Account-Id (sent in the Account-Id
    # header on every Naukri call). Single account on the contract = optional;
    # multi-account contracts MUST set this so postings don't land in the
    # wrong tenant.
    account_id: str | None = Field(default=None, max_length=100)


class TaxonomyEntry(BaseModel):
    id: str
    name: str
    location: str | None = None
    count: int | None = None


class AtsStatusBlock(BaseModel):
    connected: bool
    # 'ats' or 'job_board' — read from posting_registry. Lets the UI render
    # different copy / iconography without re-deriving the split client-side.
    destination_type: str | None = None
    connected_at: str | None = None
    last_error: str | None = None
    mapping_cached_at: str | None = None
    metadata_summary: dict[str, Any] | None = None


class AtsStatusResponse(BaseModel):
    greenhouse: AtsStatusBlock
    lever: AtsStatusBlock
    ashby: AtsStatusBlock
    naukri: AtsStatusBlock


# ── Status ───────────────────────────────────────────────────────────────────


@router.get("/status", response_model=AtsStatusResponse)
async def ats_status(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id, _user_id, _token = _require_org(current_user)

    from app.services.integrations import posting_registry

    greenhouse_row, lever_row, ashby_row, naukri_row = await asyncio.gather(
        _v2.get_row(org_id=org_id, provider="greenhouse"),
        _v2.get_row(org_id=org_id, provider="lever"),
        _v2.get_row(org_id=org_id, provider="ashby"),
        _v2.get_row(org_id=org_id, provider="naukri"),
    )

    def _block(provider: str, row: dict[str, Any] | None) -> dict[str, Any]:
        kind = posting_registry.kind_of(provider)
        if not row:
            return {"connected": False, "destination_type": kind}
        meta = row.get("metadata") or {}
        # Never leak credential-like values back to the UI.
        meta_summary = {k: v for k, v in meta.items() if k != "api_key"}
        return {
            "connected": True,
            "destination_type": kind,
            "connected_at": row.get("created_at"),
            "last_error": row.get("last_error"),
            "mapping_cached_at": row.get("mapping_cached_at"),
            "metadata_summary": meta_summary,
        }

    return {
        "greenhouse": _block("greenhouse", greenhouse_row),
        "lever": _block("lever", lever_row),
        "ashby": _block("ashby", ashby_row),
        "naukri": _block("naukri", naukri_row),
    }


# ── Connect ──────────────────────────────────────────────────────────────────


@router.post(
    "/{provider}/connect",
    dependencies=[Depends(oauth_callback_limiter)],
)
async def connect_ats(
    provider: AtsProvider,
    body: ConnectAtsRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)

    # 1. Validate the key against the provider before persisting anything.
    adapter = {
        "greenhouse": greenhouse,
        "lever": lever,
        "ashby": ashby,
        "naukri": naukri,
    }[provider]
    settings = get_settings()
    try:
        ok = await adapter.test_connection(api_key=body.api_key)
    except httpx.ConnectError as exc:
        # The host couldn't be reached. In USE_MOCK_ATS mode this almost
        # always means the mock server isn't running — surface that directly
        # instead of a generic "could not reach provider".
        if settings.use_mock_ats:
            log.warning(
                "ats.connect.mock_unreachable provider=%s url=%s err=%s",
                provider, settings.mock_ats_url, exc,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    f"Mock ATS server isn't running at {settings.mock_ats_url}. "
                    f"Start it with: cd apps/api && uv run python -m tools.mock_ats_server"
                ),
            ) from exc
        log.warning("ats.connect.unreachable provider=%s err=%s", provider, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not reach {provider}: {exc}",
        ) from exc
    except Exception as exc:
        log.warning("ats.connect.test_failed provider=%s err=%s", provider, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not reach {provider}: {exc}",
        ) from exc
    if not ok:
        # When USE_MOCK_ATS is on, the mock accepts any non-empty key — a
        # rejection here means something deeper is wrong (e.g. proxy intercept).
        if settings.use_mock_ats:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    f"Mock ATS returned a non-200 for {provider}. "
                    f"Check the mock server log for the actual response."
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The API key was rejected by the provider. Double-check it and try again.",
        )

    # 2. Persist via the unified integrations helper.
    metadata: dict[str, Any] = {}
    if provider == "greenhouse":
        if body.board_subdomain:
            metadata["board_subdomain"] = body.board_subdomain.strip()
        if body.on_behalf_of_user_id:
            metadata["on_behalf_of_user_id"] = body.on_behalf_of_user_id.strip()
    elif provider == "lever":
        if body.posting_owner_user_id:
            metadata["posting_owner_user_id"] = body.posting_owner_user_id.strip()
    elif provider == "ashby":
        if body.hiring_team_member_id:
            metadata["hiring_team_member_id"] = body.hiring_team_member_id.strip()
    elif provider == "naukri":
        if body.account_id:
            metadata["account_id"] = body.account_id.strip()

    await _v2.upsert_row(
        org_id=org_id,
        provider=provider,
        connected_by=user_id,
        access_token=body.api_key,
        metadata=metadata,
        # ATS API keys don't expire and there's no refresh endpoint to register.
        # The unified upsert path stores them in the existing access_token column.
    )

    # 3. Warm the mapping cache. Best-effort — a partial cache is fine and
    # the resolver gracefully degrades. Don't fail the connect on a cache miss.
    try:
        cache = await mapping_resolver.refresh_cache(
            org_id=org_id, ats_platform=provider
        )
    except Exception as exc:
        log.warning("ats.connect.cache_warm_failed provider=%s err=%s", provider, exc)
        cache = {}

    return {
        "ok": True,
        "provider": provider,
        "mapping_summary": _summarise_cache(cache),
    }


def _summarise_cache(cache: dict[str, Any]) -> dict[str, int]:
    return {key: len(value or []) for key, value in (cache or {}).items()}


# ── Disconnect ───────────────────────────────────────────────────────────────


@router.delete("/{provider}")
async def disconnect_ats(
    provider: AtsProvider,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)
    await _v2.delete_row(org_id=org_id, provider=provider)
    return {"ok": True}


# ── Refresh mapping ──────────────────────────────────────────────────────────


@router.post(
    "/{provider}/refresh-mapping",
    dependencies=[Depends(oauth_callback_limiter)],
)
async def refresh_mapping(
    provider: AtsProvider,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)
    try:
        cache = await mapping_resolver.refresh_cache(
            org_id=org_id, ats_platform=provider
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return {"ok": True, "mapping_summary": _summarise_cache(cache)}


# ── Department list (used by the publish form dropdowns) ─────────────────────


@router.get("/{provider}/departments")
async def list_departments(
    provider: AtsProvider,
    current_user: dict = Depends(verify_jwt),
) -> list[dict[str, str]]:
    """Return the cached department list for a provider so the publish form
    can show a plain dropdown instead of running fuzzy resolution.

    For Naukri we return Role Categories — it's the closest analogue to
    "department" in their Indian-market taxonomy and the field a recruiter
    would otherwise have to free-type. Functional area + industry are
    fetched via the dedicated `/taxonomy/{kind}` endpoint below.
    """
    org_id, _user_id, _token = _require_org(current_user)
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("integrations")
        .select("mapping_cache")
        .eq("org_id", org_id)
        .eq("provider", provider)
        .is_("scope_user_id", None)
        .maybe_single()
        .execute()
    )
    cache = ((row.data or {}).get("mapping_cache") or {}) if row else {}
    # Greenhouse → departments, Ashby → departments, Lever → teams,
    # Naukri → role_categories (the closest analogue to "department").
    entries = (
        cache.get("departments")
        or cache.get("teams")
        or cache.get("role_categories")
        or []
    )
    return [
        {"id": str(d["id"]), "name": str(d["name"])}
        for d in entries
        if d.get("id") is not None and d.get("name")
    ]


NaukriTaxonomyKind = Literal["functional_areas", "role_categories", "industries"]


@router.get("/naukri/taxonomy/{kind}")
async def list_naukri_taxonomy(
    kind: NaukriTaxonomyKind,
    current_user: dict = Depends(verify_jwt),
) -> list[dict[str, str]]:
    """Return one slice of Naukri's cached taxonomy.

    The publish form needs three separate dropdowns (functional area, role
    category, industry) that come from one connect-time refresh into
    `mapping_cache`. A single endpoint keyed by `kind` is friendlier than
    three near-identical ones — the UI just picks the kind it needs.
    """
    org_id, _user_id, _token = _require_org(current_user)
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("integrations")
        .select("mapping_cache")
        .eq("org_id", org_id)
        .eq("provider", "naukri")
        .is_("scope_user_id", None)
        .maybe_single()
        .execute()
    )
    cache = ((row.data or {}).get("mapping_cache") or {}) if row else {}
    entries = cache.get(kind) or []
    return [
        {"id": str(d["id"]), "name": str(d["name"])}
        for d in entries
        if d.get("id") is not None and d.get("name")
    ]
