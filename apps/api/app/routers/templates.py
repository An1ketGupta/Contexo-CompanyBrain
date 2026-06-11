"""Prompt template library — V3 #20.

Three visibility tiers (builtin → org-shared → private) all live in one table;
RLS does the gating. The Python layer's job is to validate input, enforce the
category enum at the API boundary (the DB CHECK is the defence in depth), and
expose a single sorted feed for the popover UI.

Use-count tracking deliberately uses an unauthenticated-against-the-row write
via the service-role client — the popover should increment "I picked this
template" even if the template is a builtin or org-shared row the caller
couldn't UPDATE under RLS. The endpoint still requires a valid JWT, so it's
not abusable beyond what a logged-in user could already do (spam-click their
own popover). Capped at TEMPLATE_USE_MAX_RATE in the rate-limit layer if abuse
shows up; for now Inngest's tracing surfaces any anomaly.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client

# {{snake_case_name}} placeholders. Names must be Python-identifier-shaped so
# `.format()` could substitute them; we use a literal scan instead but keep
# the same shape for portability.
_VARIABLE_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def _extract_variables(text: str) -> list[dict[str, Any]]:
    """Find {{name}} placeholders, dedupe preserving order, hydrate to the
    storage shape used by `prompt_templates.variables`."""
    seen: list[str] = []
    for match in _VARIABLE_PATTERN.finditer(text or ""):
        name = match.group(1)
        if name not in seen:
            seen.append(name)
    return [
        {
            "name": name,
            "label": name.replace("_", " ").strip().title() or name,
            "placeholder": "",
            "required": True,
        }
        for name in seen
    ]

log = logging.getLogger(__name__)

router = APIRouter(prefix="/templates", tags=["templates"])

# Mirrors the CHECK constraint in migration 018. Kept here so Pydantic rejects
# bad categories before they ever touch the DB.
TemplateCategory = Literal[
    "Email",
    "Job Description",
    "Announcement",
    "Policy Q&A",
    "Meeting Prep",
    "Customer Response",
    "Slack Reply",
    "Other",
]

TITLE_MAX = 120
DESC_MAX = 280
TEMPLATE_MAX = 8000

# Templates returned per request. The popover paginates naturally by category
# tabs, and 200 is plenty for the longest realistic org. The hard cap defends
# the JSON serializer if someone bulk-imports.
LIST_LIMIT = 200


class TemplateVariable(BaseModel):
    """V4 #70 — {{variable}} placeholder definition."""

    name: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    label: str = Field(default="", max_length=120)
    placeholder: str = Field(default="", max_length=240)
    required: bool = True


class TemplateBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=TITLE_MAX)
    description: str | None = Field(default=None, max_length=DESC_MAX)
    template_text: str = Field(..., min_length=1, max_length=TEMPLATE_MAX)
    category: TemplateCategory = "Other"
    is_shared: bool = False
    # V4 #70 — caller may pass explicit variables. If omitted, we auto-extract
    # from `template_text` on the server. Cap at 20 to keep the use-dialog
    # usable; anything beyond is a different kind of UI need.
    variables: list[TemplateVariable] | None = Field(default=None, max_length=20)

    @field_validator("title", "template_text")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class TemplateUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX)
    description: str | None = Field(default=None, max_length=DESC_MAX)
    template_text: str | None = Field(
        default=None, min_length=1, max_length=TEMPLATE_MAX
    )
    category: TemplateCategory | None = None
    is_shared: bool | None = None
    variables: list[TemplateVariable] | None = Field(default=None, max_length=20)


class UseTemplateBody(BaseModel):
    """V4 #70 — variable substitution payload."""

    # Each key matches a TemplateVariable.name. Empty string allowed only for
    # optional variables; the resolver re-checks against `required` after sub.
    variable_values: dict[str, str] = Field(default_factory=dict)

    @field_validator("variable_values")
    @classmethod
    def _bound_values(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 50:
            raise ValueError("Too many variable values.")
        out: dict[str, str] = {}
        for name, value in v.items():
            if not isinstance(name, str) or not _VARIABLE_PATTERN.fullmatch(
                f"{{{{{name}}}}}"
            ):
                # Defensive: discard non-identifier keys silently so a malformed
                # payload doesn't 422 the whole request.
                continue
            out[name] = (value or "")[:2_000]
        return out


def _to_dict(row: dict[str, Any]) -> dict[str, Any]:
    """Trim the row for over-the-wire — keep the API stable even as we add
    internal columns later (use_count_by_user, last_used_at, etc.)."""
    # V4 #70 — backfill `variables` from template_text for rows that pre-date
    # the column (builtins seeded in 018) so the use-dialog always has a
    # consistent shape.
    variables = row.get("variables")
    if not variables:
        variables = _extract_variables(row.get("template_text") or "")
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row.get("description"),
        "template_text": row["template_text"],
        "category": row["category"],
        "is_shared": row.get("is_shared", False),
        "is_builtin": row.get("is_builtin", False),
        "use_count": row.get("use_count", 0),
        "variables": variables,
        "org_id": row.get("org_id"),
        "created_by": row.get("created_by"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


@router.get("")
async def list_templates(
    category: TemplateCategory | None = Query(default=None),
    search: str | None = Query(default=None, max_length=120),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Return every template the caller can see, ordered by popularity then
    recency. RLS handles the visibility logic; we just sort + paginate.

    `search` is a case-insensitive substring match on title. We don't search
    template_text — the title is the canonical handle the user picks from.
    """
    if not current_user.get("org_id"):
        return {"templates": []}

    client = get_user_client(current_user["token"])

    def _run() -> list[dict[str, Any]]:
        q = client.table("prompt_templates").select(
            "id, org_id, created_by, title, description, template_text, "
            "category, is_shared, is_builtin, use_count, variables, created_at, updated_at"
        )
        if category:
            q = q.eq("category", category)
        if search:
            safe = search.replace("%", r"\%")
            q = q.ilike("title", f"%{safe}%")
        # Most-used first; ties broken by recency so the latest user-created
        # template surfaces above an older one with the same use_count.
        q = q.order("use_count", desc=True).order("created_at", desc=True)
        q = q.limit(LIST_LIMIT)
        res = q.execute()
        return res.data or []

    rows = await asyncio.to_thread(_run)
    return {"templates": [_to_dict(r) for r in rows]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_template(
    body: TemplateBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Create a new template for the caller's org. is_shared toggles the
    org-wide vs private flag; visibility is enforced by RLS on read."""
    org_id: str | None = current_user.get("org_id")
    user_id: str = current_user["user_id"]
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No organization found."
        )

    client = get_user_client(current_user["token"])

    if body.variables is not None:
        # Trust the explicit list, but only if every placeholder it names is
        # actually present in the template_text — otherwise we'd ship dead
        # variables the use-dialog would render and then never substitute.
        named = {v.name for v in body.variables}
        present = {m.group(1) for m in _VARIABLE_PATTERN.finditer(body.template_text)}
        stored_variables = [
            {
                "name": v.name,
                "label": (v.label or v.name.replace("_", " ").title()),
                "placeholder": v.placeholder,
                "required": v.required,
            }
            for v in body.variables
            if v.name in present
        ]
        # Any placeholders in the text that the caller didn't define get a
        # required entry auto-added.
        for extra in present - named:
            stored_variables.append({
                "name": extra,
                "label": extra.replace("_", " ").title(),
                "placeholder": "",
                "required": True,
            })
    else:
        stored_variables = _extract_variables(body.template_text)

    payload = {
        "org_id": org_id,
        "created_by": user_id,
        "title": body.title,
        "description": body.description,
        "template_text": body.template_text,
        "category": body.category,
        "is_shared": body.is_shared,
        "is_builtin": False,
        "variables": stored_variables,
    }

    def _run() -> dict[str, Any]:
        res = client.table("prompt_templates").insert(payload).execute()
        rows = res.data or []
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Insert returned no row.",
            )
        return rows[0]

    row = await asyncio.to_thread(_run)
    return {"template": _to_dict(row)}


@router.patch("/{template_id}")
async def update_template(
    template_id: str,
    body: TemplateUpdate,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Edit a non-builtin template. RLS rejects edits to builtins or to rows
    the caller doesn't own / isn't an admin for, so we don't double-check here
    — the empty result-set from PostgREST is the auth signal."""
    if not current_user.get("org_id"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No organization found."
        )
    _require_uuid(template_id)

    update: dict[str, Any] = {}
    if body.title is not None:
        update["title"] = body.title.strip()
    if body.description is not None:
        update["description"] = body.description
    if body.template_text is not None:
        update["template_text"] = body.template_text.strip()
        # Whenever the text changes, re-derive variables to keep them in sync
        # unless the caller also supplied an explicit list below.
        if body.variables is None:
            update["variables"] = _extract_variables(update["template_text"])
    if body.category is not None:
        update["category"] = body.category
    if body.is_shared is not None:
        update["is_shared"] = body.is_shared
    if body.variables is not None:
        update["variables"] = [
            {
                "name": v.name,
                "label": v.label or v.name.replace("_", " ").title(),
                "placeholder": v.placeholder,
                "required": v.required,
            }
            for v in body.variables
        ]

    if not update:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields supplied to update.",
        )

    client = get_user_client(current_user["token"])
    res = await asyncio.to_thread(
        lambda: client.table("prompt_templates")
        .update(update)
        .eq("id", template_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found or not editable.",
        )
    return {"template": _to_dict(res.data[0])}


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: str,
    current_user: dict = Depends(verify_jwt),
) -> None:
    if not current_user.get("org_id"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No organization found."
        )
    _require_uuid(template_id)

    client = get_user_client(current_user["token"])
    res = await asyncio.to_thread(
        lambda: client.table("prompt_templates")
        .delete()
        .eq("id", template_id)
        .execute()
    )
    if not res.data:
        # Deleting a row the user can't see / doesn't own is indistinguishable
        # from deleting a non-existent row, which is the correct privacy
        # posture for cross-org probing.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found or not deletable.",
        )


@router.post("/{template_id}/use")
async def record_template_use(
    template_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, int]:
    """Increment the use_count for popularity sorting.

    Uses the service role because builtins (org_id NULL) and org-shared rows
    owned by other users are not UPDATE-able under RLS. We still gate by
    verifying the caller can SELECT the row first — that *is* RLS-checked —
    so a random UUID guess returns 404, not a silent counter bump.
    """
    if not current_user.get("org_id"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No organization found."
        )
    _require_uuid(template_id)

    client = get_user_client(current_user["token"])
    # SELECT-then-UPDATE; the SELECT runs through RLS so we know the caller
    # is allowed to see (and therefore use) this template.
    visible = await asyncio.to_thread(
        lambda: client.table("prompt_templates")
        .select("id, use_count, title, category")
        .eq("id", template_id)
        .maybe_single()
        .execute()
    )
    if not visible or not visible.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found.",
        )
    current_count = int(visible.data.get("use_count") or 0)
    next_count = current_count + 1

    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("prompt_templates")
        .update({"use_count": next_count})
        .eq("id", template_id)
        .execute()
    )

    # V4 #18 / #57 — analytics + team activity. Wrapped: a telemetry failure
    # must not undo the counter bump we just persisted.
    try:
        from app.services.analytics import track_event
        from app.services.activity import log_activity, resolve_user_privacy

        org_id = current_user["org_id"]
        user_id = current_user.get("user_id")
        title = visible.data.get("title") or "a template"
        category = visible.data.get("category")
        await track_event(
            org_id=org_id,
            user_id=user_id,
            event_type="template_used",
            metadata={"template_id": template_id, "category": category},
        )
        if user_id:
            is_private = await resolve_user_privacy(user_id)
            await log_activity(
                org_id=org_id,
                user_id=user_id,
                activity_type="used_template",
                metadata={"template_id": template_id, "template_title": title},
                is_private=is_private,
            )
    except Exception:
        pass

    return {"use_count": next_count}


# ── V4 #70 — Resolve variables and seed a conversation ──────────────────────


@router.post("/{template_id}/resolve")
async def resolve_template(
    template_id: str,
    body: UseTemplateBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Substitute the supplied variable values into the template text and
    return the resolved prompt. The caller chooses how to use it — typically
    by pre-filling the chat composer.

    We deliberately do NOT create a conversation here. Conversations are owned
    by the chat router, which knows how to do scope, summary, idempotency, and
    the first user message. This endpoint stays a pure transform.

    Resolution semantics:
        * Missing required variable → 400 with the list of names.
        * Empty optional variable → placeholder stays in the text (a feature
          for "leave blank to skip" patterns the user types around).
        * Extra keys in body that aren't in the template — ignored silently.
    """
    if not current_user.get("org_id"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No organization found."
        )
    _require_uuid(template_id)

    client = get_user_client(current_user["token"])
    res = await asyncio.to_thread(
        lambda: client.table("prompt_templates")
        .select("id, title, template_text, variables")
        .eq("id", template_id)
        .maybe_single()
        .execute()
    )
    if not res or not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Template not found."
        )

    row = res.data
    template_text: str = row.get("template_text") or ""
    variables: list[dict[str, Any]] = list(row.get("variables") or [])
    # Backfill from text for legacy rows where the column was empty at write.
    if not variables:
        variables = _extract_variables(template_text)

    # Resolve required-vs-supplied. Names absent from `variable_values` or
    # supplied as empty count as missing for required variables.
    supplied = body.variable_values
    missing_required = [
        v["name"]
        for v in variables
        if v.get("required") and not (supplied.get(v["name"]) or "").strip()
    ]
    if missing_required:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Missing required variables: "
                + ", ".join(missing_required)
            ),
        )

    # Single-pass substitution over the placeholder pattern. Using replace()
    # inside a loop would be O(n*m) on the text length AND would double-sub
    # if a value itself contains `{{name}}` (untrusted user input).
    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in supplied:
            return supplied[name]
        # Optional variable with no supplied value → leave the placeholder.
        return match.group(0)

    resolved = _VARIABLE_PATTERN.sub(_sub, template_text)

    return {
        "template_id": template_id,
        "title": row.get("title"),
        "resolved_prompt": resolved,
    }


def _require_uuid(value: str) -> None:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid template id."
        ) from exc
