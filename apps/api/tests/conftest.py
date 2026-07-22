"""Shared fixtures for the backend test suite.

Integration tests (@pytest.mark.integration) talk to a real Supabase
instance. They refuse to run unless SUPABASE_URL points at 127.0.0.1/
localhost, so a stray `.env` pointing at a shared/hosted project can never
get written to by a test run — point these env vars at your local
`supabase start` instance before running:
  SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from supabase import Client, create_client

from app.config import get_settings


def _require_local_supabase() -> None:
    url = get_settings().supabase_url
    if "127.0.0.1" not in url and "localhost" not in url:
        pytest.skip(
            f"SUPABASE_URL ({url}) is not a local instance — refusing to run "
            "integration tests against a shared/remote database."
        )


@pytest.fixture(scope="session")
def service_client() -> Client:
    _require_local_supabase()
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


@pytest.fixture()
def org(service_client: Client) -> Iterator[dict]:
    row = (
        service_client.table("organizations")
        .insert({"name": "Test Org", "slug": f"test-org-{uuid.uuid4().hex[:10]}"})
        .execute()
    )
    org_row = row.data[0]
    yield org_row
    # Cascades to users/documents/chunks/embeddings/document_shares/etc — a
    # blanket safety net so individual tests don't need bespoke teardown.
    service_client.table("organizations").delete().eq("id", org_row["id"]).execute()


@dataclass
class TestUser:
    id: str
    email: str
    org_id: str
    client: Client  # authenticated as this user — respects RLS


@pytest.fixture()
def make_user(service_client: Client, org: dict):
    settings = get_settings()
    created: list[str] = []

    def _make(role: str = "member") -> TestUser:
        email = f"test-{uuid.uuid4().hex[:12]}@example.test"
        password = f"Test-{uuid.uuid4().hex}!"
        resp = service_client.auth.admin.create_user(
            {"email": email, "password": password, "email_confirm": True}
        )
        user_id = resp.user.id
        created.append(user_id)
        service_client.table("users").insert(
            {"id": user_id, "org_id": org["id"], "role": role}
        ).execute()

        user_client = create_client(settings.supabase_url, settings.supabase_anon_key)
        session = user_client.auth.sign_in_with_password(
            {"email": email, "password": password}
        )
        user_client.postgrest.auth(session.session.access_token)

        return TestUser(id=user_id, email=email, org_id=org["id"], client=user_client)

    yield _make

    for uid in created:
        try:
            service_client.auth.admin.delete_user(uid)
        except Exception:
            pass
