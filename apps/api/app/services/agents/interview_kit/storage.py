"""DB helpers for InterviewKitAgent. Service-role only — every caller is
either an Inngest worker or an admin-gated FastAPI handler that's already
verified the org / user."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.database import get_service_client


async def fetch_requisition(*, org_id: str, requisition_id: str) -> dict[str, Any] | None:
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("job_requisitions")
        .select("*")
        .eq("id", requisition_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    return res.data if res and res.data else None


async def create_kit_row(
    *,
    org_id: str,
    requisition_id: str,
    created_by: str,
    run_id: str,
    jd_variant_index: int | None,
) -> str:
    """Insert the kit row in 'generating' state. The agent fills it in step
    by step and flips status to 'ready' on completion."""
    svc = get_service_client()
    row = {
        "org_id": org_id,
        "requisition_id": requisition_id,
        "created_by": created_by,
        "run_id": run_id,
        "jd_variant_index": jd_variant_index,
        "status": "generating",
    }
    res = await asyncio.to_thread(
        lambda: svc.table("interview_kits").insert(row).execute()
    )
    return res.data[0]["id"]


async def update_kit_row(
    *,
    kit_id: str,
    patch: dict[str, Any],
) -> None:
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("interview_kits").update(patch).eq("id", kit_id).execute()
    )


async def mark_kit_failed(*, kit_id: str, error: str) -> None:
    await update_kit_row(
        kit_id=kit_id,
        patch={"status": "failed", "error_message": (error or "")[:2000]},
    )


async def mark_kit_ready(
    *,
    kit_id: str,
    role_title: str | None,
    seniority_level: str | None,
    competencies: list[dict[str, Any]],
    panels: list[dict[str, Any]],
    scorecard: list[dict[str, Any]],
    reference_questions: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> None:
    await update_kit_row(
        kit_id=kit_id,
        patch={
            "status": "ready",
            "role_title": role_title,
            "seniority_level": seniority_level,
            "competencies": competencies,
            "panels": panels,
            "scorecard": scorecard,
            "reference_questions": reference_questions,
            "sources": sources,
            "generated_at": datetime.now(UTC).isoformat(),
        },
    )


async def fetch_kit(*, org_id: str, kit_id: str) -> dict[str, Any] | None:
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("interview_kits")
        .select("*")
        .eq("id", kit_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    return res.data if res and res.data else None


async def list_kits_for_requisition(
    *, org_id: str, requisition_id: str
) -> list[dict[str, Any]]:
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("interview_kits")
        .select("*")
        .eq("org_id", org_id)
        .eq("requisition_id", requisition_id)
        .order("created_at", desc=True)
        .execute()
    )
    return res.data or []
