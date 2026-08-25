"""Integration test for the calendar_intelligence.py extension that lets an
user's own meeting-prep brief see prior-meeting context from a private
transcript they didn't create, as long as they hold a document_shares grant.
Previously this lookup only matched
documents the brief's own user created.

Run with: pytest -m integration (excluded from the default run — see pytest.ini).
"""
from __future__ import annotations

import uuid

import pytest

from app.services.calendar_intelligence import resolve_prior_meeting_context

pytestmark = pytest.mark.integration

_START = "2024-01-01T10:00:00+00:00"
_DOC_CREATED_AT = "2024-01-01T10:15:00+00:00"  # inside the [-1h, +6h] window


def _prior_row(org_id: str, user_id: str) -> dict:
    return {
        "org_id": org_id,
        "user_id": user_id,
        "start_time": _START,
        "title": "Weekly Sync",
    }


async def test_attendee_without_share_gets_no_prior_context(
    service_client, org, make_user
):
    host = make_user()
    attendee = make_user()

    doc = (
        service_client.table("documents")
        .insert(
            {
                "org_id": org["id"],
                "name": "Weekly Sync — transcript",
                "file_path": "orgs/x/docs/x/t.vtt",
                "file_type": "vtt",
                "source": "google_meet_transcript",
                "created_by": host.id,
                "created_at": _DOC_CREATED_AT,
            }
        )
        .execute()
        .data[0]
    )
    service_client.table("meeting_summaries").insert(
        {
            "org_id": org["id"],
            "source_document_id": doc["id"],
            "source_format": "google_meet",
            "summary": "Decided to ship the search revamp in Q3.",
        }
    ).execute()

    result = await resolve_prior_meeting_context(_prior_row(org["id"], attendee.id))
    assert result is None


async def test_attendee_with_share_gets_prior_context_from_hosts_transcript(
    service_client, org, make_user
):
    host = make_user()
    attendee = make_user()

    doc = (
        service_client.table("documents")
        .insert(
            {
                "org_id": org["id"],
                "name": "Weekly Sync — transcript",
                "file_path": "orgs/x/docs/x/t.vtt",
                "file_type": "vtt",
                "source": "google_meet_transcript",
                "created_by": host.id,
                "created_at": _DOC_CREATED_AT,
            }
        )
        .execute()
        .data[0]
    )
    service_client.table("meeting_summaries").insert(
        {
            "org_id": org["id"],
            "source_document_id": doc["id"],
            "source_format": "google_meet",
            "summary": "Decided to ship the search revamp in Q3.",
        }
    ).execute()
    service_client.table("document_shares").insert(
        {"document_id": doc["id"], "org_id": org["id"], "user_id": attendee.id}
    ).execute()

    result = await resolve_prior_meeting_context(_prior_row(org["id"], attendee.id))

    assert result is not None
    assert "Q3" in result.recap_text
    assert result.source_doc["document_id"] == doc["id"]


async def test_host_still_gets_own_transcript_as_prior_context(
    service_client, org, make_user
):
    """Control: the pre-existing created_by path still works after the merge."""
    host = make_user()
    doc = (
        service_client.table("documents")
        .insert(
            {
                "org_id": org["id"],
                "name": "Weekly Sync — transcript",
                "file_path": "orgs/x/docs/x/t.vtt",
                "file_type": "vtt",
                "source": "google_meet_transcript",
                "created_by": host.id,
                "created_at": _DOC_CREATED_AT,
            }
        )
        .execute()
        .data[0]
    )
    service_client.table("meeting_summaries").insert(
        {
            "org_id": org["id"],
            "source_document_id": doc["id"],
            "source_format": "google_meet",
            "summary": "Decided to ship the search revamp in Q3.",
        }
    ).execute()

    result = await resolve_prior_meeting_context(_prior_row(org["id"], host.id))
    assert result is not None
    assert result.source_doc["document_id"] == doc["id"]


async def test_share_outside_time_window_is_ignored(service_client, org, make_user):
    host = make_user()
    attendee = make_user()

    far_created_at = "2024-01-01T20:00:00+00:00"  # >6h after start — outside window
    doc = (
        service_client.table("documents")
        .insert(
            {
                "org_id": org["id"],
                "name": "Weekly Sync — transcript",
                "file_path": "orgs/x/docs/x/t.vtt",
                "file_type": "vtt",
                "source": "google_meet_transcript",
                "created_by": host.id,
                "created_at": far_created_at,
            }
        )
        .execute()
        .data[0]
    )
    service_client.table("meeting_summaries").insert(
        {
            "org_id": org["id"],
            "source_document_id": doc["id"],
            "source_format": "google_meet",
            "summary": "Unrelated later meeting.",
        }
    ).execute()
    service_client.table("document_shares").insert(
        {"document_id": doc["id"], "org_id": org["id"], "user_id": attendee.id}
    ).execute()

    result = await resolve_prior_meeting_context(_prior_row(org["id"], attendee.id))
    assert result is None
