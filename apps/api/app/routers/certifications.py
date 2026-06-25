"""Knowledge certification endpoints (Production Roadmap 2.10).

User-facing:
    GET    /certifications/documents/{document_id}/quiz   — fetch active quiz (answers stripped)
    POST   /certifications/quizzes/{quiz_id}/attempts     — submit answers
    GET    /certifications/documents/{document_id}/status — latest attempt

Admin:
    POST   /certifications/admin/documents/{document_id}/generate-quiz
    POST   /certifications/admin/documents/{document_id}/require   {required: bool}
    GET    /certifications/admin/report
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.services import certifications as cert_svc

router = APIRouter(prefix="/certifications", tags=["certifications"])


def _require_user(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise NoOrganization("No organization found.")
    return org_id, user_id, token


async def _require_admin(token: str, user_id: str) -> None:
    client = get_user_client(token)
    me = await asyncio.to_thread(
        lambda: client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only.")


# ── Schemas ────────────────────────────────────────────────────────────────


class AnswerItem(BaseModel):
    question_id: str = Field(..., min_length=8, max_length=64)
    selected_index: int = Field(..., ge=0, le=3)


class SubmitAttemptBody(BaseModel):
    answers: list[AnswerItem] = Field(..., min_length=1, max_length=50)


class GenerateQuizBody(BaseModel):
    question_count: int = Field(default=5, ge=3, le=20)
    passing_score: float = Field(default=0.80, ge=0.5, le=1.0)


class RequireCertificationBody(BaseModel):
    required: bool


# ── User endpoints ────────────────────────────────────────────────────────


@router.get("/documents/{document_id}/quiz")
async def get_quiz(
    document_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _, _ = _require_user(current_user)
    quiz = await cert_svc.get_active_quiz_for_document(
        org_id=org_id, document_id=document_id
    )
    if not quiz:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No active quiz exists for this document.",
        )
    return cert_svc._strip_answers(quiz)


@router.post("/quizzes/{quiz_id}/attempts")
async def submit_attempt(
    quiz_id: str,
    body: SubmitAttemptBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, _ = _require_user(current_user)
    try:
        return await cert_svc.submit_attempt(
            org_id=org_id,
            user_id=user_id,
            quiz_id=quiz_id,
            answers=[a.model_dump() for a in body.answers],
        )
    except PermissionError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.get("/documents/{document_id}/status")
async def my_status(
    document_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, _ = _require_user(current_user)
    return await cert_svc.get_user_certification_status(
        org_id=org_id, user_id=user_id, document_id=document_id
    )


# ── Admin endpoints ───────────────────────────────────────────────────────


@router.post("/admin/documents/{document_id}/generate-quiz")
async def generate_quiz(
    document_id: str,
    body: GenerateQuizBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_user(current_user)
    await _require_admin(token, user_id)

    # Resolve current_version_id (optional).
    svc = get_service_client()

    def _doc() -> dict[str, Any] | None:
        res = (
            svc.table("documents")
            .select("id, current_version_id")
            .eq("id", document_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    doc = await asyncio.to_thread(_doc)
    if not doc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Document not found."
        )

    try:
        quiz = await cert_svc.generate_quiz(
            org_id=org_id,
            document_id=document_id,
            document_version_id=doc.get("current_version_id"),
            created_by=user_id,
            question_count=body.question_count,
            passing_score=body.passing_score,
        )
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"Quiz generation failed: {exc}",
        ) from exc

    # Return the full quiz to the admin (correct_index included) so they can
    # review before announcing the certification requirement.
    return {
        "id": quiz["id"],
        "document_id": quiz["document_id"],
        "passing_score": quiz["passing_score"],
        "question_count": quiz["question_count"],
        "questions": quiz["questions"],
        "is_active": quiz.get("is_active"),
        "created_at": quiz.get("created_at"),
    }


@router.post("/admin/documents/{document_id}/require")
async def set_require_certification(
    document_id: str,
    body: RequireCertificationBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Flip documents.requires_certification.

    Implies requires_acknowledgement=true so the existing compliance
    propagation agent fans out pending ack rows to every member.
    """
    org_id, user_id, token = _require_user(current_user)
    await _require_admin(token, user_id)

    svc = get_service_client()
    updates: dict[str, Any] = {"requires_certification": body.required}
    if body.required:
        updates["requires_acknowledgement"] = True

    def _apply() -> dict[str, Any] | None:
        res = (
            svc.table("documents")
            .update(updates)
            .eq("id", document_id)
            .eq("org_id", org_id)
            .execute()
        )
        return (res.data or [None])[0]

    row = await asyncio.to_thread(_apply)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    return {"document_id": document_id, **updates}


@router.get("/admin/report")
async def report(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_user(current_user)
    await _require_admin(token, user_id)
    return await cert_svc.admin_report(org_id=org_id)
