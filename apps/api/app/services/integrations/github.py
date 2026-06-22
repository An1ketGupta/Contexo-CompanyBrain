"""GitHub App integration.

GitHub Apps DON'T use the OAuth refresh-token dance. Instead:

  1. User installs the app from the GitHub Marketplace / direct install URL.
     GitHub redirects to our callback with ?installation_id=… .
  2. To call the API, we mint a short (10-minute) JWT signed with the App's
     private key — that's the "app JWT".
  3. The app JWT is exchanged at
        POST /app/installations/{id}/access_tokens
     for a 1-hour `installation_access_token` (a `ghs_…` token).
  4. We use `ghs_…` for all data plane calls.

Token lifecycle: cache the `ghs_…` in our `access_token` column with the
right `token_expiry`. When _unified.ensure_fresh_token would normally refresh
via the OAuth dance, we instead detect provider='github' and call back into
this module's `mint_installation_token()` (see `get_installation_token` below).

`resources` JSONB shape:
    [
      {"repo_id": 123, "full_name": "acme/handbook",
       "default_branch": "main",
       "include_markdown": true, "include_issues": true,
       "include_discussions": true},
    ]

`metadata` JSONB:
    {"installation_id": 9876, "account_login": "acme", "account_type": "Organization"}

Sync cursor: ISO timestamp; we ask for issues+discussions+commits modified
since this value, per repo.

GraphQL is used for Discussions because the REST surface for Discussions is
limited (no `since` filter, no body content on the list endpoint).
"""
from __future__ import annotations

import base64
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import get_settings
from app.services.integrations import _unified

log = logging.getLogger(__name__)

PROVIDER = "github"

_GH_API = "https://api.github.com"

# Doc-style files we extract — README at any depth, /docs/**, .md, .mdx.
_DOC_EXT = {"md", "mdx"}
_DOC_PATH_PREFIXES = ("docs/", "doc/")
_README_NAMES = {"readme", "readme.md", "readme.mdx", "readme.txt"}


# Note: we intentionally do NOT call _unified.register_refresh() — the GitHub
# refresh dance is bespoke (mint app JWT → swap for installation token).
# `ensure_fresh_token` in this module's sync paths shadows the generic helper.


# ── App JWT + installation tokens ───────────────────────────────────────────


def _build_app_jwt() -> str:
    """Mint a 10-minute JWT signed with the App's private key.

    PyJWT is already a transitive dep of supabase-py via gotrue, so we don't
    add a new package. The audience claim is set by App ID; GitHub validates
    iss=app_id + iat slightly in the past + exp ≤ 10 min.
    """
    import jwt as pyjwt
    settings = get_settings()
    if not settings.github_app_id or not settings.github_app_private_key:
        raise RuntimeError("github_app_not_configured")

    # Allow private key newlines to be escaped in .env (single-line storage).
    pem = settings.github_app_private_key.replace("\\n", "\n")
    now = int(time.time())
    payload = {
        # 60s clock-skew tolerance — GitHub rejects iat in the future and
        # is tetchy on clocks that drift across containers.
        "iat": now - 60,
        "exp": now + (8 * 60),
        "iss": str(settings.github_app_id),
    }
    return pyjwt.encode(payload, pem, algorithm="RS256")


async def mint_installation_token(installation_id: int) -> tuple[str, datetime]:
    """Exchange the app JWT for an installation_access_token (`ghs_...`).

    Returns (token, expires_at). Tokens are valid for 1 hour and the
    response carries the absolute expiry, which we round-trip into our
    `token_expiry` column.
    """
    app_jwt = _build_app_jwt()
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.post(
            f"{_GH_API}/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"github_installation_token_failed: {resp.status_code} {resp.text[:200]}"
        )
    body = resp.json()
    expiry = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
    return body["token"], expiry


async def _get_installation_token(row: dict[str, Any]) -> str:
    """Return a non-expired installation_access_token, refreshing if needed.

    We bypass _unified.ensure_fresh_token because GitHub's refresh isn't an
    OAuth refresh; it's a JWT exchange. Caching keeps us under the per-app
    rate limit for token mints.
    """
    expiry_iso = row.get("token_expiry")
    if expiry_iso:
        try:
            exp = datetime.fromisoformat(expiry_iso.replace("Z", "+00:00"))
            if exp > datetime.now(UTC) + timedelta(seconds=60):
                return row["access_token"]
        except ValueError:
            pass

    installation_id = (row.get("metadata") or {}).get("installation_id")
    if not installation_id:
        raise RuntimeError("github_no_installation_id")
    token, expires_at = await mint_installation_token(int(installation_id))
    await _unified.update_fields(
        org_id=row["org_id"],
        provider=PROVIDER,
        fields={"access_token": token, "token_expiry": expires_at.isoformat()},
    )
    return token


# ── Install flow ────────────────────────────────────────────────────────────


def build_install_url(*, state: str) -> str:
    """The "install GitHub App" URL.

    Unlike OAuth, there's no `code` to exchange — GitHub redirects with
    `installation_id` and `setup_action`. We round-trip `state` through the
    `state` query param exactly like OAuth so we can bind back to the org.
    """
    settings = get_settings()
    if not settings.github_app_slug:
        raise RuntimeError("github_app_slug_not_configured")
    params = {"state": state}
    return (
        f"https://github.com/apps/{settings.github_app_slug}/installations/new"
        f"?{urlencode(params)}"
    )


async def complete_install(
    *, org_id: str, user_id: str, installation_id: int
) -> None:
    """Persist the install + mint the first installation token so the sync
    cron has something to work with on the next tick."""
    token, expires_at = await mint_installation_token(installation_id)

    # Look up the install's account (so the UI can label it).
    app_jwt = _build_app_jwt()
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        info = await client.get(
            f"{_GH_API}/app/installations/{installation_id}",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
            },
        )
    account: dict[str, Any] = {}
    if info.status_code == 200:
        acct = (info.json().get("account") or {})
        account = {
            "account_login": acct.get("login"),
            "account_type": acct.get("type"),
            "avatar_url": acct.get("avatar_url"),
        }

    await _unified.upsert_row(
        org_id=org_id,
        provider=PROVIDER,
        connected_by=user_id,
        access_token=token,
        refresh_token=None,
        token_expiry=expires_at,
        scopes=[],
        metadata={"installation_id": installation_id, **account},
    )


async def disconnect(*, org_id: str) -> None:
    """Drop the integrations row. GitHub still has the App installed on
    the user's account — they uninstall via github.com/settings/installations
    if they want to fully revoke (a UI hint we surface on disconnect)."""
    await _unified.delete_row(org_id=org_id, provider=PROVIDER)


# ── Repo enumeration ────────────────────────────────────────────────────────


async def list_installation_repos(*, org_id: str) -> list[dict[str, Any]]:
    row = await _unified.get_row(org_id=org_id, provider=PROVIDER)
    if not row:
        return []
    token = await _get_installation_token(row)
    out: list[dict[str, Any]] = []
    url = f"{_GH_API}/installation/repositories?per_page=100"
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        while url:
            resp = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            if resp.status_code != 200:
                log.warning("github_repos_failed %s %s", resp.status_code, resp.text[:200])
                break
            for r in resp.json().get("repositories") or []:
                out.append(
                    {
                        "repo_id": r["id"],
                        "full_name": r["full_name"],
                        "default_branch": r.get("default_branch") or "main",
                        "private": r.get("private", False),
                        "description": r.get("description"),
                    }
                )
            # GitHub uses RFC 5988 Link headers for pagination.
            link = resp.headers.get("link") or ""
            nxt = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    nxt = part.split(";")[0].strip().lstrip("<").rstrip(">")
                    break
            url = nxt
    return out


async def update_resources(
    *, org_id: str, resources: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for r in resources or []:
        if not r.get("repo_id") or not r.get("full_name"):
            continue
        cleaned.append(
            {
                "repo_id": int(r["repo_id"]),
                "full_name": str(r["full_name"])[:200],
                "default_branch": str(r.get("default_branch") or "main")[:120],
                "include_markdown": bool(r.get("include_markdown", True)),
                "include_issues": bool(r.get("include_issues", True)),
                "include_discussions": bool(r.get("include_discussions", True)),
            }
        )
    row = await _unified.get_row(org_id=org_id, provider=PROVIDER)
    if not row:
        raise RuntimeError("github_not_connected")
    import asyncio as _asyncio

    from app.database import get_service_client
    svc = get_service_client()
    await _asyncio.to_thread(
        lambda: svc.table("integrations")
        .update({"resources": cleaned})
        .eq("id", row["id"])
        .execute()
    )
    return cleaned


# ── Polling sync ────────────────────────────────────────────────────────────


async def poll_all_integrations() -> dict[str, Any]:
    rows = await _unified.list_org_rows(provider=PROVIDER)
    synced, errors = 0, 0
    for row in rows:
        try:
            await sync_org(org_id=row["org_id"])
            synced += 1
        except Exception as exc:
            errors += 1
            log.warning("github_sync_failed org=%s err=%s", row["org_id"], exc)
            await _unified.mark_error(
                org_id=row["org_id"], provider=PROVIDER, error=str(exc)
            )
    return {"synced": synced, "errors": errors}


async def sync_org(*, org_id: str) -> dict[str, Any]:
    row = await _unified.get_row(org_id=org_id, provider=PROVIDER)
    if not row:
        return {"status": "no-integration"}
    resources = row.get("resources") or []
    if not resources:
        return {"status": "no-resources"}

    token = await _get_installation_token(row)
    since = row.get("sync_cursor") or "1970-01-01T00:00:00Z"

    total = 0
    newest = since
    for repo in resources:
        try:
            n, latest = await _sync_repo(
                org_id=org_id,
                user_id=row.get("connected_by"),
                token=token,
                repo=repo,
                since=since,
            )
            total += n
            if latest > newest:
                newest = latest
        except Exception as exc:
            log.warning(
                "github_repo_sync_failed org=%s repo=%s err=%s",
                org_id, repo.get("full_name"), exc,
            )

    await _unified.mark_synced(org_id=org_id, provider=PROVIDER, cursor=newest)
    return {"status": "ok", "ingested": total}


async def _sync_repo(
    *,
    org_id: str,
    user_id: str | None,
    token: str,
    repo: dict[str, Any],
    since: str,
) -> tuple[int, str]:
    full_name = repo["full_name"]
    default_branch = repo.get("default_branch") or "main"
    ingested = 0
    newest = since

    if repo.get("include_markdown"):
        try:
            ingested += await _ingest_markdown_tree(
                org_id=org_id,
                user_id=user_id,
                token=token,
                full_name=full_name,
                default_branch=default_branch,
                since=since,
            )
        except Exception as exc:
            log.warning("github_md_sync_failed repo=%s err=%s", full_name, exc)

    if repo.get("include_issues"):
        try:
            n, newest_iss = await _ingest_issues(
                org_id=org_id,
                user_id=user_id,
                token=token,
                full_name=full_name,
                since=since,
            )
            ingested += n
            if newest_iss > newest:
                newest = newest_iss
        except Exception as exc:
            log.warning("github_issues_sync_failed repo=%s err=%s", full_name, exc)

    if repo.get("include_discussions"):
        try:
            n, newest_disc = await _ingest_discussions(
                org_id=org_id,
                user_id=user_id,
                token=token,
                full_name=full_name,
                since=since,
            )
            ingested += n
            if newest_disc > newest:
                newest = newest_disc
        except Exception as exc:
            log.warning(
                "github_discussions_sync_failed repo=%s err=%s", full_name, exc
            )

    return ingested, newest


# ── Markdown / doc tree ─────────────────────────────────────────────────────


def _is_doc_path(path: str) -> bool:
    """README at any depth, /docs/**, and anything *.md / *.mdx."""
    lower = path.lower()
    name = lower.rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if name in _README_NAMES:
        return True
    if ext in _DOC_EXT:
        return True
    if any(lower.startswith(p) or f"/{p}" in lower for p in _DOC_PATH_PREFIXES):
        # any file inside docs/ — but only if it's text-ish
        return ext in {"md", "mdx", "txt", "rst", "adoc"}
    return False


async def _ingest_markdown_tree(
    *,
    org_id: str,
    user_id: str | None,
    token: str,
    full_name: str,
    default_branch: str,
    since: str,
) -> int:
    """Walk the repo tree once; ingest doc-ish files whose last commit is
    newer than `since`. We use the commits-since shortcut: ask for commits
    after `since`, and only inspect files touched by those commits.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        # Commits since the last sync, on the default branch.
        commits_resp = await client.get(
            f"{_GH_API}/repos/{full_name}/commits",
            params={"since": since, "sha": default_branch, "per_page": "100"},
            headers=headers,
        )
        if commits_resp.status_code != 200:
            return 0
        commits = commits_resp.json() or []
        changed_paths: set[str] = set()
        for c in commits[:30]:  # cap detail-fetches per cycle
            sha = c.get("sha")
            if not sha:
                continue
            cdet = await client.get(
                f"{_GH_API}/repos/{full_name}/commits/{sha}",
                headers=headers,
            )
            if cdet.status_code != 200:
                continue
            for f in cdet.json().get("files") or []:
                p = f.get("filename")
                if p and _is_doc_path(p) and f.get("status") != "removed":
                    changed_paths.add(p)

        # If `since` is at the epoch (fresh install), walk the entire tree.
        if since.startswith("1970"):
            tree_resp = await client.get(
                f"{_GH_API}/repos/{full_name}/git/trees/{default_branch}",
                params={"recursive": "1"},
                headers=headers,
            )
            if tree_resp.status_code == 200:
                tree = tree_resp.json()
                for item in tree.get("tree") or []:
                    p = item.get("path")
                    if item.get("type") == "blob" and p and _is_doc_path(p):
                        changed_paths.add(p)

        ingested = 0
        for path in list(changed_paths)[:100]:
            try:
                content = await _fetch_file(client, full_name, path, default_branch, headers)
                if not content:
                    continue
                await _unified.queue_text_ingest(
                    org_id=org_id,
                    source=PROVIDER,
                    external_id=f"{full_name}:{path}",
                    name=f"{full_name}/{path}",
                    file_type="md",
                    text=content,
                    user_id=user_id,
                    source_url=f"https://github.com/{full_name}/blob/{default_branch}/{path}",
                )
                ingested += 1
            except Exception as exc:
                log.warning(
                    "github_file_ingest_failed repo=%s path=%s err=%s",
                    full_name, path, exc,
                )

    return ingested


async def _fetch_file(
    client: httpx.AsyncClient,
    full_name: str,
    path: str,
    ref: str,
    headers: dict[str, str],
) -> str:
    resp = await client.get(
        f"{_GH_API}/repos/{full_name}/contents/{path}",
        params={"ref": ref},
        headers=headers,
    )
    if resp.status_code != 200:
        return ""
    body = resp.json()
    encoding = body.get("encoding")
    raw = body.get("content") or ""
    if encoding != "base64":
        return raw
    try:
        return base64.b64decode(raw).decode("utf-8", errors="replace")
    except Exception:
        return ""


# ── Issues ──────────────────────────────────────────────────────────────────


async def _ingest_issues(
    *,
    org_id: str,
    user_id: str | None,
    token: str,
    full_name: str,
    since: str,
) -> tuple[int, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = (
        f"{_GH_API}/repos/{full_name}/issues"
        f"?state=all&since={since}&per_page=50&sort=updated&direction=desc"
    )
    ingested = 0
    newest = since
    pages = 0
    MAX_PAGES = 4

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        while url and pages < MAX_PAGES:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                break
            issues = resp.json() or []
            for iss in issues:
                # GitHub's /issues endpoint returns BOTH issues and PRs; skip
                # PRs (the v1 plan keeps PR descriptions out of scope).
                if iss.get("pull_request"):
                    continue
                updated = iss.get("updated_at") or ""
                if updated > newest:
                    newest = updated
                number = iss.get("number")
                title = iss.get("title") or f"Issue #{number}"
                body = iss.get("body") or ""
                labels = ", ".join(
                    l.get("name", "") for l in (iss.get("labels") or [])
                )
                # Synthesize a markdown doc — issue + top comments. Keep it
                # short so embeddings stay focused on the prose, not metadata.
                text = (
                    f"# {title}\n\n"
                    f"**State:** {iss.get('state')}  "
                    f"**Author:** {(iss.get('user') or {}).get('login')}  "
                    f"**Labels:** {labels}\n\n"
                    f"{body}\n"
                )
                # Best-effort: pull top-level comments too if the count is small.
                if iss.get("comments", 0) > 0 and iss.get("comments", 0) <= 20:
                    try:
                        cresp = await client.get(
                            iss["comments_url"], headers=headers, params={"per_page": "20"}
                        )
                        if cresp.status_code == 200:
                            for c in cresp.json() or []:
                                cu = (c.get("user") or {}).get("login")
                                text += f"\n---\n**Comment from {cu}:**\n\n{c.get('body') or ''}\n"
                    except Exception:
                        pass

                await _unified.queue_text_ingest(
                    org_id=org_id,
                    source=PROVIDER,
                    external_id=f"{full_name}#issue-{number}",
                    name=f"{full_name} #{number}: {title}",
                    file_type="md",
                    text=text,
                    user_id=user_id,
                    source_url=iss.get("html_url"),
                )
                ingested += 1

            link = resp.headers.get("link") or ""
            nxt = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    nxt = part.split(";")[0].strip().lstrip("<").rstrip(">")
                    break
            url = nxt
            pages += 1

    return ingested, newest


# ── Discussions (GraphQL) ───────────────────────────────────────────────────


_DISCUSSIONS_QUERY = """
query ($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    discussions(first: 25, after: $cursor, orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes {
        id
        number
        title
        body
        url
        updatedAt
        author { login }
        category { name }
        comments(first: 10) {
          nodes { body author { login } }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""


async def _ingest_discussions(
    *,
    org_id: str,
    user_id: str | None,
    token: str,
    full_name: str,
    since: str,
) -> tuple[int, str]:
    owner, _, name = full_name.partition("/")
    if not owner or not name:
        return 0, since

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    cursor: str | None = None
    ingested = 0
    newest = since
    pages = 0
    MAX_PAGES = 3

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        while pages < MAX_PAGES:
            resp = await client.post(
                f"{_GH_API}/graphql",
                json={
                    "query": _DISCUSSIONS_QUERY,
                    "variables": {"owner": owner, "name": name, "cursor": cursor},
                },
                headers=headers,
            )
            if resp.status_code != 200:
                break
            payload = resp.json()
            repo = (payload.get("data") or {}).get("repository") or {}
            disc = repo.get("discussions") or {}
            nodes = disc.get("nodes") or []
            should_stop = False

            for d in nodes:
                updated = d.get("updatedAt") or ""
                if updated > newest:
                    newest = updated
                if updated and updated <= since:
                    # Sorted newest-first → everything after is older too.
                    should_stop = True
                    break
                number = d.get("number")
                title = d.get("title") or f"Discussion #{number}"
                author = (d.get("author") or {}).get("login")
                category = (d.get("category") or {}).get("name")
                text = (
                    f"# {title}\n\n"
                    f"**Category:** {category}  **Author:** {author}\n\n"
                    f"{d.get('body') or ''}\n"
                )
                for c in (d.get("comments") or {}).get("nodes") or []:
                    cu = (c.get("author") or {}).get("login")
                    text += f"\n---\n**Comment from {cu}:**\n\n{c.get('body') or ''}\n"

                await _unified.queue_text_ingest(
                    org_id=org_id,
                    source=PROVIDER,
                    external_id=f"{full_name}#discussion-{number}",
                    name=f"{full_name} discussion #{number}: {title}",
                    file_type="md",
                    text=text,
                    user_id=user_id,
                    source_url=d.get("url"),
                )
                ingested += 1

            if should_stop or not disc.get("pageInfo", {}).get("hasNextPage"):
                break
            cursor = disc["pageInfo"].get("endCursor")
            pages += 1

    return ingested, newest


# ── Webhook payload verification (used by the webhook stub) ─────────────────


def verify_webhook_signature(*, body: bytes, signature: str | None) -> bool:
    """Verify GitHub's X-Hub-Signature-256 header against our app webhook
    secret. Returns False if the signature is missing or doesn't match."""
    settings = get_settings()
    secret = settings.github_app_webhook_secret
    if not secret or not signature:
        return False
    if not signature.startswith("sha256="):
        return False
    import hashlib
    import hmac as _hmac
    expected = "sha256=" + _hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return _hmac.compare_digest(expected, signature)
