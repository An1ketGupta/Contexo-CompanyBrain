"""Pydantic models for Sales Enablement (#22, Agent2 Day 5)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ── Pre-Call Brief ──────────────────────────────────────────────────────────


class PrecallBriefRequest(BaseModel):
    prospect_name: str = Field(..., min_length=1, max_length=200)
    company: str = Field(..., min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=4000)


class PrecallSection(BaseModel):
    heading: str
    bullets: list[str]


class PrecallBriefResponse(BaseModel):
    prospect_name: str
    company: str
    talking_points: list[str]
    objections: list[dict]                # [{objection, response}]
    case_studies: list[dict]              # [{title, takeaway}]
    pricing_scenario: list[str]
    sources: list[dict]
    generated_at: datetime


# ── RFP ─────────────────────────────────────────────────────────────────────


class RfpRequirement(BaseModel):
    id: str
    requirement_text: str
    category: str | None = None


class RfpMatch(BaseModel):
    requirement_id: str
    status: Literal["matched", "gap"]
    source_doc: str | None = None
    response_text: str | None = None
    flag_message: str | None = None
    confidence: float | None = None


class RfpResponseRead(BaseModel):
    id: str
    org_id: str
    created_by: str
    source_filename: str
    requirements: list[RfpRequirement]
    matches: list[RfpMatch]
    status: Literal["extracted", "reviewed", "generating", "ready", "failed"]
    output_file_path: str | None
    gap_count: int
    error_message: str | None
    created_at: datetime
    generated_at: datetime | None


class UpdateRequirementsRequest(BaseModel):
    """Used after extraction to let the rep edit the parsed requirements
    BEFORE generating the .docx."""

    requirements: list[RfpRequirement]
