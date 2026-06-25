"""Linear adapter (per-user OAuth, GraphQL).

Linear's API is GraphQL-only. We wrap the few mutations/queries we need in
typed helpers — no graphql client dep needed; httpx + raw query strings.

Surface:
  list_teams(org_id, user_id)
  create_issue(org_id, user_id, title, description, assignee_email, due_date)
  get_issue_status(org_id, user_id, issue_id)

Storage: `integrations` table, provider='linear', scope_user_id=user_id.
`resources.team_id` is the user-picked default team where new issues land.

Linear access tokens don't expire (per their docs), but we still store
token_expiry so the unified table doesn't need a polymorphic codepath.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.database import get_service_client

log = logging.getLogger(__name__)

PROVIDER = "linear"
_API = "https://api.linear.app/graphql"


async def _get_row(*, org_id: str, user_id: str) -> dict[str, Any] | None:
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("integrations")
        .select("*")
        .eq("org_id", org_id)
        .eq("provider", PROVIDER)
        .eq("scope_user_id", user_id)
        .maybe_single()
        .execute()
    )
    return res.data if res else None


async def _get_token(*, org_id: str, user_id: str) -> str:
    row = await _get_row(org_id=org_id, user_id=user_id)
    if not row:
        raise PermissionError("linear_not_connected")
    return row["access_token"]


async def _graphql(*, token: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        resp = await client.post(
            _API,
            headers={
                "Authorization": token,  # Linear takes raw token, not "Bearer "
                "Content-Type": "application/json",
            },
            json={"query": query, "variables": variables},
        )
    if resp.status_code == 401:
        raise PermissionError("linear_unauthorized")
    if resp.status_code >= 400:
        raise RuntimeError(f"linear_http_{resp.status_code}: {resp.text[:200]}")
    payload = resp.json() or {}
    if "errors" in payload:
        # Logical GraphQL error. Surface the first message.
        msg = payload["errors"][0].get("message", "linear_graphql_error")
        raise RuntimeError(f"linear_gql_error: {msg}")
    return payload.get("data") or {}


async def list_teams(*, org_id: str, user_id: str) -> list[dict[str, Any]]:
    token = await _get_token(org_id=org_id, user_id=user_id)
    data = await _graphql(
        token=token,
        query="query Teams { teams { nodes { id name key } } }",
        variables={},
    )
    return ((data.get("teams") or {}).get("nodes") or [])


async def _resolve_user_id_by_email(
    *, token: str, email: str
) -> str | None:
    """Linear has no email lookup endpoint — we list workspace members
    (paginated) and match locally. Cached in-process per call only; for
    persistent caching we'd need a small `linear_user_cache` table, which
    is overkill for now (queries are cheap, 250 users/page)."""
    data = await _graphql(
        token=token,
        query="query Members { users(first: 250) { nodes { id email } } }",
        variables={},
    )
    for u in ((data.get("users") or {}).get("nodes") or []):
        if (u.get("email") or "").lower() == email.lower():
            return u["id"]
    return None


async def create_issue(
    *,
    org_id: str,
    user_id: str,
    title: str,
    description: str | None,
    assignee_email: str | None,
    due_date: str | None,  # YYYY-MM-DD
) -> dict[str, Any]:
    """Create an issue in the user's default team. Returns {issue_id, url}."""
    row = await _get_row(org_id=org_id, user_id=user_id)
    if not row:
        raise PermissionError("linear_not_connected")
    token = row["access_token"]
    team_id = (row.get("resources") or {}).get("team_id")
    if not team_id:
        raise PermissionError("linear_no_team_selected")

    assignee_id: str | None = None
    if assignee_email:
        try:
            assignee_id = await _resolve_user_id_by_email(token=token, email=assignee_email)
        except Exception as exc:
            log.warning("linear.resolve_assignee_failed email=%s err=%s", assignee_email, exc)

    mutation = """
    mutation CreateIssue($input: IssueCreateInput!) {
      issueCreate(input: $input) {
        success
        issue { id identifier url }
      }
    }
    """
    input_obj: dict[str, Any] = {
        "title": title[:255],
        "description": description or "",
        "teamId": team_id,
    }
    if assignee_id:
        input_obj["assigneeId"] = assignee_id
    if due_date:
        input_obj["dueDate"] = due_date

    data = await _graphql(token=token, query=mutation, variables={"input": input_obj})
    result = (data.get("issueCreate") or {})
    if not result.get("success"):
        raise RuntimeError("linear_issue_create_returned_failure")
    issue = result.get("issue") or {}
    return {
        "task_id": issue.get("id"),
        "identifier": issue.get("identifier"),
        "url": issue.get("url"),
    }


async def get_issue_status(
    *, org_id: str, user_id: str, issue_id: str
) -> dict[str, Any]:
    """Returns {state, completed_at, identifier} or {status: 'missing'}."""
    token = await _get_token(org_id=org_id, user_id=user_id)
    data = await _graphql(
        token=token,
        query="""
        query Issue($id: String!) {
          issue(id: $id) {
            id identifier completedAt
            state { name type }
          }
        }
        """,
        variables={"id": issue_id},
    )
    issue = data.get("issue")
    if not issue:
        return {"status": "missing"}
    return {
        "id": issue["id"],
        "identifier": issue.get("identifier"),
        "completed_at": issue.get("completedAt"),
        "state": (issue.get("state") or {}).get("name"),
        "state_type": (issue.get("state") or {}).get("type"),  # 'completed' | 'started' | etc.
    }
