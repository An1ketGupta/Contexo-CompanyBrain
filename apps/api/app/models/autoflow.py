"""Pydantic models for the Autoflow engine (Agent2 Day 1).

The autoflow engine has two halves: a *definition* (what should happen and
when — the rows in the ``autoflows`` table) and an *execution* (one fire of
that definition — rows in ``autoflow_runs``). This module owns the wire shape
of both, plus the validators that keep malformed configs out of the database.

Why validate here, not in SQL:
    JSONB lets us evolve the action schema without migrations, but it also
    lets garbage in. Pydantic at the HTTP boundary is enough — the dispatcher
    re-parses with these models before executing, so a database row that was
    written before a schema tightening fails *that run*, not the whole table.

Triggers vs actions:
    A trigger is "what fires this autoflow" (the WHEN). Actions are "what it
    does" (the THEN). Triggers map 1:1 to Inngest event names — the dispatcher
    in ``app/inngest/autoflow_functions.py`` receives ``autoflow/trigger.fired``
    with ``trigger_type`` in the payload, then fans out to matching autoflows.
    Action types are dispatched in-process via the registry in
    ``app/services/autoflow_actions.py``.

Action cap (32):
    The actions list is JSONB; in principle unbounded. In practice anything
    over ~10 steps is a workflow engine, not an automation, and we'd rather
    push that user toward composing multiple autoflows than carry a debugging
    nightmare in our codebase. 32 is the soft cap. Bump deliberately.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_ACTIONS_PER_AUTOFLOW = 32
MAX_NAME_LENGTH = 120
MAX_DESCRIPTION_LENGTH = 2000


# ── Enums ────────────────────────────────────────────────────────────────


class AutoflowTriggerType(str, Enum):
    """Maps 1:1 to the SQL CHECK constraint in migration 048.

    Adding a new trigger type? Three places to update:
      1. This enum
      2. The CHECK constraint in a new migration (don't edit 048)
      3. The Inngest event emit at the originating call site
    """

    DOCUMENT_UPLOADED = "document_uploaded"
    DOCUMENT_READY = "document_ready"
    DOCUMENT_FAILED = "document_failed"
    QUERY_NO_RESULTS = "query_no_results"
    MESSAGE_FEEDBACK_NEGATIVE = "message_feedback_negative"
    SCHEDULED = "scheduled"
    EMPLOYEE_JOINED = "employee_joined"
    KNOWLEDGE_GAP_DETECTED = "knowledge_gap_detected"
    APPROVAL_REQUESTED = "approval_requested"
    AGENT_COMPLETED = "agent_completed"
    COMPLIANCE_ACKNOWLEDGED = "compliance_acknowledged"


class AutoflowActionType(str, Enum):
    """Action kinds the dispatcher knows how to run.

    Handlers live in ``app/services/autoflow_actions.py`` keyed by this enum.
    Unknown types fail the run with a clear error rather than silently dropping.
    """

    GENERATE_OUTPUT = "generate_output"
    SEND_EMAIL = "send_email"
    POST_SLACK = "post_slack"
    CREATE_NOTION_PAGE = "create_notion_page"
    NOTIFY_ADMIN = "notify_admin"
    EMIT_WEBHOOK = "emit_webhook"
    # Stub — full implementation lands Day 4/6 alongside Asana/Linear adapters.
    # Registered so the eventual builder UI's schema is stable from Day 1.
    CREATE_TASK = "create_task"
    HOLD_FOR_APPROVAL = "hold_for_approval"


class AutoflowRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    HELD_FOR_APPROVAL = "held_for_approval"
    CANCELLED = "cancelled"


# ── Trigger config ───────────────────────────────────────────────────────


class AutoflowTriggerConfig(BaseModel):
    """Trigger-type-specific knobs.

    Kept loose (most fields optional) because each trigger type cares about a
    different subset. The dispatcher applies the relevant ones at fan-out time.
    """

    model_config = ConfigDict(extra="forbid")

    # Only used for trigger_type == 'scheduled'. Standard 5-field cron.
    cron: Optional[str] = None

    # Optional filters applied to the trigger payload before firing. Common
    # shapes: {collection_id, tags, document_id}. Unknown keys are ignored by
    # the matcher (defensive: a typo shouldn't silently break the flow).
    filters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("cron")
    @classmethod
    def _cron_shape(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        # Cheap sanity check — full parsing happens in the dispatcher with
        # croniter, which throws clearly on malformed cron.
        parts = v.split()
        if len(parts) != 5:
            raise ValueError("cron must have 5 space-separated fields (min hour day month dow)")
        return v


# ── Action shape ─────────────────────────────────────────────────────────


class AutoflowAction(BaseModel):
    """One step in the THEN sequence.

    ``order`` is explicit so a UI can reorder by mutating ``order`` rather
    than rewriting the array — keeps optimistic updates cheap. The executor
    sorts by ``order`` before running.
    """

    model_config = ConfigDict(extra="forbid")

    type: AutoflowActionType
    config: dict[str, Any] = Field(default_factory=dict)
    order: int = 0


# ── Definition (create/update wire model) ────────────────────────────────


class AutoflowBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=MAX_NAME_LENGTH)
    description: Optional[str] = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)
    trigger_type: AutoflowTriggerType
    trigger_config: AutoflowTriggerConfig = Field(default_factory=AutoflowTriggerConfig)
    actions: list[AutoflowAction] = Field(default_factory=list)
    confidence_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    is_active: bool = True

    @model_validator(mode="after")
    def _validate_shape(self) -> "AutoflowBase":
        if len(self.actions) > MAX_ACTIONS_PER_AUTOFLOW:
            raise ValueError(
                f"autoflow exceeds the {MAX_ACTIONS_PER_AUTOFLOW}-action cap; split into multiple flows"
            )

        # Scheduled autoflows must carry a cron. Anything else must NOT carry
        # a cron (else the cron dispatcher would mistakenly fire them).
        if self.trigger_type == AutoflowTriggerType.SCHEDULED:
            if not self.trigger_config.cron:
                raise ValueError("scheduled triggers require trigger_config.cron")
        else:
            if self.trigger_config.cron:
                raise ValueError(
                    f"trigger_config.cron only valid for trigger_type=scheduled (got {self.trigger_type.value})"
                )

        # hold_for_approval as the LAST action is meaningless — there's nothing
        # to gate. Catch the obvious misconfiguration early.
        if self.actions:
            last = sorted(self.actions, key=lambda a: a.order)[-1]
            if last.type == AutoflowActionType.HOLD_FOR_APPROVAL:
                raise ValueError(
                    "hold_for_approval cannot be the final action — it must precede the actions it gates"
                )

        return self


class AutoflowCreate(AutoflowBase):
    """Body for POST /admin/autoflows."""

    pass


class AutoflowUpdate(BaseModel):
    """Body for PATCH /admin/autoflows/{id}. All fields optional."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=MAX_NAME_LENGTH)
    description: Optional[str] = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)
    trigger_type: Optional[AutoflowTriggerType] = None
    trigger_config: Optional[AutoflowTriggerConfig] = None
    actions: Optional[list[AutoflowAction]] = None
    confidence_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    is_active: Optional[bool] = None


class AutoflowRead(AutoflowBase):
    """Response shape — what the API returns when reading an autoflow row."""

    id: UUID
    org_id: UUID
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    last_fired_at: Optional[datetime] = None


# ── Run (read-only timeline) ─────────────────────────────────────────────


class AutoflowRunStep(BaseModel):
    """One entry in autoflow_runs.steps."""

    model_config = ConfigDict(extra="allow")  # tolerant of new fields rolling forward

    index: int
    type: AutoflowActionType | str  # tolerate older runs with deprecated action types
    status: Literal["pending", "running", "completed", "failed", "skipped", "held_for_approval"]
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    output: Optional[dict[str, Any]] = None


class AutoflowRunRead(BaseModel):
    id: UUID
    autoflow_id: UUID
    org_id: UUID
    trigger_payload: dict[str, Any] = Field(default_factory=dict)
    status: AutoflowRunStatus
    steps: list[AutoflowRunStep] = Field(default_factory=list)
    steps_completed: int = 0
    total_steps: int = 0
    error_message: Optional[str] = None
    blocking_approval_id: Optional[UUID] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
