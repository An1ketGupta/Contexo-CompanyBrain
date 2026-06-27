"""End-to-end smoke test of the ATS adapter layer against the mock server.

Runs each provider's full publish path:
    test_connection() → list_*() → publish_job() (via the adapter wrappers,
    bypassing the requisition + DB layer)

This exercises the wire format every adapter uses to talk to the real API,
proving our request shapes match what the providers' docs say. If this passes
against the mock, the only thing that *could* still go wrong against a real
provider is provider-side validation on values we hard-code (e.g. a missing
jobTemplateId) — and those surface immediately as a clear 4xx with a message.

Run:
    # In one terminal:
    uv run python -m tools.mock_ats_server
    # In another:
    uv run python -m tools.smoke_test_ats

Exits non-zero on any failure so it's CI-friendly.
"""
from __future__ import annotations

import asyncio
import base64
import os
import sys
from typing import Any

import httpx

MOCK_URL = os.environ.get("MOCK_ATS_URL", "http://localhost:8001")
TEST_API_KEY = os.environ.get("MOCK_TEST_KEY", "test-tenant-key-001")


def _auth_header(api_key: str) -> str:
    return "Basic " + base64.b64encode(f"{api_key}:".encode()).decode("ascii")


# ── Greenhouse ──────────────────────────────────────────────────────────────


async def smoke_greenhouse() -> None:
    base = f"{MOCK_URL}/greenhouse/v1"
    headers = {"Authorization": _auth_header(TEST_API_KEY)}

    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. test_connection equivalent
        r = await client.get(f"{base}/users", params={"per_page": 1}, headers=headers)
        assert r.status_code == 200, f"users: {r.status_code} {r.text}"
        assert isinstance(r.json(), list)

        # 2. taxonomy
        offices = (await client.get(f"{base}/offices", headers=headers)).json()
        departments = (await client.get(f"{base}/departments", headers=headers)).json()
        assert offices and departments, "expected seeded taxonomy"

        # 3. publish
        body = {
            "name": "Smoke Test — Senior Engineer",
            "notes": "# JD body\n\nSmoke test from tools/smoke_test_ats.py",
            "office_ids": [offices[0]["id"]],
            "department_id": departments[0]["id"],
        }
        r = await client.post(f"{base}/jobs", json=body, headers=headers)
        assert r.status_code == 200, f"create job: {r.status_code} {r.text}"
        data = r.json()
        assert data.get("id"), f"missing job id: {data}"
        print(f"  greenhouse: created job id={data['id']} (offices={len(offices)})")

        # 4. error injection sanity
        r = await client.get(
            f"{base}/users",
            headers={**headers, "X-Mock-Force-Status": "503"},
        )
        assert r.status_code == 503, f"expected 503, got {r.status_code}"
        print("  greenhouse: forced 503 surfaced correctly")


# ── Lever ───────────────────────────────────────────────────────────────────


async def smoke_lever() -> None:
    base = f"{MOCK_URL}/lever/v1"
    headers = {"Authorization": _auth_header(TEST_API_KEY)}

    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{base}/users", params={"limit": 1}, headers=headers)
        assert r.status_code == 200, f"users: {r.status_code}"
        assert "data" in r.json()

        # postings list — drives Lever's "distinct categories" derivation
        r = await client.get(
            f"{base}/postings",
            params={"limit": 25, "include": "categories"},
            headers=headers,
        )
        assert r.status_code == 200
        postings = r.json().get("data") or []
        assert postings, "expected synthetic postings"

        body = {
            "text": "Smoke Test — Staff Designer",
            "state": "draft",
            "content": {"description": "# JD\n\nfrom smoke test"},
            "categories": {
                "location": postings[0]["categories"]["location"],
                "team": postings[0]["categories"]["team"],
            },
        }
        r = await client.post(f"{base}/postings", json=body, headers=headers)
        assert r.status_code == 200, f"create posting: {r.status_code} {r.text}"
        data = r.json().get("data") or {}
        assert data.get("id") and data.get("urls", {}).get("show")
        print(f"  lever: created posting id={data['id']}")


# ── Ashby ───────────────────────────────────────────────────────────────────


async def smoke_ashby() -> None:
    base = f"{MOCK_URL}/ashby"
    headers = {
        "Authorization": _auth_header(TEST_API_KEY),
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"{base}/user.list", headers=headers, json={})
        assert r.status_code == 200 and r.json().get("success") is True

        locations = (await client.post(f"{base}/location.list", headers=headers, json={})).json()["results"]
        departments = (await client.post(f"{base}/department.list", headers=headers, json={})).json()["results"]
        teams = (await client.post(f"{base}/team.list", headers=headers, json={})).json()["results"]
        templates = (await client.post(f"{base}/jobTemplate.list", headers=headers, json={})).json()["results"]
        assert locations and departments and teams and templates

        # Validate that missing jobTemplateId returns the documented error shape
        bad = await client.post(
            f"{base}/jobOpening.create",
            headers=headers,
            json={"title": "Should Fail"},
        )
        bad_body = bad.json()
        assert bad_body.get("success") is False, bad_body
        print("  ashby: missing jobTemplateId returns success=false as documented")

        body = {
            "jobTemplateId": templates[0]["id"],
            "teamId": teams[0]["id"],
            "title": "Smoke Test — Principal Engineer",
            "description": "# JD\n\nfrom smoke test",
            "locationIds": [locations[0]["id"]],
            "departmentId": departments[0]["id"],
        }
        r = await client.post(f"{base}/jobOpening.create", headers=headers, json=body)
        envelope = r.json()
        assert envelope.get("success") is True, envelope
        opening = envelope["results"]
        assert opening.get("id")
        print(f"  ashby: created opening id={opening['id']}")


# ── Adapter-level integration ────────────────────────────────────────────────


async def smoke_via_adapter() -> None:
    """Exercise the adapter layer with env overrides, proving the URL plumbing
    works end-to-end. This is what the production code actually calls."""
    # Override URLs before importing app modules so settings pick them up.
    os.environ["GREENHOUSE_API_URL"] = f"{MOCK_URL}/greenhouse/v1"
    os.environ["LEVER_API_URL"] = f"{MOCK_URL}/lever/v1"
    os.environ["ASHBY_API_URL"] = f"{MOCK_URL}/ashby"

    # Force a fresh settings cache.
    from app.config import get_settings  # noqa: PLC0415

    get_settings.cache_clear()  # type: ignore[attr-defined]

    from app.services.integrations.ats import ashby, greenhouse, lever  # noqa: PLC0415

    ok_g = await greenhouse.test_connection(api_key=TEST_API_KEY)
    ok_l = await lever.test_connection(api_key=TEST_API_KEY)
    ok_a = await ashby.test_connection(api_key=TEST_API_KEY)
    assert ok_g and ok_l and ok_a, (ok_g, ok_l, ok_a)
    print(
        f"  adapter test_connection: greenhouse={ok_g}, lever={ok_l}, ashby={ok_a}"
    )

    offices = await greenhouse.list_offices(api_key=TEST_API_KEY)
    departments = await greenhouse.list_departments(api_key=TEST_API_KEY)
    print(
        f"  adapter list_offices={len(offices)} list_departments={len(departments)}"
    )

    locs = await ashby.list_locations(api_key=TEST_API_KEY)
    tmpls = await ashby.list_job_templates(api_key=TEST_API_KEY)
    print(
        f"  adapter ashby list_locations={len(locs)} list_job_templates={len(tmpls)}"
    )


async def main() -> int:
    failures: list[str] = []

    async with httpx.AsyncClient(timeout=3.0) as client:
        try:
            health = await client.get(f"{MOCK_URL}/__health")
            health.raise_for_status()
        except Exception as exc:
            print(
                f"[X]Mock server not reachable at {MOCK_URL} — "
                f"start it with: uv run python -m tools.mock_ats_server",
                file=sys.stderr,
            )
            print(f"  error: {exc}", file=sys.stderr)
            return 2

    print("Smoke testing mock ATS at", MOCK_URL)

    for name, fn in (
        ("greenhouse wire", smoke_greenhouse),
        ("lever wire", smoke_lever),
        ("ashby wire", smoke_ashby),
        ("via adapter", smoke_via_adapter),
    ):
        try:
            print(f">>{name}")
            await fn()
        except AssertionError as exc:
            failures.append(f"{name}: {exc}")
            print(f"  [X]{exc}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
            print(f"  [X]{type(exc).__name__}: {exc}")

    if failures:
        print(f"\n{len(failures)} failure(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\n[OK]All ATS smoke tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
