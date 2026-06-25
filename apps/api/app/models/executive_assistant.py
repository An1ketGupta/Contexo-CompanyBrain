"""Pydantic models for the Executive Assistant agent (#25, Agent2 Day 6)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class GenerateBriefingRequest(BaseModel):
    request_text: str = Field(..., min_length=10, max_length=4000)
    recipients: list[EmailStr] = Field(default_factory=list, max_length=20)
    schedule_followup: bool = False
    followup_proposed_start: datetime | None = None
    followup_proposed_end: datetime | None = None
    followup_title: str | None = None


class BriefingSections(BaseModel):
    executive_summary: str = ""
    market_context: str = ""
    competitive_advantages: str = ""
    risks: str = ""
    recommendations: str = ""


class BriefingRead(BaseModel):
    id: str
    org_id: str
    created_by: str
    request_text: str
    recipients: list[str]
    sections: dict
    google_doc_id: str | None
    google_doc_url: str | None
    followup_event_id: str | None
    followup_event_url: str | None
    status: Literal["draft", "doc_created", "sent", "failed"]
    error_message: str | None
    created_at: datetime
    sent_at: datetime | None
