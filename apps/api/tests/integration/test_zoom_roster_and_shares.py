"""Integration tests against a real local Supabase instance for the Zoom
service-layer primitives added in migration 089: the participant roster
buffer, the document_shares grant, and the two opt-in maps living on the
unified `integrations` row.

Run with: pytest -m integration (excluded from the default run — see pytest.ini).
"""
from __future__ import annotations

import uuid

import pytest

from app.services.integrations import _unified
from app.services.integrations import zoom as zoom_svc

pytestmark = pytest.mark.integration


async def test_record_participant_is_idempotent(service_client, org):
    meeting_uuid = f"meeting-{uuid.uuid4().hex}"

    await zoom_svc.record_participant(
        org_id=org["id"], meeting_uuid=meeting_uuid, email="Alice@Example.com"
    )
    await zoom_svc.record_participant(
        org_id=org["id"], meeting_uuid=meeting_uuid, email="alice@example.com"
    )

    rows = (
        service_client.table("zoom_meeting_participants")
        .select("*")
        .eq("meeting_uuid", meeting_uuid)
        .execute()
        .data
    )
    assert len(rows) == 1
    assert rows[0]["email"] == "alice@example.com"


async def test_record_participant_noops_without_email(org):
    meeting_uuid = f"meeting-{uuid.uuid4().hex}"
    # Must not raise — external guests joining without a Zoom login have no email.
    await zoom_svc.record_participant(org_id=org["id"], meeting_uuid=meeting_uuid, email=None)
    emails = await zoom_svc.get_participant_emails(meeting_uuid)
    assert emails == []


async def test_get_participant_emails_returns_roster(org):
    meeting_uuid = f"meeting-{uuid.uuid4().hex}"
    await zoom_svc.record_participant(
        org_id=org["id"], meeting_uuid=meeting_uuid, email="alice@example.com"
    )
    await zoom_svc.record_participant(
        org_id=org["id"], meeting_uuid=meeting_uuid, email="bob@example.com"
    )

    emails = await zoom_svc.get_participant_emails(meeting_uuid)
    assert set(emails) == {"alice@example.com", "bob@example.com"}


async def test_get_participant_emails_scoped_to_meeting_uuid(org):
    meeting_a = f"meeting-{uuid.uuid4().hex}"
    meeting_b = f"meeting-{uuid.uuid4().hex}"
    await zoom_svc.record_participant(org_id=org["id"], meeting_uuid=meeting_a, email="a@x.com")
    await zoom_svc.record_participant(org_id=org["id"], meeting_uuid=meeting_b, email="b@x.com")

    assert await zoom_svc.get_participant_emails(meeting_a) == ["a@x.com"]
    assert await zoom_svc.get_participant_emails(meeting_b) == ["b@x.com"]


async def test_grant_document_shares_is_idempotent(service_client, org, make_user):
    host = make_user()
    shared = make_user()
    doc = (
        service_client.table("documents")
        .insert(
            {
                "org_id": org["id"],
                "name": "transcript",
                "file_path": "orgs/x/docs/x/t.vtt",
                "file_type": "vtt",
                "source": "zoom",
                "created_by": host.id,
            }
        )
        .execute()
        .data[0]
    )

    await zoom_svc.grant_document_shares(
        document_id=doc["id"], org_id=org["id"], user_ids=[shared.id]
    )
    await zoom_svc.grant_document_shares(
        document_id=doc["id"], org_id=org["id"], user_ids=[shared.id]
    )

    rows = (
        service_client.table("document_shares")
        .select("*")
        .eq("document_id", doc["id"])
        .execute()
        .data
    )
    assert len(rows) == 1
    assert rows[0]["user_id"] == shared.id


async def test_grant_document_shares_noops_on_empty_list(service_client, org, make_user):
    host = make_user()
    doc = (
        service_client.table("documents")
        .insert(
            {
                "org_id": org["id"],
                "name": "transcript",
                "file_path": "orgs/x/docs/x/t.vtt",
                "file_type": "vtt",
                "source": "zoom",
                "created_by": host.id,
            }
        )
        .execute()
        .data[0]
    )
    await zoom_svc.grant_document_shares(document_id=doc["id"], org_id=org["id"], user_ids=[])
    rows = (
        service_client.table("document_shares")
        .select("*")
        .eq("document_id", doc["id"])
        .execute()
        .data
    )
    assert rows == []


async def test_transcript_and_attendee_optin_round_trip(org, make_user):
    user = make_user()
    await _unified.upsert_row(
        org_id=org["id"],
        provider="zoom",
        connected_by=user.id,
        access_token="fake-token",
        metadata={"account_id": "acct-1", "email": "connector@example.com"},
    )

    await zoom_svc.set_transcript_optin(
        org_id=org["id"], user_id=user.id, email="host@example.com", opted_in=True
    )
    await zoom_svc.set_attendee_optin(
        org_id=org["id"], user_id=user.id, email="attendee@example.com", opted_in=True
    )

    row = await _unified.get_row(org_id=org["id"], provider="zoom")
    assert zoom_svc.is_user_opted_in(row, user.id) is True
    assert zoom_svc.is_attendee_opted_in(row, user.id) is True
    assert row["metadata"][zoom_svc.OPTINS_KEY]["host@example.com"] == user.id
    assert row["metadata"][zoom_svc.ATTENDEE_OPTINS_KEY]["attendee@example.com"] == user.id

    # Opting out removes the entry, and does not disturb the other map.
    await zoom_svc.set_transcript_optin(
        org_id=org["id"], user_id=user.id, email="host@example.com", opted_in=False
    )
    row = await _unified.get_row(org_id=org["id"], provider="zoom")
    assert zoom_svc.is_user_opted_in(row, user.id) is False
    assert zoom_svc.is_attendee_opted_in(row, user.id) is True


async def test_set_transcript_optin_raises_when_not_connected(org, make_user):
    user = make_user()
    with pytest.raises(RuntimeError, match="zoom_not_connected"):
        await zoom_svc.set_transcript_optin(
            org_id=org["id"], user_id=user.id, email="host@example.com", opted_in=True
        )
