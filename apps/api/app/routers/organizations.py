"""Org-scoped endpoints introduced for V3 Day 1.

  * GET /organizations/document-status — counts of total / ready / processing
    docs. Backs the "no documents" / "still processing" banner shown above
    the chat input. Tiny, cheap, frequently polled.
  * GET /organizations/me              — basic org metadata + V5 enrichment.
  * POST /organizations/enrich         — admin-only V5 #97 enrichment write.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.observability import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.get("/document-status")
async def document_status(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Return doc counts the chat banner uses to decide its empty / processing
    state. Three states the UI cares about:

      * total = 0                                → "knowledge base empty"
      * total > 0 AND ready = 0 AND processing>0 → "documents still processing"
      * ready > 0                                → banner hidden

    We fold pending + processing into a single "processing" count because
    from the user's perspective both mean "not yet usable". Internally those
    are distinct (pending = pre-Inngest, processing = inside the pipeline)
    but exposing the distinction here would be a footgun for the banner copy.
    """
    org_id: str | None = current_user.get("org_id")
    if not org_id:
        return {"total": 0, "ready": 0, "processing": 0, "failed": 0, "has_ready": False}

    client = get_user_client(current_user["token"])

    # Four head-counts, one round-trip each. We *could* fetch one row of
    # status strings and tally in Python, but the GIN/B-tree on status is
    # already there from migration 001 and supabase-py's `head=True` ships
    # zero rows back. Four pings of <2ms each on a warm pool is fine.
    async def _count(status_value: str | None) -> int:
        def _run() -> int:
            q = client.table("documents").select("id", count="exact", head=True)
            if status_value is not None:
                q = q.eq("status", status_value)
            res = q.execute()
            return int(getattr(res, "count", 0) or 0)
        return await asyncio.to_thread(_run)

    total, ready, processing, pending, failed = await asyncio.gather(
        _count(None),
        _count("ready"),
        _count("processing"),
        _count("pending"),
        _count("failed"),
        return_exceptions=False,
    )

    return {
        "total": total,
        "ready": ready,
        "processing": processing + pending,
        "failed": failed,
        "has_ready": ready > 0,
    }


# ── V5 #97 — Org metadata enrichment ────────────────────────────────────────

# Allowed use cases. Open-coded (not an Enum) so adding a new one is a
# one-line change to the frontend modal — the DB column is plain TEXT so
# we don't need a migration to extend.
_USE_CASES = (
    "hr_policies",
    "sales_enablement",
    "customer_support",
    "engineering",
    "general",
)

# Sizes mirror the modal's options. We accept any string up to 20 chars to
# stay forward-compatible if the modal grows a bucket.
_SIZES = ("1-10", "11-50", "51-200", "200-1000", "1000+")


class OrgEnrichmentBody(BaseModel):
    industry: str = Field(..., min_length=1, max_length=80)
    company_size: str = Field(..., min_length=1, max_length=20)
    primary_use_case: Literal[
        "hr_policies",
        "sales_enablement",
        "customer_support",
        "engineering",
        "general",
    ]


@router.get("/me")
async def get_my_org(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Public-to-the-org metadata snapshot used by the dashboard. Surfaces
    V5 enrichment fields so the EnrichmentModal can decide whether to show
    itself, plus a couple of basics the sidebar already reads.
    """
    org_id = current_user.get("org_id")
    if not org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No organization found.")
    client = get_user_client(current_user["token"])
    res = await asyncio.to_thread(
        lambda: client.table("organizations")
        .select(
            "id, name, slug, plan, created_at, "
            "industry, company_size, primary_use_case, onboarding_completed_at"
        )
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    if not res or not res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found.")
    return res.data


@router.get("/recommendations")
async def get_recommendations(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """V3 #50 — recommended-documents checklist for the Documents-page widget.

    Returns the stored recommendations array (populated post-enrichment by the
    Inngest `org/post-enrichment` handler). Empty array for orgs that haven't
    gone through enrichment yet; the widget hides itself in that case.

    Each item: { key, name, description, why, examples, matched_document_id,
    matched_at, dismissed_at }.
    """
    org_id = current_user.get("org_id")
    if not org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No organization found.")
    client = get_user_client(current_user["token"])
    res = await asyncio.to_thread(
        lambda: client.table("organizations")
        .select("recommended_documents")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    if not res or not res.data:
        return {"recommendations": []}
    recs = res.data.get("recommended_documents") or []
    if not isinstance(recs, list):
        recs = []
    return {"recommendations": recs}


class DismissRecommendationBody(BaseModel):
    key: str = Field(..., min_length=1, max_length=80)


@router.post("/recommendations/dismiss")
async def dismiss_recommendation(
    body: DismissRecommendationBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Soft-dismiss a single recommendation by key. The row stays in the
    list (so the checklist still shows what's been skipped), it just won't
    be eligible for auto-match anymore.

    Any org member can dismiss — the recommendation list is org-scoped state,
    not an admin setting. If two people dismiss the same key in parallel,
    the second write wins (last-writer-wins on the JSONB array). Acceptable
    because the data is idempotent: both are setting the same dismissed_at.
    """
    org_id = current_user.get("org_id")
    if not org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No organization found.")

    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select("recommended_documents")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found.")
    recs = row.data.get("recommended_documents") or []
    if not isinstance(recs, list):
        raise HTTPException(status_code=400, detail="Recommendations not initialised.")

    updated = False
    now = datetime.now(timezone.utc).isoformat()
    for rec in recs:
        if rec.get("key") == body.key and not rec.get("dismissed_at"):
            rec["dismissed_at"] = now
            updated = True
            break

    if not updated:
        return {"dismissed": False, "reason": "not_found_or_already_dismissed"}

    await asyncio.to_thread(
        lambda: svc.table("organizations")
        .update({"recommended_documents": recs})
        .eq("id", org_id)
        .execute()
    )
    return {"dismissed": True, "key": body.key}


@router.post("/enrich")
async def enrich_org(
    body: OrgEnrichmentBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Persist the 3-step enrichment modal's answers. Admin-only.

    We don't strictly validate `industry` (the modal offers a curated list
    but admins can pick "Other") or `company_size` (mirrors the modal's set,
    but staying lenient lets us evolve the modal without a migration).
    """
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No organization found.")

    user_client = get_user_client(token)
    me = await asyncio.to_thread(
        lambda: user_client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only.")

    patch = {
        "industry": body.industry.strip(),
        "company_size": body.company_size.strip(),
        "primary_use_case": body.primary_use_case,
        "onboarding_completed_at": datetime.now(timezone.utc).isoformat(),
    }
    await asyncio.to_thread(
        lambda: user_client.table("organizations")
        .update(patch)
        .eq("id", org_id)
        .execute()
    )

    # Fire the post-enrichment Inngest event so background personalization
    # (suggesting templates, seeding AI instructions) can run async without
    # holding the HTTP response. Best-effort: an Inngest outage shouldn't
    # block the modal completing.
    try:
        from app.inngest import get_inngest_client
        import inngest as inngest_module  # type: ignore

        client = get_inngest_client()
        await client.send(
            inngest_module.Event(
                name="org/post-enrichment",
                data={
                    "org_id": org_id,
                    "industry": patch["industry"],
                    "company_size": patch["company_size"],
                    "primary_use_case": patch["primary_use_case"],
                },
            )
        )
    except Exception as exc:
        log.warning("post_enrichment_event_send_failed: %s", exc)

    return {"enriched": True, **patch}
