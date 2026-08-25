"""Integration tests against a real local Supabase instance for the
document-sharing feature's security-critical surface (migration 089):

  * the transcript trigger forces new Meet transcripts private
  * document_shares grants extend documents_select / meeting_summaries_select_org
  * document_shares grants extend fts_search's visibility predicate

Run with: pytest -m integration (excluded from the default run — see pytest.ini).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def _insert_private_transcript(service_client, org_id: str, created_by: str) -> dict:
    row = (
        service_client.table("documents")
        .insert(
            {
                "org_id": org_id,
                "name": "Q3 roadmap sync — transcript",
                "file_path": "orgs/x/docs/x/transcript.vtt",
                "file_type": "vtt",
                "source": "google_meet_transcript",
                "created_by": created_by,
                # visibility intentionally omitted — the migration 086 trigger
                # must set it, not the column DEFAULT ('org').
            }
        )
        .execute()
    )
    return row.data[0]


def test_transcript_document_defaults_to_private(service_client, org, make_user):
    host = make_user()
    doc = _insert_private_transcript(service_client, org["id"], host.id)
    assert doc["visibility"] == "private"


def test_shared_user_can_see_private_document_outsider_cannot(
    service_client, org, make_user
):
    host = make_user()
    shared = make_user()
    outsider = make_user()

    doc = _insert_private_transcript(service_client, org["id"], host.id)
    doc_id = doc["id"]

    service_client.table("document_shares").insert(
        {"document_id": doc_id, "org_id": org["id"], "user_id": shared.id}
    ).execute()

    host_seen = host.client.table("documents").select("id").eq("id", doc_id).execute()
    shared_seen = (
        shared.client.table("documents").select("id").eq("id", doc_id).execute()
    )
    outsider_seen = (
        outsider.client.table("documents").select("id").eq("id", doc_id).execute()
    )

    assert len(host_seen.data) == 1
    assert len(shared_seen.data) == 1
    assert len(outsider_seen.data) == 0


def test_shared_user_can_see_meeting_summary_of_shared_transcript(
    service_client, org, make_user
):
    host = make_user()
    shared = make_user()
    outsider = make_user()

    doc = _insert_private_transcript(service_client, org["id"], host.id)
    doc_id = doc["id"]

    service_client.table("document_shares").insert(
        {"document_id": doc_id, "org_id": org["id"], "user_id": shared.id}
    ).execute()

    summary = (
        service_client.table("meeting_summaries")
        .insert(
            {
                "org_id": org["id"],
                "source_document_id": doc_id,
                "source_format": "google_meet",
                "summary": "Discussed Q3 roadmap priorities.",
            }
        )
        .execute()
        .data[0]
    )

    host_seen = (
        host.client.table("meeting_summaries")
        .select("id")
        .eq("id", summary["id"])
        .execute()
    )
    shared_seen = (
        shared.client.table("meeting_summaries")
        .select("id")
        .eq("id", summary["id"])
        .execute()
    )
    outsider_seen = (
        outsider.client.table("meeting_summaries")
        .select("id")
        .eq("id", summary["id"])
        .execute()
    )

    assert len(host_seen.data) == 1
    assert len(shared_seen.data) == 1
    assert len(outsider_seen.data) == 0


def test_fts_search_respects_document_shares_grant(service_client, org, make_user):
    host = make_user()
    shared = make_user()
    outsider = make_user()

    doc = _insert_private_transcript(service_client, org["id"], host.id)
    doc_id = doc["id"]

    service_client.table("chunks").insert(
        {
            "org_id": org["id"],
            "document_id": doc_id,
            "content": "We finalized the Q3 roadmap and prioritized the search revamp.",
            "chunk_index": 0,
        }
    ).execute()

    service_client.table("document_shares").insert(
        {"document_id": doc_id, "org_id": org["id"], "user_id": shared.id}
    ).execute()

    def _search(client) -> list[dict]:
        result = client.rpc(
            "fts_search",
            {"query_text": "roadmap", "match_org_id": org["id"]},
        ).execute()
        return result.data or []

    host_results = _search(host.client)
    shared_results = _search(shared.client)
    outsider_results = _search(outsider.client)

    assert any(r["document_id"] == doc_id for r in host_results)
    assert any(r["document_id"] == doc_id for r in shared_results)
    assert not any(r["document_id"] == doc_id for r in outsider_results)


def test_unshared_private_document_is_invisible_to_org_mate(
    service_client, org, make_user
):
    """Control case: without a document_shares grant, a same-org user still
    can't see another user's private transcript — proves the share grant
    (not just org membership) is what's doing the work above."""
    host = make_user()
    other = make_user()

    doc = _insert_private_transcript(service_client, org["id"], host.id)
    doc_id = doc["id"]

    other_seen = (
        other.client.table("documents").select("id").eq("id", doc_id).execute()
    )
    assert len(other_seen.data) == 0
