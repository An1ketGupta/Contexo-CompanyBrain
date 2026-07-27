"""GDPR data export — personal + admin org-wide ZIP builders.

Two endpoints share this module:

  * ``/users/me/export``          → personal data the user has authored.
                                    User-scoped Supabase client; RLS is the
                                    security boundary, app filters are
                                    defence-in-depth.
  * ``/organizations/me/export``  → admin-only, full workspace dump.
                                    Service-role client with explicit
                                    ``eq("org_id", ...)`` on every query
                                    since cross-user data is required.

What we DO NOT include, ever:

  * OAuth access/refresh tokens (any integration). We list provider +
    connected email + connection timestamp; never the token itself.
  * API key material (we don't even store it in clear — only a hash —
    but we skip the row entirely for clarity).
  * Embedding vectors. They're derived data the user can't act on, and
    they bloat the archive enormously without informational value.
  * Document binary content. Listed by name + size only; the spec was
    explicit on this and we have neither the latency budget for sync
    file zips nor the storage budget to materialise them per request.
    Full-file export is a backlog item (Inngest job + email link).

Rate limit: per-caller daily fixed window via Upstash. Three exports
in 24h is plenty for legitimate "I want my data" use and stops the
endpoint from being a free DoS vector against the DB. Falls open if
Upstash is unreachable (matches the rest of our rate-limit policy).
"""
from __future__ import annotations

import asyncio
import io
import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Callable

import httpx

from app.config import get_settings
from app.database import get_service_client, get_user_client
from app.observability import get_logger

log = get_logger(__name__)


# Truncation caps for the org-wide export. Past these counts we still
# produce a valid archive but include a ``truncated: true`` marker so
# the admin knows to contact support for the full history. These are
# chosen so the largest legitimate v1 export stays under ~50MB of JSON.
ORG_MESSAGE_CAP = 50_000
ORG_QUERY_LOG_CAP = 100_000
ORG_DOCUMENT_CAP = 10_000

# Personal caps — much smaller since they're one user's footprint.
USER_MESSAGE_CAP = 20_000
USER_QUERY_LOG_CAP = 50_000

# We only ever surface these integration columns. ``access_token`` /
# ``refresh_token`` / ``webhook_secret`` etc. are explicitly excluded
# from the projection so a future schema change can't accidentally
# leak them.
_INTEGRATION_SAFE_COLUMNS = (
    "provider, scope_user_id, scopes, metadata, "
    "last_synced_at, created_at, updated_at"
)


# ── Rate limit ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExportQuotaResult:
    allowed: bool
    used: int
    limit: int
    seconds_until_reset: int


async def check_export_quota(
    *, namespace: str, identifier: str, limit: int = 3
) -> ExportQuotaResult:
    """Per-identifier daily counter. Increments only on ``allowed=True`` calls.

    ``namespace`` distinguishes personal vs org export so an admin who has
    already pulled the workspace dump today can still grab their own
    personal export without burning the same budget.
    """
    settings = get_settings()
    if not settings.upstash_redis_rest_url or not settings.upstash_redis_rest_token:
        # Fail-open: matches the rest of our rate-limit policy. A DB query
        # is cheap relative to actually generating an export, and we'd
        # rather not 503 a legitimate caller because Upstash blipped.
        return ExportQuotaResult(allowed=True, used=0, limit=limit, seconds_until_reset=0)

    today = date.today().isoformat()  # noqa: DTZ011 — bucket key, not a clock-sensitive value
    key = f"export:{namespace}:{identifier}:{today}"

    # We need INCR's return value, but only set the day-long TTL on the
    # first hit so a long-running attack can't keep extending the window.
    try:
        async with httpx.AsyncClient(
            base_url=settings.upstash_redis_rest_url.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.upstash_redis_rest_token}"},
            timeout=httpx.Timeout(3.0, connect=2.0),
        ) as client:
            resp = await client.post(
                "/pipeline",
                json=[
                    ["INCR", key],
                    ["EXPIRE", key, "86400", "NX"],
                ],
            )
            resp.raise_for_status()
            results = resp.json()
            used = int(results[0].get("result", 0))
    except Exception as exc:
        log.warning("export_quota_upstash_unavailable", error=str(exc))
        return ExportQuotaResult(allowed=True, used=0, limit=limit, seconds_until_reset=0)

    # Seconds until UTC midnight — close enough; a Redis EXPIRE TTL of 86400
    # set at a non-midnight time would naturally elapse before the bucket
    # turns over, so the user might see "available" up to a day earlier
    # than the displayed countdown. That's fine UX-wise.
    now = datetime.now(UTC)
    seconds_until_reset = (
        (24 - now.hour) * 3600 - now.minute * 60 - now.second
    )

    if used > limit:
        # Roll back so the next caller's display matches reality.
        try:
            async with httpx.AsyncClient(
                base_url=settings.upstash_redis_rest_url.rstrip("/"),
                headers={"Authorization": f"Bearer {settings.upstash_redis_rest_token}"},
                timeout=httpx.Timeout(3.0, connect=2.0),
            ) as client:
                await client.post("/", json=["DECR", key])
        except Exception:
            pass
        return ExportQuotaResult(
            allowed=False, used=limit, limit=limit, seconds_until_reset=seconds_until_reset
        )

    return ExportQuotaResult(
        allowed=True, used=used, limit=limit, seconds_until_reset=seconds_until_reset
    )


# ── Helpers ──────────────────────────────────────────────────────────────────


def _json_dump(payload: Any) -> bytes:
    """Stable JSON encoding for archive contents. ``default=str`` lets us
    pass through Postgres timestamps and UUIDs without bespoke handlers."""
    return json.dumps(payload, default=str, indent=2, ensure_ascii=False).encode("utf-8")


async def _safe_select(
    *, query_factory: Callable[[], Any], label: str
) -> list[dict[str, Any]]:
    """Run a Supabase select on a thread, downgrade DB failures to an empty
    list so a single missing table doesn't blow up the whole archive.

    The export is best-effort by design: a user who's hit "Download my data"
    has consented to receiving whatever can be assembled, and a half-empty
    archive is more useful than a 500.
    """
    try:
        result = await asyncio.to_thread(lambda: query_factory().execute())
        return list(result.data or [])
    except Exception as exc:
        log.warning("export_section_failed", section=label, error=str(exc))
        return []


def _readme(*, scope: str, generated_at: str, notes: list[str]) -> bytes:
    body = [
        "Contexo — Data Export",
        "=======================",
        "",
        f"Scope:        {scope}",
        f"Generated at: {generated_at}",
        "",
        "Contents",
        "--------",
        "Each .json file in this archive holds a category of data we hold",
        "about you (or your workspace). Timestamps are ISO 8601 UTC.",
        "",
        "Excluded by design",
        "------------------",
        "  * OAuth access/refresh tokens for connected integrations.",
        "    We list the provider, the connected account, and timestamps",
        "    only — never any credential material.",
        "  * Programmatic API keys: we only store an irreversible hash,",
        "    not the key itself, so there is nothing to return.",
        "  * Embedding vectors derived from your documents (derived data).",
        "  * Document binary content. Document metadata (name, size, type,",
        "    upload date) is included; the underlying files are not, since",
        "    a synchronous endpoint can't safely zip arbitrary blobs.",
        "    To request a copy of an uploaded document, email",
        "    support@nirnayaiq.com with the document_id.",
        "",
        "Your rights",
        "-----------",
        "This archive is provided under GDPR Article 15 (Right of Access)",
        "and Article 20 (Right to Data Portability), and analogous rights",
        "under CCPA / Indian DPDP Act 2023. For deletion requests, use the",
        "in-app account deletion flow in Settings → Danger zone, or email",
        "privacy@nirnayaiq.com.",
        "",
    ]
    if notes:
        body.append("Notes specific to this export")
        body.append("-----------------------------")
        body.extend(f"  * {n}" for n in notes)
        body.append("")
    return "\n".join(body).encode("utf-8")


# ── Personal export ──────────────────────────────────────────────────────────


async def build_user_export(
    *, user_id: str, org_id: str, token: str, email: str | None
) -> bytes:
    """Assemble a ZIP of the caller's personal data.

    Uses the user-scoped Supabase client so RLS narrows every query to
    rows the caller has SELECT permission on — the explicit ``.eq("user_id"...)``
    filters below are a second layer rather than the only layer.
    """
    client = get_user_client(token)
    now = datetime.now(UTC).isoformat()
    notes: list[str] = []

    profile = await _safe_select(
        query_factory=lambda: (
            client.table("users")
            .select(
                "id, org_id, role, display_name, role_title, "
                "activity_private, competitor_names, created_at"
            )
            .eq("id", user_id)
        ),
        label="profile",
    )

    conversations = await _safe_select(
        query_factory=lambda: (
            client.table("conversations").select("*").eq("user_id", user_id)
        ),
        label="conversations",
    )
    conversation_ids = [c["id"] for c in conversations]

    # Pull messages in one batched ``in`` query when possible; Supabase REST
    # caps the ``in`` list at ~500 ids implicitly via URL length, so we chunk.
    messages: list[dict[str, Any]] = []
    truncated_messages = False
    if conversation_ids:
        for start in range(0, len(conversation_ids), 200):
            chunk = conversation_ids[start : start + 200]
            page = await _safe_select(
                query_factory=lambda c=chunk: (
                    client.table("messages")
                    .select(
                        "id, conversation_id, role, content, sources, "
                        "feedback, delivery_status, created_at, parent_message_id, is_pinned"
                    )
                    .in_("conversation_id", c)
                    .order("created_at", desc=False)
                    .limit(USER_MESSAGE_CAP - len(messages))
                ),
                label=f"messages_chunk_{start}",
            )
            messages.extend(page)
            if len(messages) >= USER_MESSAGE_CAP:
                truncated_messages = True
                messages = messages[:USER_MESSAGE_CAP]
                break

    if truncated_messages:
        notes.append(
            f"Messages truncated at {USER_MESSAGE_CAP:,} rows. Contact "
            "support@nirnayaiq.com for the remainder."
        )

    query_logs = await _safe_select(
        query_factory=lambda: (
            client.table("query_logs")
            .select(
                "id, conversation_id, message_id, query_text, intent, "
                "response_length, source_count, tool_calls, latency_ms, "
                "model_used, created_at"
            )
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(USER_QUERY_LOG_CAP)
        ),
        label="query_logs",
    )

    documents = await _safe_select(
        query_factory=lambda: (
            client.table("documents")
            .select(
                "id, name, file_type, status, chunk_count, summary, "
                "uploaded_by, created_at, updated_at"
            )
            .eq("uploaded_by", user_id)
        ),
        label="documents",
    )

    # User-scoped integrations only. Org-wide integrations belong in the
    # workspace export so we don't surface them across the team boundary
    # to a single member.
    integrations = await _safe_select(
        query_factory=lambda: (
            client.table("integrations")
            .select(_INTEGRATION_SAFE_COLUMNS)
            .eq("scope_user_id", user_id)
        ),
        label="integrations_user",
    )
    # Gmail uses a per-user table; surface it too.
    gmail_connections = await _safe_select(
        query_factory=lambda: (
            client.table("gmail_integrations")
            .select("email_address, has_send_scope, created_at, updated_at")
            .eq("user_id", user_id)
        ),
        label="gmail_integrations",
    )

    feedback = [
        {
            "message_id": m["id"],
            "feedback": m.get("feedback"),
            "created_at": m.get("created_at"),
        }
        for m in messages
        if m.get("feedback") in ("positive", "negative")
    ]

    notifications = await _safe_select(
        query_factory=lambda: (
            client.table("notifications")
            .select("id, type, title, body, link_url, read_at, created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(5000)
        ),
        label="notifications",
    )

    compliance_acks = await _safe_select(
        query_factory=lambda: (
            client.table("compliance_acknowledgements")
            .select("id, policy_id, acknowledged_at")
            .eq("user_id", user_id)
        ),
        label="compliance_acknowledgements",
    )

    competitor_watchlist = profile[0].get("competitor_names") if profile else []

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr(
            "README.txt",
            _readme(scope=f"Personal data for user {email or user_id}", generated_at=now, notes=notes),
        )
        zf.writestr(
            "profile.json",
            _json_dump(
                {
                    "id": user_id,
                    "org_id": org_id,
                    "email": email,
                    "record": (profile[0] if profile else None),
                    "exported_at": now,
                }
            ),
        )
        zf.writestr("conversations.json", _json_dump(conversations))
        zf.writestr("messages.json", _json_dump(messages))
        zf.writestr("query_logs.json", _json_dump(query_logs))
        zf.writestr("documents_metadata.json", _json_dump(documents))
        zf.writestr(
            "integrations.json",
            _json_dump(
                {
                    "user_scoped_integrations": integrations,
                    "gmail_connections": gmail_connections,
                    "credentials_excluded": True,
                }
            ),
        )
        zf.writestr("feedback.json", _json_dump(feedback))
        zf.writestr("notifications.json", _json_dump(notifications))
        zf.writestr("compliance_acknowledgements.json", _json_dump(compliance_acks))
        zf.writestr(
            "competitor_watchlist.json",
            _json_dump({"names": competitor_watchlist or []}),
        )

    return buf.getvalue()


# ── Org-wide export (admin-only) ─────────────────────────────────────────────


async def build_org_export(*, org_id: str, requested_by: str) -> bytes:
    """Assemble a ZIP of the entire workspace's data.

    Uses the service-role client because the requesting admin needs to
    read rows authored by other workspace members — which RLS correctly
    blocks under the user-scoped client (see migration 042). The route
    layer pre-verifies the admin role before calling us.

    Includes a ``billing.json`` slice (plan, status, period_end) but
    NEVER the underlying Stripe customer/subscription IDs in case the
    archive ends up forwarded outside the company.
    """
    svc = get_service_client()
    now = datetime.now(UTC).isoformat()
    notes: list[str] = []

    org = await _safe_select(
        query_factory=lambda: (
            svc.table("organizations")
            .select(
                "id, name, slug, plan, plan_status, current_period_end, "
                "cancel_at_period_end, ai_instructions, competitor_names, "
                "allow_output_sharing, metadata, created_at"
            )
            .eq("id", org_id)
        ),
        label="organization",
    )

    users = await _safe_select(
        query_factory=lambda: (
            svc.table("users")
            .select(
                "id, role, role_title, display_name, "
                "activity_private, competitor_names, created_at"
            )
            .eq("org_id", org_id)
        ),
        label="users",
    )

    documents = await _safe_select(
        query_factory=lambda: (
            svc.table("documents")
            .select(
                "id, name, file_type, status, chunk_count, summary, "
                "uploaded_by, health_score, health_label, "
                "requires_acknowledgement, created_at, updated_at"
            )
            .eq("org_id", org_id)
            .limit(ORG_DOCUMENT_CAP)
        ),
        label="documents",
    )
    if len(documents) >= ORG_DOCUMENT_CAP:
        notes.append(
            f"Documents truncated at {ORG_DOCUMENT_CAP:,} rows. Contact "
            "support@nirnayaiq.com for the remainder."
        )

    conversations = await _safe_select(
        query_factory=lambda: (
            svc.table("conversations").select("*").eq("org_id", org_id)
        ),
        label="conversations",
    )

    messages = await _safe_select(
        query_factory=lambda: (
            svc.table("messages")
            .select(
                "id, conversation_id, user_id, role, content, sources, "
                "feedback, delivery_status, created_at, parent_message_id, is_pinned"
            )
            .eq("org_id", org_id)
            .order("created_at", desc=False)
            .limit(ORG_MESSAGE_CAP)
        ),
        label="messages",
    )
    if len(messages) >= ORG_MESSAGE_CAP:
        notes.append(
            f"Messages truncated at {ORG_MESSAGE_CAP:,} rows (oldest-first). "
            "Contact support@nirnayaiq.com for the remainder."
        )

    query_logs = await _safe_select(
        query_factory=lambda: (
            svc.table("query_logs")
            .select(
                "id, user_id, conversation_id, message_id, query_text, "
                "intent, response_length, source_count, tool_calls, "
                "latency_ms, model_used, created_at"
            )
            .eq("org_id", org_id)
            .order("created_at", desc=True)
            .limit(ORG_QUERY_LOG_CAP)
        ),
        label="query_logs",
    )
    if len(query_logs) >= ORG_QUERY_LOG_CAP:
        notes.append(
            f"Query logs truncated at {ORG_QUERY_LOG_CAP:,} rows (newest-first). "
            "Contact support@nirnayaiq.com for the remainder."
        )

    integrations = await _safe_select(
        query_factory=lambda: (
            svc.table("integrations")
            .select(_INTEGRATION_SAFE_COLUMNS)
            .eq("org_id", org_id)
        ),
        label="integrations_org",
    )
    gmail_connections = await _safe_select(
        query_factory=lambda: (
            svc.table("gmail_integrations")
            .select("user_id, email_address, has_send_scope, created_at, updated_at")
            .eq("org_id", org_id)
        ),
        label="gmail_integrations",
    )

    invitations = await _safe_select(
        query_factory=lambda: (
            svc.table("invitations")
            .select("id, email, role, inviter_id, accepted_at, expires_at, created_at")
            .eq("org_id", org_id)
        ),
        label="invitations",
    )

    compliance_policies = await _safe_select(
        query_factory=lambda: (
            svc.table("compliance_policies")
            .select(
                "id, name, document_id, requires_ack, enforcement_date, created_at"
            )
            .eq("org_id", org_id)
        ),
        label="compliance_policies",
    )
    compliance_acks = await _safe_select(
        query_factory=lambda: (
            svc.table("compliance_acknowledgements")
            .select("id, user_id, policy_id, acknowledged_at")
            .eq("org_id", org_id)
        ),
        label="compliance_acknowledgements",
    )

    document_tags = await _safe_select(
        query_factory=lambda: (
            svc.table("document_tags")
            .select("id, name, created_at")
            .eq("org_id", org_id)
        ),
        label="document_tags",
    )
    document_collections = await _safe_select(
        query_factory=lambda: (
            svc.table("document_collections")
            .select("id, creator_id, name, tag_filters, created_at")
            .eq("org_id", org_id)
        ),
        label="document_collections",
    )

    # Billing summary — explicitly NOT including Stripe customer/subscription
    # IDs. The archive may be forwarded to legal/finance external to the
    # company and Stripe IDs are useful for impersonation reconnaissance.
    billing = {
        "plan": (org[0].get("plan") if org else None),
        "plan_status": (org[0].get("plan_status") if org else None),
        "current_period_end": (org[0].get("current_period_end") if org else None),
        "cancel_at_period_end": (org[0].get("cancel_at_period_end") if org else None),
    }

    org_name = org[0].get("name") if org else org_id

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr(
            "README.txt",
            _readme(
                scope=f"Workspace data for {org_name} (requested by admin {requested_by})",
                generated_at=now,
                notes=notes,
            ),
        )
        zf.writestr("organization.json", _json_dump(org[0] if org else None))
        zf.writestr("billing.json", _json_dump(billing))
        zf.writestr("users.json", _json_dump(users))
        zf.writestr("invitations.json", _json_dump(invitations))
        zf.writestr("documents.json", _json_dump(documents))
        zf.writestr("document_tags.json", _json_dump(document_tags))
        zf.writestr("document_collections.json", _json_dump(document_collections))
        zf.writestr("conversations.json", _json_dump(conversations))
        zf.writestr("messages.json", _json_dump(messages))
        zf.writestr("query_logs.json", _json_dump(query_logs))
        zf.writestr(
            "integrations.json",
            _json_dump(
                {
                    "org_integrations": integrations,
                    "gmail_connections": gmail_connections,
                    "credentials_excluded": True,
                }
            ),
        )
        zf.writestr("compliance_policies.json", _json_dump(compliance_policies))
        zf.writestr(
            "compliance_acknowledgements.json", _json_dump(compliance_acks)
        )

    return buf.getvalue()
