"""Integration tests for the Zoom webhook router (routers/integrations_v2.py
::zoom_webhook), exercising the real HTTP path with a valid HMAC signature
against a real local Supabase instance. Only the Inngest send in the
recording.transcript_completed branch is stubbed out — everything else
(account resolution, roster buffering, host/attendee opt-in resolution)
runs for real.

Run with: pytest -m integration (excluded from the default run — see pytest.ini).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import httpx
import pytest
from fastapi import FastAPI

from app.config import get_settings
from app.routers import integrations_v2
from app.services.integrations import _unified

pytestmark = pytest.mark.integration


def _signed_headers(body: bytes) -> dict[str, str]:
    settings = get_settings()
    timestamp = "1700000000"
    message = f"v0:{timestamp}:{body.decode('utf-8')}"
    signature = "v0=" + hmac.new(
        settings.zoom_webhook_secret_token.encode(), message.encode(), hashlib.sha256
    ).hexdigest()
    return {"x-zm-signature": signature, "x-zm-request-timestamp": timestamp}


@pytest.fixture()
async def client():
    app = FastAPI()
    app.include_router(integrations_v2.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _connect_zoom(org_id: str, connector_id: str, account_id: str) -> None:
    await _unified.upsert_row(
        org_id=org_id,
        provider="zoom",
        connected_by=connector_id,
        access_token="fake-token",
        metadata={"account_id": account_id, "email": "connector@example.com"},
    )


async def test_bad_signature_is_rejected(client):
    body = json.dumps({"event": "meeting.participant_joined", "payload": {}}).encode()
    resp = await client.post(
        "/webhooks/zoom",
        content=body,
        headers={
            "content-type": "application/json",
            "x-zm-signature": "v0=deadbeef",
            "x-zm-request-timestamp": "1700000000",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "reason": "signature"}


async def test_participant_joined_buffers_roster(client, service_client, org):
    account_id = f"acct-{uuid.uuid4().hex}"
    meeting_uuid = f"meeting-{uuid.uuid4().hex}"
    connector = service_client.auth.admin.create_user(
        {"email": f"c-{uuid.uuid4().hex}@example.test", "password": "x", "email_confirm": True}
    ).user
    service_client.table("users").insert(
        {"id": connector.id, "org_id": org["id"], "role": "admin"}
    ).execute()
    await _connect_zoom(org["id"], connector.id, account_id)

    body = json.dumps(
        {
            "event": "meeting.participant_joined",
            "payload": {
                "account_id": account_id,
                "object": {
                    "uuid": meeting_uuid,
                    "participant": {"email": "Attendee@Example.com"},
                },
            },
        }
    ).encode()
    resp = await client.post(
        "/webhooks/zoom", content=body, headers=_signed_headers(body)
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    rows = (
        service_client.table("zoom_meeting_participants")
        .select("*")
        .eq("meeting_uuid", meeting_uuid)
        .execute()
        .data
    )
    assert len(rows) == 1
    assert rows[0]["email"] == "attendee@example.com"

    service_client.auth.admin.delete_user(connector.id)


async def test_transcript_completed_skips_non_opted_in_host(
    client, service_client, org, monkeypatch
):
    account_id = f"acct-{uuid.uuid4().hex}"
    connector = service_client.auth.admin.create_user(
        {"email": f"c-{uuid.uuid4().hex}@example.test", "password": "x", "email_confirm": True}
    ).user
    service_client.table("users").insert(
        {"id": connector.id, "org_id": org["id"], "role": "admin"}
    ).execute()
    await _connect_zoom(org["id"], connector.id, account_id)

    sent_events: list[dict] = []

    class _StubInngestClient:
        async def send(self, event):
            sent_events.append(event)

    monkeypatch.setattr(
        "app.inngest.client.get_inngest_client", lambda: _StubInngestClient()
    )

    body = json.dumps(
        {
            "event": "recording.transcript_completed",
            "payload": {
                "account_id": account_id,
                "object": {
                    "uuid": f"meeting-{uuid.uuid4().hex}",
                    "host_email": "never-opted-in@example.com",
                    "topic": "Weekly Sync",
                    "recording_files": [
                        {"file_type": "TRANSCRIPT", "download_url": "https://zoom.example/t.vtt"}
                    ],
                },
            },
        }
    ).encode()
    resp = await client.post(
        "/webhooks/zoom", content=body, headers=_signed_headers(body)
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert sent_events == []  # never reached the Inngest send

    service_client.auth.admin.delete_user(connector.id)


async def test_transcript_completed_resolves_opted_in_attendees(
    client, service_client, org, make_user, monkeypatch
):
    host = make_user()
    attendee = make_user()
    account_id = f"acct-{uuid.uuid4().hex}"
    meeting_uuid = f"meeting-{uuid.uuid4().hex}"

    await _connect_zoom(org["id"], host.id, account_id)
    await integrations_v2.zoom_svc.set_transcript_optin(
        org_id=org["id"], user_id=host.id, email="host@example.com", opted_in=True
    )
    await integrations_v2.zoom_svc.set_attendee_optin(
        org_id=org["id"], user_id=attendee.id, email="attendee@example.com", opted_in=True
    )
    # Roster buffered earlier by a participant_joined event.
    await integrations_v2.zoom_svc.record_participant(
        org_id=org["id"], meeting_uuid=meeting_uuid, email="attendee@example.com"
    )

    sent_events: list[dict] = []

    class _StubInngestClient:
        async def send(self, event):
            sent_events.append(event)

    monkeypatch.setattr(
        "app.inngest.client.get_inngest_client", lambda: _StubInngestClient()
    )

    body = json.dumps(
        {
            "event": "recording.transcript_completed",
            "payload": {
                "account_id": account_id,
                "object": {
                    "uuid": meeting_uuid,
                    "host_email": "host@example.com",
                    "topic": "Weekly Sync",
                    "recording_files": [
                        {"file_type": "TRANSCRIPT", "download_url": "https://zoom.example/t.vtt"}
                    ],
                },
            },
        }
    ).encode()
    resp = await client.post(
        "/webhooks/zoom", content=body, headers=_signed_headers(body)
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    assert len(sent_events) == 1
    event = sent_events[0]
    assert event.data["user_id"] == host.id
    assert event.data["attendee_user_ids"] == [attendee.id]
