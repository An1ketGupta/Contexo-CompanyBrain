"""Public-API agent registry (Agent Roadmap Day 14).

Single source of truth for the four agents exposed via
    POST /v1/agents/{agent_type}/run

Each registry entry binds:
    * agent_type             — URL slug + audit row tag
    * required / optional    — input schema validated at the API boundary
    * estimated_seconds      — surfaced in the trigger response so clients
                                know roughly when to start polling
    * description / example  — surfaced by GET /v1/agents (self-describing)
    * dispatch               — async callable that fires the right Inngest
                                event with a stable id (idempotency)

We keep the registry data-only (no agent-class imports here) so a future
agent change doesn't ripple back into the public API surface — and so the
list endpoint doesn't import every agent transitively.

Idempotency model: every dispatch takes a `run_id` (uuid) which becomes the
agent_runs.id. The Inngest event id is keyed off this run_id so an external
caller retrying with the same Idempotency-Key gets exactly one agent run.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import inngest

from app.database import get_service_client
from app.inngest.client import get_inngest_client

log = logging.getLogger(__name__)


# ── Registry entry types ────────────────────────────────────────────────────


DispatchFn = Callable[
    ["AgentDispatchContext"],
    Awaitable[None],
]


@dataclass(frozen=True)
class AgentSpec:
    agent_type: str
    description: str
    required: tuple[str, ...]
    optional: tuple[str, ...]
    estimated_seconds: int
    dispatch: DispatchFn
    example_input: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentDispatchContext:
    run_id: str
    org_id: str
    input: dict[str, Any]
    output_channels: list[str]
    webhook_url: str | None
    api_key_id: str | None
    approval_id: str | None
    triggered_by: str  # 'api' | 'approval'


# ── Dispatchers ─────────────────────────────────────────────────────────────


async def _dispatch_onboarding(ctx: AgentDispatchContext) -> None:
    client = get_inngest_client()
    await client.send(
        inngest.Event(
            name="agent/onboarding/triggered-api",
            data={
                "run_id": ctx.run_id,
                "org_id": ctx.org_id,
                "input": ctx.input,
                "output_channels": ctx.output_channels,
                "webhook_url": ctx.webhook_url,
                "api_key_id": ctx.api_key_id,
                "approval_id": ctx.approval_id,
                "triggered_by": ctx.triggered_by,
            },
            id=f"agent-onboarding-{ctx.run_id}",
        )
    )


async def _dispatch_policy_propagation(ctx: AgentDispatchContext) -> None:
    client = get_inngest_client()
    # The existing PolicyPropagationAgent already handles the policy doc
    # path end-to-end. We re-fire its native event so the audit + concurrency
    # caps match what the doc-pipeline trigger uses.
    await client.send(
        inngest.Event(
            name="agent/policy-propagate",
            data={
                "document_id": ctx.input["document_id"],
                "org_id": ctx.org_id,
                "version_id": ctx.input.get("version_id"),
                # Side-channel context so the agent can fire the API
                # callback when it completes:
                "_api_context": {
                    "run_id": ctx.run_id,
                    "webhook_url": ctx.webhook_url,
                    "api_key_id": ctx.api_key_id,
                    "approval_id": ctx.approval_id,
                },
            },
            id=f"agent-policy-{ctx.run_id}",
        )
    )


async def _dispatch_support_response(ctx: AgentDispatchContext) -> None:
    client = get_inngest_client()
    await client.send(
        inngest.Event(
            name="support/email-received",
            data={
                "org_id": ctx.org_id,
                "from_email": ctx.input["from_email"],
                "from_name": ctx.input.get("from_name"),
                "subject": ctx.input.get("subject") or "",
                "body": ctx.input.get("body") or "",
                "category": ctx.input.get("category") or "support",
                "_api_context": {
                    "run_id": ctx.run_id,
                    "webhook_url": ctx.webhook_url,
                    "api_key_id": ctx.api_key_id,
                    "approval_id": ctx.approval_id,
                },
            },
            id=f"agent-support-{ctx.run_id}",
        )
    )


async def _dispatch_weekly_digest(ctx: AgentDispatchContext) -> None:
    client = get_inngest_client()
    await client.send(
        inngest.Event(
            name="agent/weekly-digest/triggered-api",
            data={
                "run_id": ctx.run_id,
                "org_id": ctx.org_id,
                "send_to_email": ctx.input.get("send_to_email"),
                "webhook_url": ctx.webhook_url,
                "api_key_id": ctx.api_key_id,
                "approval_id": ctx.approval_id,
                "triggered_by": ctx.triggered_by,
            },
            id=f"agent-weekly-digest-{ctx.run_id}",
        )
    )


# ── Registry ────────────────────────────────────────────────────────────────


AGENT_REGISTRY: dict[str, AgentSpec] = {
    "onboarding": AgentSpec(
        agent_type="onboarding",
        description=(
            "Generate a personalised 90-day onboarding plan grounded in the "
            "company knowledge base. Optionally creates a Notion page, emails "
            "the hire, and DMs their manager on Slack."
        ),
        required=("name", "email", "role", "start_date"),
        optional=("manager_email", "department", "user_id"),
        estimated_seconds=45,
        dispatch=_dispatch_onboarding,
        example_input={
            "name": "Sarah Chen",
            "email": "sarah@acme.com",
            "role": "Senior Product Designer",
            "start_date": "2026-07-01",
            "manager_email": "john@acme.com",
        },
    ),
    "policy_propagation": AgentSpec(
        agent_type="policy_propagation",
        description=(
            "Re-run the policy propagation pipeline for a document: diff vs. "
            "previous version, post to the Slack policy channel, and create "
            "acknowledgement rows for every active org member. Pass either "
            "`document_id` (UUID) or `document_name` — the API will look up "
            "the id by exact-match name within the calling org."
        ),
        required=(),
        optional=("document_id", "document_name", "version_id"),
        estimated_seconds=30,
        dispatch=_dispatch_policy_propagation,
        example_input={"document_name": "Refund Policy"},
    ),
    "support_response": AgentSpec(
        agent_type="support_response",
        description=(
            "Generate a draft customer-support reply for an inbound email. "
            "Returns a ticket id; the draft lands in the support queue for an "
            "admin to review and send."
        ),
        required=("from_email", "subject", "body"),
        optional=("from_name", "category"),
        estimated_seconds=20,
        dispatch=_dispatch_support_response,
        example_input={
            "from_email": "customer@example.com",
            "subject": "Refund request",
            "body": "Hi — I'd like to request a refund for order #12345…",
        },
    ),
    "weekly_digest": AgentSpec(
        agent_type="weekly_digest",
        description=(
            "Generate the weekly admin digest (usage stats, top queries, "
            "coverage gaps) and email it. By default sends to all admins; "
            "specify `send_to_email` to override."
        ),
        required=(),
        optional=("send_to_email",),
        estimated_seconds=15,
        dispatch=_dispatch_weekly_digest,
        example_input={"send_to_email": "admin@acme.com"},
    ),
}


# ── Input validation ────────────────────────────────────────────────────────


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


class AgentInputError(ValueError):
    """Raised when the API trigger payload fails the boundary check."""

    pass


def validate_agent_input(agent_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Return a normalised, allowlisted dict for the agent_type.

    Strips unknown keys (defensive — the dispatcher would ignore them anyway,
    but a smaller payload keeps the audit JSONB readable). Coerces email
    fields to lowercase strings. Doesn't try to deep-validate values; the
    underlying agent is responsible for application-layer checks.
    """
    spec = AGENT_REGISTRY.get(agent_type)
    if not spec:
        raise AgentInputError(
            f"Unknown agent type '{agent_type}'. Valid types: "
            f"{', '.join(sorted(AGENT_REGISTRY.keys()))}"
        )

    if not isinstance(payload, dict):
        raise AgentInputError("input must be an object")

    missing = [k for k in spec.required if not payload.get(k)]
    if missing:
        raise AgentInputError(
            f"Missing required input field(s) for '{agent_type}': "
            f"{', '.join(missing)}"
        )

    # policy_propagation accepts EITHER document_id OR document_name. Enforce
    # at-least-one at validation time so the API returns 400 immediately
    # instead of hitting the document_id lookup with an empty value.
    if agent_type == "policy_propagation":
        if not payload.get("document_id") and not payload.get("document_name"):
            raise AgentInputError(
                "policy_propagation requires either `document_id` (UUID) "
                "or `document_name` (exact match within your org)."
            )

    allowed = set(spec.required) | set(spec.optional)
    cleaned: dict[str, Any] = {}
    for key in allowed:
        if key in payload and payload[key] is not None:
            cleaned[key] = payload[key]

    # Lightweight per-agent coercion. Anything heavier belongs in the agent.
    if agent_type == "onboarding":
        for email_field in ("email", "manager_email"):
            v = cleaned.get(email_field)
            if isinstance(v, str):
                cleaned[email_field] = v.strip().lower()
                if email_field == "email" and not _EMAIL_RE.match(cleaned[email_field]):
                    raise AgentInputError("input.email is not a valid email")
        sd = cleaned.get("start_date")
        if isinstance(sd, str) and not _ISO_DATE_RE.match(sd):
            raise AgentInputError("input.start_date must be ISO-8601 (YYYY-MM-DD)")
    elif agent_type == "support_response":
        v = cleaned.get("from_email")
        if isinstance(v, str):
            cleaned["from_email"] = v.strip().lower()
            if not _EMAIL_RE.match(cleaned["from_email"]):
                raise AgentInputError("input.from_email is not a valid email")
    elif agent_type == "weekly_digest":
        v = cleaned.get("send_to_email")
        if isinstance(v, str):
            cleaned["send_to_email"] = v.strip().lower()
            if not _EMAIL_RE.match(cleaned["send_to_email"]):
                raise AgentInputError("input.send_to_email is not a valid email")

    return cleaned


# ── Document name → id resolution (policy_propagation convenience) ────────


async def resolve_document_id_by_name(
    *, org_id: str, document_name: str
) -> str | None:
    """Look up a document id by exact-match name within an org.

    Used by the public API to translate the customer-friendly
    `document_name` field on policy_propagation triggers into the
    canonical UUID. Returns None when no match exists; the caller turns
    that into a 400. Ambiguous names (multiple docs share the name) also
    return None — callers should fall back to the UUID path.
    """
    name = (document_name or "").strip()
    if not name:
        return None
    svc = get_service_client()
    rows = await asyncio.to_thread(
        lambda: svc.table("documents")
        .select("id")
        .eq("org_id", org_id)
        .eq("name", name)
        .limit(2)
        .execute()
    )
    data = rows.data or []
    if len(data) != 1:
        return None
    return data[0]["id"]


# ── Approver resolution ─────────────────────────────────────────────────────


async def resolve_approver_user_id(*, org_id: str, approver_email: str) -> str | None:
    """Map an approver email to a workspace user id in the same org.

    Returns None when no member is found — callers should 400 with a clear
    message. We do NOT auto-create users; security and audit trails are
    cleaner when approvals require an existing workspace identity.
    """
    svc = get_service_client()
    email_l = (approver_email or "").strip().lower()
    if not email_l or not _EMAIL_RE.match(email_l):
        return None

    # Two-step resolve: auth.users carries the canonical email, then we
    # confirm the user belongs to this org via public.users.
    try:
        page = await asyncio.to_thread(
            lambda: svc.auth.admin.list_users(page=1, per_page=200)
        )
    except Exception as exc:
        log.warning("approver_email_lookup_failed_auth: %s", exc)
        return None

    users = getattr(page, "users", None) or page or []
    candidate_ids: list[str] = []
    for au in users:
        email = (getattr(au, "email", "") or "").lower()
        if email == email_l:
            candidate_ids.append(getattr(au, "id", None))
    candidate_ids = [u for u in candidate_ids if u]
    if not candidate_ids:
        return None

    rows = await asyncio.to_thread(
        lambda: svc.table("users")
        .select("id, org_id")
        .in_("id", candidate_ids)
        .eq("org_id", org_id)
        .limit(1)
        .execute()
    )
    data = rows.data or []
    return data[0]["id"] if data else None


# ── Dispatch entrypoints ────────────────────────────────────────────────────


async def dispatch_api_agent(
    *,
    org_id: str,
    agent_type: str,
    agent_input: dict[str, Any],
    output_channels: list[str] | None = None,
    webhook_url: str | None = None,
    api_key_id: str | None = None,
    approval_id: str | None = None,
    run_id: str,
) -> None:
    """Fire the right Inngest event for `agent_type`. Idempotent on run_id."""
    spec = AGENT_REGISTRY.get(agent_type)
    if not spec:
        raise AgentInputError(f"Unknown agent type '{agent_type}'")

    ctx = AgentDispatchContext(
        run_id=run_id,
        org_id=org_id,
        input=agent_input,
        output_channels=list(output_channels or []),
        webhook_url=webhook_url,
        api_key_id=api_key_id,
        approval_id=approval_id,
        triggered_by="approval" if approval_id else "api",
    )
    await spec.dispatch(ctx)


async def precreate_agent_run(
    *,
    run_id: str,
    org_id: str,
    agent_type: str,
    triggered_by: str,
    triggered_by_user_id: str | None,
    input_data: dict[str, Any],
) -> None:
    """Insert the agent_runs row up-front so the API can return a pollable
    id immediately. The agent's BaseAgent.create_run_record() is idempotent
    on duplicate key — when it runs inside the worker it will fall back to
    appending steps onto this pre-created row."""
    svc = get_service_client()
    row = {
        "id": run_id,
        "org_id": org_id,
        "agent_type": agent_type,
        "triggered_by": triggered_by,
        "triggered_by_user_id": triggered_by_user_id,
        "status": "running",
        "input": input_data,
        "started_at": datetime.now(UTC).isoformat(),
    }
    try:
        await asyncio.to_thread(
            lambda: svc.table("agent_runs").insert(row).execute()
        )
    except Exception as exc:
        # Duplicate run_id (caller retried with same Idempotency-Key) — fine,
        # we already have the row. Don't bubble up.
        if "duplicate key" not in str(exc).lower():
            raise


def agent_registry_public_view() -> list[dict[str, Any]]:
    """Self-describing list for GET /v1/agents."""
    out: list[dict[str, Any]] = []
    for spec in AGENT_REGISTRY.values():
        out.append(
            {
                "agent_type": spec.agent_type,
                "description": spec.description,
                "input_schema": {
                    "required": list(spec.required),
                    "optional": list(spec.optional),
                },
                "estimated_seconds": spec.estimated_seconds,
                "example_input": spec.example_input,
            }
        )
    return out


# ── Idempotency-Key handling ────────────────────────────────────────────────


def normalize_idempotency_key(raw: str | None) -> str | None:
    """Strip + length-cap an Idempotency-Key header value.

    Returns None for empty values. Callers should treat the returned string
    as opaque — we hash it into a deterministic run_id elsewhere.
    """
    if not raw:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    if len(cleaned) > 128:
        cleaned = cleaned[:128]
    return cleaned


def run_id_from_idempotency_key(
    *, api_key_id: str, agent_type: str, idem_key: str
) -> str:
    """Deterministic UUIDv5 from (api_key, agent_type, idem_key).

    Same caller + same key + same agent → identical run_id → identical
    agent_runs insert → same Inngest event id → idempotent dispatch.
    """
    import uuid as _uuid

    name = f"{api_key_id}:{agent_type}:{idem_key}"
    return str(_uuid.uuid5(_uuid.NAMESPACE_URL, name))


__all__ = [
    "AgentSpec",
    "AgentDispatchContext",
    "AgentInputError",
    "AGENT_REGISTRY",
    "validate_agent_input",
    "resolve_approver_user_id",
    "resolve_document_id_by_name",
    "dispatch_api_agent",
    "precreate_agent_run",
    "agent_registry_public_view",
    "normalize_idempotency_key",
    "run_id_from_idempotency_key",
]
