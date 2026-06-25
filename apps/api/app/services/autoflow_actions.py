"""Action handler registry for the autoflow engine (Agent2 Day 1).

Each handler runs *one* step of an autoflow_run. The dispatcher in
``autoflow_service.execute_autoflow_run`` walks the actions in order, calls
the matching handler, threads its return value into the run's context so
subsequent actions can reference it, and records per-step status to the
``autoflow_runs.steps`` JSONB column.

Handler contract:
    async def handler(ctx: ActionContext) -> ActionResult

    - Idempotent where the underlying API is idempotent. Where it isn't
      (Slack post, Gmail send), the worker emits a structured log line so an
      operator can correlate a duplicate side-effect to a specific run if a
      retry fires.
    - Raises ActionExecutionError on terminal failure. The dispatcher logs
      the error, marks the step failed, and fails the run.
    - Returns a dict that's stored as the step's ``output`` and made
      available to subsequent steps via the {{step_N.output.X}} templating
      in ``_render_config``.

Why a registry vs. a big if/elif:
    Keeps the dispatcher honest — adding a new action type means registering
    a handler here, period. The dispatcher doesn't grow. Tests can swap a
    handler for a fake by mutating the registry without monkeypatching
    arbitrary modules.

Templating ({{step_N.output.field}}):
    Deliberately minimal — string substitution against the run context, no
    expressions, no logic. If a flow needs branching or computation, that's
    a sign the user is reaching past what an automation should do, and they
    should hand-write a small backend hook instead.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.models.autoflow import AutoflowAction, AutoflowActionType
from app.observability import get_logger

log = get_logger(__name__)


# ── Errors ───────────────────────────────────────────────────────────────


class ActionExecutionError(RuntimeError):
    """Terminal failure of a single action — caller marks the step failed."""


class ActionUnavailable(ActionExecutionError):
    """The action type is registered but not implemented yet (e.g. create_task)."""


class ActionHeldForApproval(Exception):
    """Sentinel raised by the hold_for_approval handler to pause the run.

    Not an error — the dispatcher catches this distinctly, marks the run
    ``held_for_approval``, persists the approval row id on ``autoflow_runs``,
    and returns. A later ``autoflow/resume`` event resumes execution.
    """

    def __init__(self, approval_id: str, preview_text: str | None = None) -> None:
        super().__init__("held_for_approval")
        self.approval_id = approval_id
        self.preview_text = preview_text


# ── Context passed to every handler ──────────────────────────────────────


@dataclass
class ActionContext:
    """Everything a handler needs without reaching into globals.

    Keeping this explicit (not a thread-local or contextvar) makes the
    handlers trivially testable — pass a hand-rolled context, assert on the
    return value.
    """

    org_id: str
    autoflow_id: str
    run_id: str
    action_index: int
    action: AutoflowAction
    trigger_payload: dict[str, Any]
    # Outputs of previous steps in this run, keyed by step index as a string.
    # Looks like: {"0": {"text": "..."}, "1": {"message_id": "..."}}
    prior_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Confidence threshold from the autoflow row, if set.
    confidence_threshold: float | None = None


ActionResult = dict[str, Any]
Handler = Callable[[ActionContext], Awaitable[ActionResult]]


# ── Templating ───────────────────────────────────────────────────────────

# Match {{step_N.output.path.to.field}} or {{trigger.path.to.field}}.
# Deliberately strict — no nested braces, no expressions.
_TEMPLATE_RE = re.compile(r"\{\{\s*(?P<expr>[a-zA-Z0-9_.]+)\s*\}\}")


def _resolve_path(root: Any, path: list[str]) -> Any:
    """Walk dotted keys against nested dicts/lists. Returns None on miss."""
    cur = root
    for part in path:
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if cur is None:
            return None
    return cur


def render_config(
    config: dict[str, Any],
    *,
    prior_outputs: dict[str, dict[str, Any]],
    trigger_payload: dict[str, Any],
) -> dict[str, Any]:
    """Recursively substitute {{...}} references in string values.

    Lists and dicts walked depth-first. Non-string leaves passed through.
    A missing reference resolves to an empty string — the handler decides
    whether that's an error (most string-required fields raise).
    """

    def _sub(match: re.Match[str]) -> str:
        expr = match.group("expr")
        parts = expr.split(".")
        head = parts[0]
        # step_N.output.foo => walk prior_outputs[str(N)]['foo'] (since output is the dict itself)
        if head.startswith("step_") and len(parts) >= 2 and parts[1] == "output":
            try:
                idx = head.split("_", 1)[1]
            except IndexError:
                return ""
            output = prior_outputs.get(idx) or {}
            val = _resolve_path(output, parts[2:])
        elif head == "trigger":
            val = _resolve_path(trigger_payload, parts[1:])
        else:
            return ""
        if val is None:
            return ""
        if isinstance(val, (dict, list)):
            return json.dumps(val, separators=(",", ":"))
        return str(val)

    def _walk(v: Any) -> Any:
        if isinstance(v, str):
            return _TEMPLATE_RE.sub(_sub, v)
        if isinstance(v, list):
            return [_walk(x) for x in v]
        if isinstance(v, dict):
            return {k: _walk(x) for k, x in v.items()}
        return v

    return _walk(config)


# ── Handlers ─────────────────────────────────────────────────────────────


async def _handle_generate_output(ctx: ActionContext) -> ActionResult:
    """Run a knowledge-base-backed generation, return the text.

    config:
        prompt        : str (required) — user-style instruction
        scope_tags    : list[str] (optional) — restrict KB search to docs with these tags
        model         : str (optional) — passthrough; falls back to org default
        intent        : str (optional) — task_chain intent override

    output:
        text          : str — the generated content
        sources       : list[dict] — citation list (doc_name, chunk_id, page_number)
        confidence    : float | None — model confidence if available
    """
    # Lazy-import to keep the autoflow module light at import time — task_chain
    # pulls in the LLM SDK + retrieval stack which is a meaningful import cost.
    from app.services.llm.task_chain import run_autoflow_generation

    cfg = render_config(
        ctx.action.config,
        prior_outputs=ctx.prior_outputs,
        trigger_payload=ctx.trigger_payload,
    )
    prompt = (cfg.get("prompt") or "").strip()
    if not prompt:
        raise ActionExecutionError("generate_output requires a non-empty 'prompt'")

    result = await run_autoflow_generation(
        org_id=ctx.org_id,
        prompt=prompt,
        scope_tags=cfg.get("scope_tags") or None,
        intent_hint=cfg.get("intent"),
    )

    # Confidence gating — applied here rather than in the dispatcher so the
    # handler that actually has the score is the one that decides.
    if (
        ctx.confidence_threshold is not None
        and result.get("confidence") is not None
        and float(result["confidence"]) < ctx.confidence_threshold
    ):
        # Convert the bare result into a hold. The dispatcher catches the
        # sentinel, opens an approval row tied to this autoflow_run, and
        # stops the run cleanly.
        from app.services.autoflow_service import open_run_approval

        approval_id = await open_run_approval(
            org_id=ctx.org_id,
            autoflow_run_id=ctx.run_id,
            preview_text=result.get("text", "")[:1000],
            execution_action={
                "channel": "autoflow",
                "params": {
                    "autoflow_run_id": ctx.run_id,
                    "action_index": ctx.action_index,
                    "reason": "confidence_below_threshold",
                    "confidence": result.get("confidence"),
                    "threshold": ctx.confidence_threshold,
                },
            },
        )
        raise ActionHeldForApproval(approval_id=approval_id, preview_text=result.get("text"))

    return result


async def _handle_send_email(ctx: ActionContext) -> ActionResult:
    """Queue a transactional email via the existing Resend pipeline.

    config:
        to            : str (required)
        subject       : str (required)
        body          : str (required) — plain text; HTML wrapping is handled by the template
        dedupe_key    : str (optional)

    Uses the 'scheduled_usage_summary' event template as a neutral
    text-pass-through — keeps us out of the welcome / quota templates which
    have hardcoded copy. A future hardening pass should add a generic
    'autoflow_message' template; until then this is the right reuse.

    output:
        queued        : bool
    """
    from app.services.email.dispatcher import send_email_event

    cfg = render_config(
        ctx.action.config,
        prior_outputs=ctx.prior_outputs,
        trigger_payload=ctx.trigger_payload,
    )
    to = (cfg.get("to") or "").strip()
    subject = (cfg.get("subject") or "").strip()
    body = (cfg.get("body") or "").strip()
    if not to or not subject or not body:
        raise ActionExecutionError("send_email requires 'to', 'subject', and 'body'")

    await send_email_event(
        event_type="scheduled_usage_summary",
        to=to,
        user_id=None,
        org_id=ctx.org_id,
        dedupe_key=cfg.get("dedupe_key") or f"autoflow:{ctx.run_id}:{ctx.action_index}",
        data={
            "subject": subject,
            "body": body,
            "autoflow_run_id": ctx.run_id,
            "source": "autoflow",
        },
    )
    return {"queued": True, "to": to, "subject": subject}


async def _handle_post_slack(ctx: ActionContext) -> ActionResult:
    """Post a message to a Slack channel via the existing bot token.

    config:
        channel_id    : str (required)
        text          : str (required)
        thread_ts     : str (optional)

    output:
        ts            : str — Slack timestamp of the posted message
        channel       : str
    """
    from app.services.integrations.slack import post_message

    cfg = render_config(
        ctx.action.config,
        prior_outputs=ctx.prior_outputs,
        trigger_payload=ctx.trigger_payload,
    )
    channel_id = (cfg.get("channel_id") or "").strip()
    text = (cfg.get("text") or "").strip()
    if not channel_id or not text:
        raise ActionExecutionError("post_slack requires 'channel_id' and 'text'")

    try:
        result = await post_message(
            org_id=ctx.org_id,
            channel_id=channel_id,
            text=text,
            thread_ts=cfg.get("thread_ts") or None,
        )
    except PermissionError as exc:
        # PermissionError from the Slack adapter is terminal — the bot needs
        # human intervention (re-invite, reconnect). Surface it as a failure
        # without retry rather than burning Inngest budget.
        raise ActionExecutionError(f"slack_unavailable: {exc}") from exc
    return {"ts": result.get("ts"), "channel": result.get("channel")}


async def _handle_create_notion_page(ctx: ActionContext) -> ActionResult:
    """Create a Notion page under the configured parent.

    config:
        parent_page_id : str (required)
        title          : str (required)
        content        : str (required) — plain text; the Notion adapter chunks it into blocks

    output:
        page_id        : str
        url            : str
    """
    from app.services.integrations.notion import create_page

    cfg = render_config(
        ctx.action.config,
        prior_outputs=ctx.prior_outputs,
        trigger_payload=ctx.trigger_payload,
    )
    parent_page_id = (cfg.get("parent_page_id") or "").strip()
    title = (cfg.get("title") or "").strip()
    content = (cfg.get("content") or "").strip()
    if not parent_page_id or not title or not content:
        raise ActionExecutionError(
            "create_notion_page requires 'parent_page_id', 'title', and 'content'"
        )

    try:
        result = await create_page(
            org_id=ctx.org_id,
            parent_page_id=parent_page_id,
            title=title,
            content=content,
        )
    except PermissionError as exc:
        raise ActionExecutionError(f"notion_unavailable: {exc}") from exc
    return {"page_id": result.get("page_id"), "url": result.get("url")}


async def _handle_notify_admin(ctx: ActionContext) -> ActionResult:
    """Fan a notification out to every admin in the org.

    config:
        title         : str (required)
        body          : str (optional)
        link_url      : str (optional)
        dedupe_key    : str (optional) — defaults to a per-run unique key

    output:
        delivered_count : int
    """
    from app.database import get_service_client
    from app.services.notifications import create_notification

    cfg = render_config(
        ctx.action.config,
        prior_outputs=ctx.prior_outputs,
        trigger_payload=ctx.trigger_payload,
    )
    title = (cfg.get("title") or "").strip()
    if not title:
        raise ActionExecutionError("notify_admin requires 'title'")

    svc = get_service_client()

    def _admins() -> list[str]:
        res = (
            svc.table("users")
            .select("id")
            .eq("org_id", ctx.org_id)
            .eq("role", "admin")
            .execute()
        )
        return [row["id"] for row in (res.data or [])]

    import asyncio as _aio

    admins = await _aio.to_thread(_admins)
    if not admins:
        log.info("autoflow.notify_admin.no_admins", org_id=ctx.org_id, run_id=ctx.run_id)
        return {"delivered_count": 0}

    dedupe = cfg.get("dedupe_key") or f"autoflow:{ctx.run_id}:{ctx.action_index}"
    delivered = 0
    for admin_id in admins:
        row = await create_notification(
            org_id=ctx.org_id,
            user_id=admin_id,
            type="autoflow_admin_alert",
            title=title,
            body=cfg.get("body"),
            link_url=cfg.get("link_url"),
            dedupe_key=dedupe,
            metadata={"autoflow_run_id": ctx.run_id},
        )
        if row:
            delivered += 1
    return {"delivered_count": delivered}


async def _handle_emit_webhook(ctx: ActionContext) -> ActionResult:
    """Fire a webhook event from inside an autoflow.

    config:
        event         : str (required) — must be in webhooks.ALLOWED_EVENTS
        payload       : dict (optional) — passed straight through

    output:
        queued_count  : int — how many webhooks matched and were enqueued
    """
    from app.services.webhooks import trigger_event

    cfg = render_config(
        ctx.action.config,
        prior_outputs=ctx.prior_outputs,
        trigger_payload=ctx.trigger_payload,
    )
    event = (cfg.get("event") or "").strip()
    if not event:
        raise ActionExecutionError("emit_webhook requires 'event'")

    queued = await trigger_event(
        org_id=ctx.org_id,
        event=event,
        payload={
            **(cfg.get("payload") or {}),
            "autoflow_run_id": ctx.run_id,
            "source": "autoflow",
        },
    )
    return {"queued_count": queued, "event": event}


async def _handle_hold_for_approval(ctx: ActionContext) -> ActionResult:
    """Pause the run pending human sign-off.

    config:
        approver_user_id : str (optional) — defaults to any admin in the org
        preview_text     : str (optional) — short description shown to approver
        note             : str (optional)

    Effect:
        Raises ActionHeldForApproval; the dispatcher catches it and parks the
        run. Returns are unreachable (kept for type consistency).
    """
    from app.services.autoflow_service import open_run_approval

    cfg = render_config(
        ctx.action.config,
        prior_outputs=ctx.prior_outputs,
        trigger_payload=ctx.trigger_payload,
    )
    approval_id = await open_run_approval(
        org_id=ctx.org_id,
        autoflow_run_id=ctx.run_id,
        preview_text=cfg.get("preview_text"),
        note=cfg.get("note"),
        approver_user_id=cfg.get("approver_user_id"),
        execution_action={
            "channel": "autoflow",
            "params": {
                "autoflow_run_id": ctx.run_id,
                "action_index": ctx.action_index,
                "reason": "explicit_hold",
            },
        },
    )
    raise ActionHeldForApproval(approval_id=approval_id, preview_text=cfg.get("preview_text"))


async def _handle_create_task(ctx: ActionContext) -> ActionResult:
    """Create an external task in Linear / Jira / Asana / Notion.

    config:
        provider          : str (required) — 'linear' | 'jira' | 'asana' | 'notion'
        user_id           : str (required) — the workspace member whose OAuth
                            row to use. Most flows want the autoflow author
                            here; templating like {{trigger.user_id}} works.
        title             : str (required)
        description       : str (optional)
        assignee_email    : str (optional) — looked up on the provider side
        due_date          : str (optional) — YYYY-MM-DD
        notion_parent_id  : str (required for provider='notion')

    output:
        provider          : str
        task_id           : str
        identifier        : str | None — e.g. PROJ-123 for Jira/Linear
        url               : str | None
    """
    from app.services.action_tracker import _create_one

    cfg = render_config(
        ctx.action.config,
        prior_outputs=ctx.prior_outputs,
        trigger_payload=ctx.trigger_payload,
    )
    provider = (cfg.get("provider") or "").strip().lower()
    if provider not in ("linear", "jira", "asana", "notion"):
        raise ActionExecutionError(
            "create_task requires provider in {linear,jira,asana,notion}"
        )
    user_id = (cfg.get("user_id") or "").strip()
    if not user_id:
        raise ActionExecutionError(
            "create_task requires 'user_id' (the workspace member whose "
            "integration credentials to use)"
        )
    title = (cfg.get("title") or "").strip()
    if not title:
        raise ActionExecutionError("create_task requires non-empty 'title'")

    try:
        result = await _create_one(
            target=provider,  # type: ignore[arg-type]
            org_id=ctx.org_id,
            user_id=user_id,
            action_text=title,
            notes=(cfg.get("description") or "").strip(),
            owner_email=(cfg.get("assignee_email") or None),
            due_date=(cfg.get("due_date") or None),
            notion_parent_page_id=(cfg.get("notion_parent_id") or None),
        )
    except PermissionError as exc:
        # Missing/expired OAuth — terminal; the author needs to reconnect.
        raise ActionExecutionError(f"{provider}_unavailable: {exc}") from exc
    return {
        "provider": provider,
        "task_id": result.get("task_id"),
        "identifier": result.get("identifier"),
        "url": result.get("url"),
    }


# ── Registry ─────────────────────────────────────────────────────────────


HANDLERS: dict[AutoflowActionType, Handler] = {
    AutoflowActionType.GENERATE_OUTPUT: _handle_generate_output,
    AutoflowActionType.SEND_EMAIL: _handle_send_email,
    AutoflowActionType.POST_SLACK: _handle_post_slack,
    AutoflowActionType.CREATE_NOTION_PAGE: _handle_create_notion_page,
    AutoflowActionType.NOTIFY_ADMIN: _handle_notify_admin,
    AutoflowActionType.EMIT_WEBHOOK: _handle_emit_webhook,
    AutoflowActionType.HOLD_FOR_APPROVAL: _handle_hold_for_approval,
    AutoflowActionType.CREATE_TASK: _handle_create_task,
}


def get_handler(action_type: AutoflowActionType) -> Handler:
    handler = HANDLERS.get(action_type)
    if handler is None:
        raise ActionExecutionError(f"no handler registered for action type {action_type.value!r}")
    return handler
