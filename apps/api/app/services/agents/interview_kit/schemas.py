"""Pydantic schemas for InterviewKitAgent payloads.

Used both for LLM output validation (lenient) and API responses (strict). We
keep them in one place so the FastAPI router and the agent can share types —
the agent's parsed JSON gets `.model_validate()`d before persistence.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

CompetencyKind = Literal["technical", "behavioral", "values", "cultural"]
SeniorityLevel = Literal["intern", "entry", "mid", "senior", "staff", "lead"]
ReferenceRelationship = Literal["manager", "peer", "report", "cross_functional"]
InterviewKitStatus = Literal["draft", "generating", "ready", "published", "failed"]


class Competency(BaseModel):
    name: str
    kind: CompetencyKind
    description: str
    weight: float = Field(0.1, ge=0.0, le=1.0)


class PanelQuestion(BaseModel):
    question: str
    competency: str
    kind: CompetencyKind
    follow_ups: list[str] = Field(default_factory=list)
    what_good_looks_like: str = ""


class InterviewPanel(BaseModel):
    stage: str
    panelist_role: str
    duration_minutes: int = Field(45, ge=15, le=180)
    focus: str
    questions: list[PanelQuestion]


class RubricLevel(BaseModel):
    score: int = Field(..., ge=1, le=5)
    label: str
    anchor: str
    behavioral_indicators: list[str] = Field(default_factory=list)


class CompetencyRubric(BaseModel):
    competency: str
    levels: list[RubricLevel]


class ReferenceQuestion(BaseModel):
    question: str
    competency: str
    why: str = ""


class ReferenceBank(BaseModel):
    relationship: ReferenceRelationship
    intro: str = ""
    questions: list[ReferenceQuestion]


class InterviewKitRead(BaseModel):
    id: str
    org_id: str
    requisition_id: str
    created_by: str
    run_id: str | None = None
    jd_variant_index: int | None = None
    role_title: str | None = None
    seniority_level: str | None = None
    competencies: list[Competency] = Field(default_factory=list)
    panels: list[InterviewPanel] = Field(default_factory=list)
    scorecard: list[CompetencyRubric] = Field(default_factory=list)
    reference_questions: list[ReferenceBank] = Field(default_factory=list)
    sources: list[dict] = Field(default_factory=list)
    status: InterviewKitStatus
    error_message: str | None = None
    generated_at: datetime | None = None
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class GenerateInterviewKitRequest(BaseModel):
    # Which JD variant to ground on. If null, defaults to the requisition's
    # selected_variant_index (the one that got published).
    jd_variant_index: int | None = None


class UpdateInterviewKitRequest(BaseModel):
    competencies: list[Competency] | None = None
    panels: list[InterviewPanel] | None = None
    scorecard: list[CompetencyRubric] | None = None
    reference_questions: list[ReferenceBank] | None = None
    role_title: str | None = None
    seniority_level: str | None = None
