"""V5 #35 — Document collections (tag-based saved views).

A `collection` is a named bucket of tags, e.g. "Marketing" = ["marketing",
"brand", "campaigns"]. A document is "in" the collection if any of its tags
appear in `tag_filters`. Collections compose with the existing per-doc and
per-tag chat scoping (V3) — selecting a collection on the new-chat surface
just resolves to its tag list and reuses the scoped_tags plumbing.

Routes:
    GET    /collections                       — list + document counts
    POST   /collections                       — admin-only create
    PATCH  /collections/{id}                  — admin-only edit
    DELETE /collections/{id}                  — admin-only delete (docs untouched)
    GET    /collections/{id}/documents        — list docs in a collection
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from supabase import Client

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization

log = logging.getLogger(__name__)

router = APIRouter(prefix="/collections", tags=["collections"])

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_NAME_MAX = 80
_DESC_MAX = 280
_ICON_MAX = 12
_MAX_TAGS = 20


# ── Request / response models ──────────────────────────────────────────────

class CollectionUpsertBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=_NAME_MAX)
    description: str | None = Field(default=None, max_length=_DESC_MAX)
    # Color is required for create — UI sends #6366f1 by default if the user
    # didn't change the picker. Always validated against the hex regex below.
    color: str = Field(default="#6366f1", max_length=7)
    icon: str | None = Field(default=None, max_length=_ICON_MAX)
    tag_filters: list[str] = Field(default_factory=list, max_length=_MAX_TAGS)


class CollectionPatchBody(BaseModel):
    """Same fields as upsert, all optional — PATCH semantics."""

    name: str | None = Field(default=None, min_length=1, max_length=_NAME_MAX)
    description: str | None = Field(default=None, max_length=_DESC_MAX)
    color: str | None = Field(default=None, max_length=7)
    icon: str | None = Field(default=None, max_length=_ICON_MAX)
    tag_filters: list[str] | None = Field(default=None, max_length=_MAX_TAGS)


# ── Helpers ────────────────────────────────────────────────────────────────

def _require_org(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return org_id, user_id, token


async def _require_admin(token: str, user_id: str) -> None:
    """Cheap admin guard — same pattern as routers/admin.py."""
    user_client = get_user_client(token)
    me = await asyncio.to_thread(
        lambda: user_client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Admin only.")


def _normalize_tags(raw: list[str] | None) -> list[str]:
    """Lowercase + dedupe (preserves first-seen order)."""
    out: list[str] = []
    seen: set[str] = set()
    for t in raw or []:
        if not isinstance(t, str):
            continue
        cleaned = t.strip().lower()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


def _validate_color(color: str) -> str:
    if not _HEX_RE.match(color):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Color must be a hex value like #6366f1.",
        )
    return color.lower()


async def _doc_count_for_tags(
    *, client: Client, org_id: str, tags: list[str]
) -> int:
    """Return the count of `ready` documents in this org tagged with ANY of
    the provided tags. We do the count in Python because supabase-py's array-
    overlap operator support (`.overlaps()`) doesn't ship a count parameter."""
    if not tags:
        return 0
    res = await asyncio.to_thread(
        lambda: client.table("documents")
        .select("id, tags", count="exact")
        .eq("org_id", org_id)
        .eq("status", "ready")
        .overlaps("tags", tags)
        .limit(1)  # we only want the count, not the rows
        .execute()
    )
    return int(getattr(res, "count", 0) or 0)


def _shape(row: dict[str, Any], document_count: int) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row.get("description"),
        "color": row.get("color") or "#6366f1",
        "icon": row.get("icon"),
        "tag_filters": list(row.get("tag_filters") or []),
        "document_count": document_count,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


# ── Routes ─────────────────────────────────────────────────────────────────

@router.get("")
async def list_collections(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _user_id, token = _require_org(current_user)
    client = get_user_client(token)

    rows_res = await asyncio.to_thread(
        lambda: client.table("collections")
        .select("*")
        .eq("org_id", org_id)
        .order("name")
        .limit(200)
        .execute()
    )
    rows = rows_res.data or []

    # Per-row counts in parallel — capped to the typical row count (one org
    # with 50 collections is unrealistic; we trim to 50 just so a hostile org
    # can't fan out N counts).
    counts = await asyncio.gather(
        *(
            _doc_count_for_tags(
                client=client, org_id=org_id, tags=list(row.get("tag_filters") or []),
            )
            for row in rows[:50]
        )
    )
    out = [_shape(row, count) for row, count in zip(rows, counts)]
    return {"collections": out}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_collection(
    body: CollectionUpsertBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)

    color = _validate_color(body.color)
    tags = _normalize_tags(body.tag_filters)
    if not tags:
        # A collection with no tags would match every document — silently
        # confusing. Force the admin to pick at least one tag.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Pick at least one tag for this collection.",
        )

    client = get_user_client(token)
    row = {
        "org_id": org_id,
        "name": body.name.strip(),
        "description": (body.description or "").strip() or None,
        "color": color,
        "icon": (body.icon or "").strip() or None,
        "tag_filters": tags,
        "created_by": user_id,
    }
    try:
        res = await asyncio.to_thread(
            lambda: client.table("collections").insert(row).execute()
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "collections_unique_name_per_org" in msg or "duplicate key" in msg:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f'A collection named "{body.name}" already exists.',
            ) from exc
        raise

    created = (res.data or [None])[0]
    if not created:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Insert returned no row.")
    count = await _doc_count_for_tags(client=client, org_id=org_id, tags=tags)
    return _shape(created, count)


@router.patch("/{collection_id}")
async def update_collection(
    collection_id: str,
    body: CollectionPatchBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)

    patch: dict[str, Any] = {}
    if body.name is not None:
        patch["name"] = body.name.strip()
    if body.description is not None:
        patch["description"] = body.description.strip() or None
    if body.color is not None:
        patch["color"] = _validate_color(body.color)
    if body.icon is not None:
        patch["icon"] = body.icon.strip() or None
    if body.tag_filters is not None:
        tags = _normalize_tags(body.tag_filters)
        if not tags:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="A collection needs at least one tag.",
            )
        patch["tag_filters"] = tags
    if not patch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Nothing to update.")
    patch["updated_at"] = datetime.now(timezone.utc).isoformat()

    client = get_user_client(token)
    try:
        res = await asyncio.to_thread(
            lambda: client.table("collections")
            .update(patch)
            .eq("id", collection_id)
            .eq("org_id", org_id)
            .execute()
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "duplicate key" in msg or "collections_unique_name_per_org" in msg:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="A collection with that name already exists.",
            ) from exc
        raise

    rows = res.data or []
    if not rows:
        # RLS hides not-yours rows; either way the caller sees a 404 here.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Collection not found.")
    row = rows[0]
    count = await _doc_count_for_tags(
        client=client, org_id=org_id, tags=list(row.get("tag_filters") or []),
    )
    return _shape(row, count)


@router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection(
    collection_id: str,
    current_user: dict = Depends(verify_jwt),
) -> None:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)

    client = get_user_client(token)
    await asyncio.to_thread(
        lambda: client.table("collections")
        .delete()
        .eq("id", collection_id)
        .eq("org_id", org_id)
        .execute()
    )
    # We don't 404 on a no-op delete — RLS + double-delete races would make
    # that noisier than useful. The conversations.scoped_collection_id FK has
    # ON DELETE SET NULL so existing conversations keep their tag snapshot.
    return None


@router.get("/{collection_id}/documents")
async def list_collection_documents(
    collection_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _user_id, token = _require_org(current_user)
    client = get_user_client(token)

    col = await asyncio.to_thread(
        lambda: client.table("collections")
        .select("id, tag_filters")
        .eq("id", collection_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not col or not col.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Collection not found.")
    tags = list(col.data.get("tag_filters") or [])
    if not tags:
        return {"documents": []}

    docs = await asyncio.to_thread(
        lambda: client.table("documents")
        .select("id, name, file_type, tags, status, created_at, metadata")
        .eq("org_id", org_id)
        .eq("status", "ready")
        .overlaps("tags", tags)
        .order("created_at", desc=True)
        .limit(500)
        .execute()
    )
    return {"documents": docs.data or []}


# ── Internal helper used by the chat router to resolve collection → tags ──

async def resolve_collection_tags(
    *, client: Client, org_id: str, collection_id: str
) -> tuple[str, list[str]]:
    """Look up the collection's tag snapshot for chat-scope resolution.

    Returns (collection_id, tags). Raises 404 if the collection isn't in this
    org (RLS hides cross-org rows so this is enforced at the DB layer too).
    """
    res = await asyncio.to_thread(
        lambda: client.table("collections")
        .select("id, tag_filters")
        .eq("id", collection_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not res or not res.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Collection not found.")
    return res.data["id"], list(res.data.get("tag_filters") or [])
