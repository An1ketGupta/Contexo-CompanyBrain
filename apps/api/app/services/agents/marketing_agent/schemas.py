"""Pydantic schemas for MarketingAgent payloads.

Used both for LLM output validation (lenient) and API responses (strict).
The agent's parsed JSON gets `.model_validate()`d before persistence so a
malformed LLM response can't poison the DB.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Channel = Literal["blog", "linkedin", "x", "email", "landing", "ads"]
MarketingBriefStatus = Literal[
    "draft", "generating", "ready", "published", "failed"
]


# ── Artifact 1: positioning ────────────────────────────────────────────────


class ValueProp(BaseModel):
    name: str
    statement: str


class Positioning(BaseModel):
    audience: str = ""
    problem: str = ""
    category: str = ""
    differentiation: str = ""
    value_props: list[ValueProp] = Field(default_factory=list)
    taglines: list[str] = Field(default_factory=list)


# ── Artifact 2: messaging pillars ──────────────────────────────────────────


class MessagingPillar(BaseModel):
    name: str
    statement: str
    proof_points: list[str] = Field(default_factory=list)
    weight: float = Field(0.2, ge=0.0, le=1.0)


# ── Artifact 3: competitive angle ──────────────────────────────────────────


class CompetitiveAngle(BaseModel):
    competitor: str
    their_pitch: str = ""
    our_counter: str = ""
    win_themes: list[str] = Field(default_factory=list)
    gotchas: list[str] = Field(default_factory=list)


# ── Artifact 4: channel plan ───────────────────────────────────────────────


class ChannelDraft(BaseModel):
    # Optional fields because not every channel needs a title (X thread doesn't)
    # or a hook (long-form blog leads with the H1).
    title: str = ""
    body: str
    hook: str = ""
    length_hint: str = ""


class ChannelPlanEntry(BaseModel):
    channel: Channel
    lens: str = ""
    cta: str = ""
    timing: str = ""
    drafts: list[ChannelDraft] = Field(default_factory=list)


# ── Artifact 5: content brief ──────────────────────────────────────────────


class OutlineSection(BaseModel):
    heading: str
    key_points: list[str] = Field(default_factory=list)


class ContentBrief(BaseModel):
    working_title: str = ""
    target_length_words: int = 0
    target_keywords: list[str] = Field(default_factory=list)
    outline: list[OutlineSection] = Field(default_factory=list)
    internal_link_ideas: list[str] = Field(default_factory=list)
    distribution_notes: str = ""


# ── DB row shape ───────────────────────────────────────────────────────────


class MarketingBriefRead(BaseModel):
    id: str
    org_id: str
    created_by: str
    run_id: str | None = None
    objective: str
    audience_hint: str | None = None
    channels: list[Channel] = Field(default_factory=list)
    competitors: list[str] = Field(default_factory=list)
    collection_id: str | None = None
    positioning: Positioning = Field(default_factory=Positioning)
    messaging_pillars: list[MessagingPillar] = Field(default_factory=list)
    competitive_angle: list[CompetitiveAngle] = Field(default_factory=list)
    channel_plan: list[ChannelPlanEntry] = Field(default_factory=list)
    content_brief: ContentBrief = Field(default_factory=ContentBrief)
    sources: list[dict] = Field(default_factory=list)
    status: MarketingBriefStatus
    error_message: str | None = None
    generated_at: datetime | None = None
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


# ── API request bodies ─────────────────────────────────────────────────────


class GenerateMarketingBriefRequest(BaseModel):
    objective: str = Field(..., min_length=4, max_length=1000)
    audience_hint: str | None = Field(None, max_length=500)
    channels: list[Channel] = Field(
        default_factory=lambda: ["blog", "linkedin", "email"]
    )
    competitors: list[str] = Field(default_factory=list, max_length=10)
    collection_id: str | None = None


class UpdateMarketingBriefRequest(BaseModel):
    objective: str | None = None
    audience_hint: str | None = None
    positioning: Positioning | None = None
    messaging_pillars: list[MessagingPillar] | None = None
    competitive_angle: list[CompetitiveAngle] | None = None
    channel_plan: list[ChannelPlanEntry] | None = None
    content_brief: ContentBrief | None = None
