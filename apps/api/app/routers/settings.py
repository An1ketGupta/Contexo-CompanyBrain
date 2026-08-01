"""Settings endpoints — workspace + user profile + account deletion.

Naming convention: `/organizations/me` and `/users/me` route to the caller's
own resources via JWT. We avoid `/organizations/{id}` so a future bug in path
handling can't lead a user to mutate someone else's org.

Account deletion uses the service-role client because `auth.admin.delete_user`
isn't callable with a user JWT. The org cascade comes from FKs (see migration
001 — documents/conversations/messages all hold an `org_id` that goes away when
the org row is deleted).
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.services import org_config

log = logging.getLogger(__name__)

router = APIRouter(tags=["settings"])

ORG_NAME_PATTERN = re.compile(r"^[\w\s\-&.,'()]+$", re.UNICODE)


def _require_user(current_user: dict) -> tuple[str, str, str]:
    user_id = current_user.get("user_id")
    org_id = current_user.get("org_id")
    token = current_user.get("token")
    if not user_id or not org_id or not token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No organization found. Please sign out and sign back in.",
        )
    return user_id, org_id, token


# ── Request models ────────────────────────────────────────────────────────────

class UpdateOrganizationRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)


class UpdateOrgSettingsRequest(BaseModel):
    # `None` clears any existing value; empty string is treated the same as None.
    # 500-char cap mirrors org_config.INSTRUCTIONS_MAX_CHARS — keep the two in sync
    # if you change it. The text rides inside the LLM system prompt on every turn,
    # so length directly costs us tokens.
    ai_instructions: str | None = Field(default=None, max_length=500)


class UpdateOrgSharingRequest(BaseModel):
    # V3 Day 4 #62. Admin kill-switch for public output links. New shares are
    # gated by this; existing tokens stop resolving while the flag is off.
    allow_output_sharing: bool


# V3 #104 — conversation archive settings live in organizations.metadata.archive.
# Defaults applied in get/patch handlers, not at the DB level, so we can change
# them without a migration.
ARCHIVE_THRESHOLD_CHOICES = [30, 45, 60, 90, 180]


class UpdateArchiveSettingsRequest(BaseModel):
    auto_archive_enabled: bool | None = None
    # None or 0 in `threshold_days` means "use default 45". Anything else
    # must be one of the allow-listed values so the UI Select can't push a
    # 999-day value through.
    threshold_days: int | None = Field(default=None, ge=1, le=3650)
    # Optional retention tier — None = never delete (default). Cap at 365
    # days so a typo doesn't set a 100-year window.
    delete_after_archive_days: int | None = Field(default=None, ge=1, le=365)
    # Explicit clear sentinel for `delete_after_archive_days` because the
    # JSON `null` we'd otherwise need would collide with "field omitted".
    clear_delete_retention: bool = False


class UpdateOnboardingStepsRequest(BaseModel):
    """Which optional onboarding steps this org runs. An omitted field leaves
    that step's current setting alone, so the UI can PATCH one toggle at a
    time. LOI is absent because it is never optional."""

    bgv: bool | None = None
    appointment_bundle: bool | None = None
    policies: bool | None = None
    induction: bool | None = None


class UpdateProfileRequest(BaseModel):
    # All optional; PATCH allows changing one without echoing the others back.
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    # V4 #57 — hide my activity from the team feed.
    activity_private: bool | None = None
    # Agent2 Day 2 #37 — function-style persona. None clears it. Validated
    # against the same enum the DB CHECK constraint enforces, plus 'custom'
    # and 'org:<uuid>' for Feature 1.11.
    persona: str | None = Field(default=None)
    # Sentinel: explicit clear vs. omitted. Pydantic doesn't expose "user
    # passed null" vs "user omitted" via Optional alone, so a sibling flag.
    clear_persona: bool = False
    # Feature 1.11 — custom persona body. Only applied when persona='custom'
    # is selected on the same request, OR when the user already has
    # persona='custom' and is editing the prompt without flipping kind.
    custom_persona_name: str | None = Field(default=None, max_length=80)
    custom_persona_instructions: str | None = Field(
        default=None, max_length=2000
    )


class DeleteAccountRequest(BaseModel):
    # Defense in depth — the UI also requires the user to type the org name.
    confirm_org_name: str


# ── Organization ──────────────────────────────────────────────────────────────

@router.patch("/organizations/me")
async def update_organization(
    body: UpdateOrganizationRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)

    cleaned = " ".join(body.name.split()).strip()
    if not cleaned or not ORG_NAME_PATTERN.match(cleaned):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Organization name contains unsupported characters.",
        )

    # Role check — only admins can rename the org. Members get a 403.
    me = await asyncio.to_thread(
        lambda: client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not me.data or me.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can update workspace settings.",
        )

    result = await asyncio.to_thread(
        lambda: client.table("organizations")
        .update({"name": cleaned})
        .eq("id", org_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found.",
        )
    return {"organization": result.data[0]}


# ── Competitor watchlist (admin org list + per-user list) ────────────────────

# Length caps mirror the DB CHECK constraints — defence-in-depth so a
# malformed client can't push a 10k-entry array that the DB would
# eventually reject anyway.
ORG_COMPETITORS_MAX = 200
USER_COMPETITORS_MAX = 100
# Per-term length cap. 200 chars is far longer than any real company
# name but bounded enough that the compiled regex stays cheap.
COMPETITOR_TERM_MAX_CHARS = 200


def _clean_competitor_list(raw: list[str] | None, *, cap: int) -> list[str]:
    """Strip, collapse whitespace, drop empties, dedupe case-insensitively,
    enforce per-entry max length. Order preserved on first-seen so the UI's
    drag-to-reorder (future) doesn't get rearranged on every save.
    """
    if not raw:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for term in raw:
        if not isinstance(term, str):
            continue
        cleaned = " ".join(term.split()).strip()
        if not cleaned:
            continue
        if len(cleaned) > COMPETITOR_TERM_MAX_CHARS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Competitor name exceeds {COMPETITOR_TERM_MAX_CHARS} characters.",
            )
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    if len(out) > cap:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"At most {cap} competitor names allowed.",
        )
    return out


class UpdateCompetitorListRequest(BaseModel):
    # `None` is treated as "leave alone"; `[]` clears the list. Sized at
    # the higher of the two caps so a stale client doesn't get rejected
    # on the org endpoint for being one entry over the user cap.
    names: list[str] = Field(..., max_length=ORG_COMPETITORS_MAX)


@router.get("/organizations/me/competitors")
async def get_org_competitors(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Read the org-wide competitor watchlist. All members can read; only
    admins can write — matches the AI settings pattern above."""
    _, org_id, token = _require_user(current_user)
    client = get_user_client(token)
    result = await asyncio.to_thread(
        lambda: client.table("organizations")
        .select("competitor_names")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return {
        "names": result.data.get("competitor_names") or [],
        "max": ORG_COMPETITORS_MAX,
    }


@router.put("/organizations/me/competitors")
async def update_org_competitors(
    body: UpdateCompetitorListRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Admin-only. Replaces the org-wide watchlist atomically. Invalidates
    the per-request detector cache so the next chat turn uses the new
    list within the writer's worker; other workers age out via TTL."""
    user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)

    me = await asyncio.to_thread(
        lambda: client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not me.data or me.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can change the org competitor list.",
        )

    cleaned = _clean_competitor_list(body.names, cap=ORG_COMPETITORS_MAX)
    result = await asyncio.to_thread(
        lambda: client.table("organizations")
        .update({"competitor_names": cleaned})
        .eq("id", org_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Workspace not found.")

    from app.services.competitor_detector import invalidate as _invalidate
    _invalidate(org_id)

    return {"names": cleaned, "max": ORG_COMPETITORS_MAX}


@router.get("/users/me/competitors")
async def get_user_competitors(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Read the caller's personal watchlist. Every user has one; the list
    is empty by default."""
    user_id, _org_id, token = _require_user(current_user)
    client = get_user_client(token)
    result = await asyncio.to_thread(
        lambda: client.table("users")
        .select("competitor_names")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        raise HTTPException(status_code=404, detail="User not found.")
    return {
        "names": result.data.get("competitor_names") or [],
        "max": USER_COMPETITORS_MAX,
    }


@router.put("/users/me/competitors")
async def update_user_competitors(
    body: UpdateCompetitorListRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """All authenticated users can set their own watchlist."""
    user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)

    cleaned = _clean_competitor_list(body.names, cap=USER_COMPETITORS_MAX)
    result = await asyncio.to_thread(
        lambda: client.table("users")
        .update({"competitor_names": cleaned})
        .eq("id", user_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="User not found.")

    from app.services.competitor_detector import invalidate as _invalidate
    _invalidate(org_id, user_id)

    return {"names": cleaned, "max": USER_COMPETITORS_MAX}


# ── Org-wide AI settings (Day 9 / #67) ────────────────────────────────────────


@router.get("/organizations/me/ai-settings")
async def get_org_ai_settings(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)
    result = await asyncio.to_thread(
        lambda: client.table("organizations")
        .select("ai_instructions")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return {"ai_instructions": result.data.get("ai_instructions") or ""}


@router.patch("/organizations/me/ai-settings")
async def update_org_ai_settings(
    body: UpdateOrgSettingsRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Admin-only. Set or clear the per-org AI instructions prepended to the
    LLM system prompt on every chat turn.

    Invalidates the in-process org_config cache for this process — other
    worker processes pick up the change within the cache TTL (~60s).
    """
    user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)

    me = await asyncio.to_thread(
        lambda: client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not me.data or me.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can change AI settings.",
        )

    cleaned: str | None
    raw = (body.ai_instructions or "").strip()
    cleaned = raw or None  # treat blank as a clear

    result = await asyncio.to_thread(
        lambda: client.table("organizations")
        .update({"ai_instructions": cleaned})
        .eq("id", org_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Workspace not found.")

    # Invalidate the cache so this worker picks the change up on the very
    # next chat call. Other workers age out via TTL.
    from app.services.org_config import invalidate

    invalidate(org_id)

    return {"ai_instructions": cleaned or ""}


# ── Org-wide sharing toggle (Day 4 / #62) ─────────────────────────────────────


@router.get("/organizations/me/sharing")
async def get_org_sharing(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)
    result = await asyncio.to_thread(
        lambda: client.table("organizations")
        .select("allow_output_sharing")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return {
        "allow_output_sharing": bool(result.data.get("allow_output_sharing", True)),
    }


@router.get("/organizations/me/archive-settings")
async def get_org_archive_settings(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Read the archive config block out of org metadata, with defaults."""
    _, org_id, token = _require_user(current_user)
    client = get_user_client(token)
    result = await asyncio.to_thread(
        lambda: client.table("organizations")
        .select("metadata")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    archive_cfg = (result.data.get("metadata") or {}).get("archive") or {}
    return {
        "auto_archive_enabled": bool(archive_cfg.get("auto_archive_enabled", True)),
        "threshold_days": int(archive_cfg.get("threshold_days") or 45),
        "delete_after_archive_days": archive_cfg.get("delete_after_archive_days"),
        "allowed_threshold_days": ARCHIVE_THRESHOLD_CHOICES,
    }


@router.patch("/organizations/me/archive-settings")
async def update_org_archive_settings(
    body: UpdateArchiveSettingsRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Admin-only. Update auto-archive + retention settings.

    We do a read-modify-write so we don't clobber other top-level keys living
    in ``metadata`` (onboarding, compliance, etc.). PostgREST's JSONB merge
    operator isn't exposed through the Python client, so this is the cleanest
    portable path.
    """
    user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)

    me = await asyncio.to_thread(
        lambda: client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not me.data or me.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can change archive settings.",
        )

    if (
        body.threshold_days is not None
        and body.threshold_days not in ARCHIVE_THRESHOLD_CHOICES
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"threshold_days must be one of {ARCHIVE_THRESHOLD_CHOICES}.",
        )

    existing = await asyncio.to_thread(
        lambda: client.table("organizations")
        .select("metadata")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    if not existing or not existing.data:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    metadata: dict[str, Any] = dict(existing.data.get("metadata") or {})
    archive_cfg: dict[str, Any] = dict(metadata.get("archive") or {})

    if body.auto_archive_enabled is not None:
        archive_cfg["auto_archive_enabled"] = body.auto_archive_enabled
    if body.threshold_days is not None:
        archive_cfg["threshold_days"] = body.threshold_days
    if body.clear_delete_retention:
        archive_cfg.pop("delete_after_archive_days", None)
    elif body.delete_after_archive_days is not None:
        archive_cfg["delete_after_archive_days"] = body.delete_after_archive_days

    metadata["archive"] = archive_cfg
    await asyncio.to_thread(
        lambda: client.table("organizations")
        .update({"metadata": metadata})
        .eq("id", org_id)
        .execute()
    )
    return {
        "auto_archive_enabled": bool(archive_cfg.get("auto_archive_enabled", True)),
        "threshold_days": int(archive_cfg.get("threshold_days") or 45),
        "delete_after_archive_days": archive_cfg.get("delete_after_archive_days"),
        "allowed_threshold_days": ARCHIVE_THRESHOLD_CHOICES,
    }


def _steps_payload(steps: org_config.OnboardingStepConfig) -> dict[str, bool]:
    """The wire shape both onboarding-steps endpoints return."""
    return {
        "bgv": steps.bgv,
        "appointment_bundle": steps.appointment_bundle,
        "policies": steps.policies,
        "induction": steps.induction,
        "configured": steps.configured,
    }


@router.get("/organizations/me/onboarding-steps")
async def get_org_onboarding_steps(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, bool]:
    """Which optional steps the onboarding pipeline runs for this org.

    `configured` is false until someone saves a choice, which is how the
    onboarding page knows to show its first-run setup screen instead of the
    runs list.
    """
    _, org_id, _ = _require_user(current_user)
    steps = await org_config.get_onboarding_steps(org_id)
    return _steps_payload(steps)


@router.patch("/organizations/me/onboarding-steps")
async def update_org_onboarding_steps(
    body: UpdateOnboardingStepsRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, bool]:
    """Admin-only. Turn optional onboarding steps on or off.

    Goes through `org_config` rather than writing `organizations.metadata`
    directly like the archive settings above: the onboarding agent reads these
    flags through that module's TTL cache, and `update_onboarding_steps` is
    what invalidates it.
    """
    user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)

    me = await asyncio.to_thread(
        lambda: client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not me.data or me.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can change onboarding steps.",
        )

    steps = await org_config.update_onboarding_steps(
        org_id=org_id,
        bgv=body.bgv,
        appointment_bundle=body.appointment_bundle,
        policies=body.policies,
        induction=body.induction,
    )
    return _steps_payload(steps)


@router.patch("/organizations/me/sharing")
async def update_org_sharing(
    body: UpdateOrgSharingRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Admin-only. Toggle whether team members can mint public share links."""
    user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)

    me = await asyncio.to_thread(
        lambda: client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not me.data or me.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can change sharing settings.",
        )

    result = await asyncio.to_thread(
        lambda: client.table("organizations")
        .update({"allow_output_sharing": body.allow_output_sharing})
        .eq("id", org_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return {"allow_output_sharing": body.allow_output_sharing}


# ── V3 #91 — Query history ─────────────────────────────────────────────────

@router.get("/users/me/query-history")
async def get_my_query_history(
    cursor: str | None = None,
    limit: int = 20,
    intent: str | None = None,
    search: str | None = None,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Cursor-paginated query history for the calling user.

    `cursor` is the `created_at` of the last row in the previous page.
    Limit is clamped server-side at 100. Filters:
      * `intent` — exact match against the classifier label
      * `search` — case-insensitive substring on `query_text`

    RLS guarantees the user only ever sees their own rows.
    """
    from app.services.query_logs import fetch_query_history

    user_id, _org_id, token = _require_user(current_user)
    if limit < 1 or limit > 100:
        limit = max(1, min(100, limit))

    result = await asyncio.to_thread(
        lambda: fetch_query_history(
            user_jwt=token,
            user_id=user_id,
            cursor=cursor,
            limit=limit,
            intent=intent,
            search=search,
        )
    )
    return result


# ── User profile ──────────────────────────────────────────────────────────────

@router.patch("/users/me")
async def update_profile(
    body: UpdateProfileRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, _org_id, token = _require_user(current_user)
    client = get_user_client(token)

    update: dict[str, Any] = {}
    if body.display_name is not None:
        cleaned = " ".join(body.display_name.split()).strip()
        if not cleaned:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Display name cannot be empty.",
            )
        update["display_name"] = cleaned
    if body.activity_private is not None:
        update["activity_private"] = body.activity_private
    if body.clear_persona:
        update["persona"] = None
        # Also wipe the custom payload so a later switch back to 'custom'
        # doesn't resurrect stale instructions the user thought they cleared.
        update["custom_persona_name"] = None
        update["custom_persona_instructions"] = None
    elif body.persona is not None:
        persona = body.persona.strip()
        builtin = {"hr", "sales", "engineering", "finance", "operations", "executive"}
        valid_builtin = persona.lower() in builtin
        valid_custom = persona == "custom"
        # org:<uuid> form. Match the same regex as the DB CHECK so the SQL
        # layer never has to reject a value we already accepted here.
        valid_org = bool(re.fullmatch(
            r"org:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            persona,
        ))
        if not (valid_builtin or valid_custom or valid_org):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Persona must be a built-in role, 'custom', or 'org:<uuid>'."
                ),
            )
        update["persona"] = persona.lower() if valid_builtin else persona

    # Custom persona fields — accept independently so the user can edit
    # their saved custom prompt without flipping the persona kind. The DB
    # CHECK constraints enforce length bounds; we mirror the friendly
    # message here to avoid a 500 if a client bypasses the UI cap.
    if body.custom_persona_name is not None:
        name = body.custom_persona_name.strip()
        update["custom_persona_name"] = name or None
    if body.custom_persona_instructions is not None:
        instr = body.custom_persona_instructions.strip()
        if instr and len(instr) < 10:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Custom persona instructions must be at least 10 characters.",
            )
        update["custom_persona_instructions"] = instr or None

    if not update:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes supplied.",
        )

    result = await asyncio.to_thread(
        lambda: client.table("users")
        .update(update)
        .eq("id", user_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found.",
        )
    return {"user": result.data[0]}


# ── Account deletion ──────────────────────────────────────────────────────────

@router.delete("/users/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    body: DeleteAccountRequest,
    current_user: dict = Depends(verify_jwt),
) -> None:
    """Delete the caller's auth identity and all org data they own.

    Semantics:
    - If the caller is the sole admin of the org → delete the org row, which
      cascades to documents, chunks, embeddings, conversations, messages.
    - If other admins exist → only delete the caller's user row + auth identity;
      the org keeps running.
    - Storage objects under `orgs/{org_id}/` are removed best-effort.
    """
    user_id, org_id, _token = _require_user(current_user)
    svc = get_service_client()

    # Confirm org-name typed by user (UI requires it; server enforces it).
    org_row = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select("id, name")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    if not org_row.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found.")

    expected = org_row.data["name"].strip().lower()
    provided = body.confirm_org_name.strip().lower()
    if expected != provided:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace name does not match. Type the exact name to confirm.",
        )

    # Count admins to decide single-admin vs. multi-admin path.
    admins = await asyncio.to_thread(
        lambda: svc.table("users")
        .select("id", count="exact")
        .eq("org_id", org_id)
        .eq("role", "admin")
        .execute()
    )
    admin_count = admins.count or 0
    me_row = next((row for row in (admins.data or []) if row["id"] == user_id), None)
    is_sole_admin = admin_count <= 1 and me_row is not None

    if is_sole_admin:
        # Delete storage objects for the whole org, then drop the org row.
        # Cascade removes documents, conversations, messages, embeddings, users.
        await _wipe_org_storage(svc, org_id)
        await asyncio.to_thread(
            lambda: svc.table("organizations").delete().eq("id", org_id).execute()
        )
    else:
        # Just drop this user's profile row; auth.users delete below cascades to
        # owner-of records only where we use ON DELETE SET NULL.
        await asyncio.to_thread(
            lambda: svc.table("users").delete().eq("id", user_id).execute()
        )

    # Delete the Supabase auth identity. Without this, the email/password
    # remains usable to sign back in — which would be confusing UX.
    try:
        await asyncio.to_thread(lambda: svc.auth.admin.delete_user(user_id))
    except Exception as exc:
        # Surface the failure — leaving an orphan auth identity is the worse
        # outcome (re-sign-in would land in a broken state with no profile row).
        log.error("Failed to delete auth user %s: %s", user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete account. Please contact support.",
        ) from exc


# ── Admin: feedback stats ─────────────────────────────────────────────────────

@router.get("/admin/feedback-stats")
async def admin_feedback_stats(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Aggregated thumbs-up/down counts for the caller's workspace.

    Admin-only. Powers the 'Feedback signal' card on Settings → Workspace.
    Verifies admin role via the user-scoped client (so a non-admin can't
    forge their way past), then issues the cross-user count with the
    service-role client. We can't use the user-scoped client for the count
    because messages RLS was tightened (043) to only return rows from
    conversations the caller owns — counting an org's full feedback signal
    requires bypassing per-user scoping.
    """
    user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)

    me = await asyncio.to_thread(
        lambda: client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can view feedback analytics.",
        )

    svc = get_service_client()

    async def _count(filter_value: str | None) -> int:
        def _run() -> int:
            q = (
                svc.table("messages")
                .select("id", count="exact", head=True)
                .eq("org_id", org_id)
                .eq("role", "assistant")
            )
            q = q.eq("feedback", filter_value) if filter_value else q.is_("feedback", "null")
            res = q.execute()
            return res.count or 0

        return await asyncio.to_thread(_run)

    positive, negative, unrated = await asyncio.gather(
        _count("positive"),
        _count("negative"),
        _count(None),
    )

    return {
        "positive": positive,
        "negative": negative,
        "unrated": unrated,
        "total": positive + negative + unrated,
    }


# ── API keys (Day 15 / #47) ──────────────────────────────────────────────────

class CreateApiKeyBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    scope: str = Field(default="org", pattern="^(org|user)$")


@router.get("/settings/api-keys")
async def list_api_keys(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, org_id, token = _require_user(current_user)
    # Members can see keys but only admins can create/revoke. Visibility is
    # safe because we never expose key_hash or anything reversible.
    from app.services.api_keys import list_keys
    keys = await list_keys(org_id=org_id)
    return {"keys": keys}


@router.post("/settings/api-keys", status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: CreateApiKeyBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)
    me = await asyncio.to_thread(
        lambda: client.table("users").select("role").eq("id", user_id).maybe_single().execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can create API keys.",
        )

    cleaned = " ".join(body.name.split()).strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Name cannot be empty.")

    from app.services.api_keys import create_key
    issued = await create_key(
        org_id=org_id, user_id=user_id, name=cleaned, scope=body.scope
    )
    # The full key leaves the server here and never again.
    return {
        "id": issued.id,
        "name": issued.name,
        "scope": issued.scope,
        "key": issued.full_key,
        "key_prefix": issued.key_prefix,
        "created_at": issued.created_at,
    }


@router.delete("/settings/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: str,
    current_user: dict = Depends(verify_jwt),
) -> None:
    user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)
    me = await asyncio.to_thread(
        lambda: client.table("users").select("role").eq("id", user_id).maybe_single().execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can revoke API keys.",
        )
    from app.services.api_keys import revoke_key
    ok = await revoke_key(org_id=org_id, key_id=key_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Key not found or already revoked.")


# ── GDPR data export (Day 11 / production roadmap) ──────────────────────────


def _export_filename(prefix: str, identifier: str) -> str:
    # Compact ISO date so the filename sorts cleanly when a user has
    # multiple exports in their downloads folder.
    stamp = re.sub(r"[^0-9]", "", datetime.now(UTC).isoformat(timespec="seconds"))
    # Trim the identifier to avoid producing 200-char filenames on Windows.
    short_id = identifier.replace("-", "")[:8]
    return f"contexo-{prefix}-{short_id}-{stamp}.zip"


def _too_many_exports(used: int, limit: int, seconds_until_reset: int) -> HTTPException:
    hours = max(seconds_until_reset // 3600, 1)
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=(
            f"Data export limit reached ({used}/{limit} in the last 24 hours). "
            f"Try again in about {hours} hour(s)."
        ),
        headers={"Retry-After": str(max(seconds_until_reset, 60))},
    )


@router.get("/users/me/export")
async def export_my_data(
    current_user: dict = Depends(verify_jwt),
) -> Response:
    """Download a ZIP archive of the calling user's personal data.

    Returns ``application/zip`` with ``Content-Disposition: attachment``.
    Rate-limited to 3 exports per 24h per user. Strictly scoped to the
    requesting user — there is no path or query parameter that would
    allow targeting a different user, and the route uses the user-scoped
    Supabase client so RLS is the security boundary even if app filters
    were bypassed.

    See ``app/services/data_export.py`` for the full inclusion / exclusion list.
    """
    from app.services.data_export import (
        build_user_export,
        check_export_quota,
    )

    user_id, org_id, token = _require_user(current_user)

    quota = await check_export_quota(namespace="user", identifier=user_id, limit=3)
    if not quota.allowed:
        raise _too_many_exports(quota.used, quota.limit, quota.seconds_until_reset)

    # Look up the caller's email via the service client (auth.users isn't
    # readable via the user JWT). We include it in profile.json so the
    # archive is self-describing without joining against external data.
    email: str | None = None
    try:
        svc = get_service_client()
        au = await asyncio.to_thread(lambda: svc.auth.admin.get_user_by_id(user_id))
        email = getattr(getattr(au, "user", None), "email", None)
    except Exception as exc:
        log.warning("export_email_lookup_failed user=%s err=%s", user_id, exc)

    log.info(
        "gdpr_export_user_started user=%s org=%s quota_used=%s/%s",
        user_id, org_id, quota.used, quota.limit,
    )

    try:
        payload = await build_user_export(
            user_id=user_id, org_id=org_id, token=token, email=email
        )
    except Exception as exc:
        log.error("gdpr_export_user_failed user=%s err=%s", user_id, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Couldn't build your data export. Please try again in a moment.",
        ) from exc

    log.info(
        "gdpr_export_user_completed user=%s bytes=%s",
        user_id, len(payload),
    )

    filename = _export_filename("export", user_id)
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store, private",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/organizations/me/export")
async def export_my_organization(
    current_user: dict = Depends(verify_jwt),
) -> Response:
    """Admin-only ZIP archive of the entire workspace.

    Rate-limited to 1 per 24h per workspace (heavier scope than personal
    export). Uses the service-role client because the calling admin
    legitimately needs to read rows authored by other members — which
    RLS correctly blocks under the user-scoped client.

    Includes user/document/conversation/message/integration metadata.
    Excludes Stripe customer/subscription IDs, OAuth tokens, API key
    material, embedding vectors, and document binary content. See
    ``app/services/data_export.py`` for the full exclusion list.
    """
    from app.services.data_export import (
        build_org_export,
        check_export_quota,
    )

    user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)

    me = await asyncio.to_thread(
        lambda: client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not me.data or me.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can export workspace data.",
        )

    quota = await check_export_quota(namespace="org", identifier=org_id, limit=1)
    if not quota.allowed:
        raise _too_many_exports(quota.used, quota.limit, quota.seconds_until_reset)

    log.info(
        "gdpr_export_org_started org=%s requested_by=%s quota_used=%s/%s",
        org_id, user_id, quota.used, quota.limit,
    )

    try:
        payload = await build_org_export(org_id=org_id, requested_by=user_id)
    except Exception as exc:
        log.error(
            "gdpr_export_org_failed org=%s requested_by=%s err=%s",
            org_id, user_id, exc, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Couldn't build the workspace export. Please try again in a moment.",
        ) from exc

    log.info(
        "gdpr_export_org_completed org=%s requested_by=%s bytes=%s",
        org_id, user_id, len(payload),
    )

    filename = _export_filename("workspace-export", org_id)
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store, private",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def _wipe_org_storage(svc, org_id: str) -> None:
    """Best-effort removal of org storage tree. Failures don't block deletion."""
    from app.routers.documents import STORAGE_BUCKET

    try:
        # List recursively under the org prefix.
        prefix = f"orgs/{org_id}"
        listing = await asyncio.to_thread(
            lambda: svc.storage.from_(STORAGE_BUCKET).list(prefix)
        )
        # `list` is shallow; we follow up by removing whole doc folders.
        doc_paths: list[str] = []
        for entry in listing or []:
            doc_id = entry.get("name")
            if not doc_id:
                continue
            sub = await asyncio.to_thread(
                lambda: svc.storage.from_(STORAGE_BUCKET).list(f"{prefix}/{doc_id}")
            )
            for f in sub or []:
                fname = f.get("name")
                if fname:
                    doc_paths.append(f"{prefix}/{doc_id}/{fname}")

        if doc_paths:
            await asyncio.to_thread(
                lambda: svc.storage.from_(STORAGE_BUCKET).remove(doc_paths)
            )
    except Exception as exc:
        log.warning("Storage cleanup for org %s failed: %s", org_id, exc)
