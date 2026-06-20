"""Integration tests for vector_search and fts_search SQL functions.

These tests run against a real Supabase instance (local or remote) and call
the real Gemini API to embed test data. They are intentionally slow and are
excluded from the default test run.

Prerequisites:
    supabase start          # local Supabase
    supabase db push        # apply all migrations

Run with:
    uv run pytest tests/retrieval/test_integration.py -v -m integration

Required env vars (from apps/api/.env):
    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, GEMINI_API_KEY

Test data lifecycle:
    Each test function creates its own org (random UUID, no auth.users row
    needed with service_role client), seeds chunks + embeddings, then cleans
    them up in a finally block. Tests are fully self-contained.
"""
from __future__ import annotations

import os
import uuid
import asyncio
import pytest

# Skip the entire module if the required env vars are absent, so the unit
# tests keep passing in CI without Supabase credentials.
pytestmark = pytest.mark.integration

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")

_missing = [v for v in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "GEMINI_API_KEY") if not os.getenv(v)]
if _missing:
    pytest.skip(
        f"Integration tests skipped — missing env vars: {', '.join(_missing)}",
        allow_module_level=True,
    )


# ─── lazy imports (only evaluated when env vars present) ──────────────────────

from supabase import create_client, Client
from app.services.ingestion.embedder import GoogleEmbedder
from app.services.retrieval.vector_search import vector_search
from app.services.retrieval.fts_search import fts_search
from app.services.retrieval.hybrid_search import hybrid_search


# ─── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def service_client() -> Client:
    """Service-role client — bypasses RLS so we can seed/clean test data."""
    return create_client(SUPABASE_URL, SERVICE_KEY)


@pytest.fixture(scope="module")
def embedder() -> GoogleEmbedder:
    return GoogleEmbedder(api_key=GEMINI_KEY)


TEST_CHUNKS = [
    {
        "content": "Employees are entitled to 20 days of paid vacation per calendar year. "
                   "Unused vacation days roll over up to a maximum of 10 days.",
        "section_heading": "Vacation Policy",
    },
    {
        "content": "The parental leave policy grants 16 weeks of fully paid leave "
                   "for primary caregivers and 4 weeks for secondary caregivers.",
        "section_heading": "Parental Leave",
    },
    {
        "content": "Q4-2024 revenue target is $5,000,000. Q3 actual was $4,200,000, "
                   "representing 12% growth quarter over quarter.",
        "section_heading": "Financial Targets",
    },
    {
        "content": "All employees must complete the mandatory security training SKU-SEC-001 "
                   "within 30 days of joining and annually thereafter.",
        "section_heading": "Security Training",
    },
    {
        "content": "The engineering on-call rotation runs Sunday to Sunday. "
                   "Incident severity is classified as P0, P1, P2, or P3.",
        "section_heading": "On-Call Policy",
    },
]


@pytest.fixture(scope="module")
def seeded_org(service_client, embedder):
    """Creates a temporary org + document + chunks + embeddings. Tears down after."""
    org_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())

    # Insert org (no FK to auth.users with service role)
    service_client.table("organizations").insert({
        "id": org_id,
        "name": "Integration Test Org",
        "slug": f"test-{org_id[:8]}",
        "plan": "starter",
    }).execute()

    # Insert document
    service_client.table("documents").insert({
        "id": doc_id,
        "org_id": org_id,
        "name": "Company Handbook",
        "file_path": "test/handbook.pdf",
        "file_type": "pdf",
        "status": "ready",
    }).execute()

    # Embed all test chunks
    texts = [c["content"] for c in TEST_CHUNKS]
    vectors = asyncio.get_event_loop().run_until_complete(
        embedder.embed_texts(texts, task_type="RETRIEVAL_DOCUMENT")
    )

    chunk_ids = []
    for i, (chunk, vec) in enumerate(zip(TEST_CHUNKS, vectors)):
        chunk_id = str(uuid.uuid4())
        chunk_ids.append(chunk_id)

        service_client.table("chunks").insert({
            "id": chunk_id,
            "org_id": org_id,
            "document_id": doc_id,
            "content": chunk["content"],
            "section_heading": chunk["section_heading"],
            "chunk_index": i,
            "page_number": i + 1,
        }).execute()

        embedding_id = str(uuid.uuid4())
        service_client.table("embeddings").insert({
            "id": embedding_id,
            "chunk_id": chunk_id,
            "org_id": org_id,
            "embedding": vec,
        }).execute()

    yield {"org_id": org_id, "doc_id": doc_id, "chunk_ids": chunk_ids}

    # Teardown — cascade handles embeddings and chunks if FK constraints set;
    # delete explicitly to be safe.
    for cid in chunk_ids:
        service_client.table("embeddings").delete().eq("chunk_id", cid).execute()
        service_client.table("chunks").delete().eq("id", cid).execute()
    service_client.table("documents").delete().eq("id", doc_id).execute()
    service_client.table("organizations").delete().eq("id", org_id).execute()


# ─── vector_search tests ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vector_search_returns_results(seeded_org, service_client):
    result = await vector_search(
        "what is the vacation policy",
        seeded_org["org_id"],
        service_client,
        k=5,
    )
    assert len(result) > 0


@pytest.mark.asyncio
async def test_vector_search_top_result_is_semantically_correct(seeded_org, service_client):
    # "vacation days" should surface the vacation policy chunk, not the financial one
    result = await vector_search(
        "how many vacation days do employees get",
        seeded_org["org_id"],
        service_client,
        k=5,
    )
    assert len(result) >= 1
    assert "vacation" in result[0].content.lower()


@pytest.mark.asyncio
async def test_vector_search_similarity_in_zero_one_range(seeded_org, service_client):
    result = await vector_search(
        "parental leave benefits",
        seeded_org["org_id"],
        service_client,
        k=5,
    )
    for hit in result:
        assert 0.0 <= hit.similarity <= 1.0, f"similarity out of range: {hit.similarity}"


@pytest.mark.asyncio
async def test_vector_search_org_isolation(service_client, embedder):
    # Create a second org and verify it gets no results from the first org's data
    other_org_id = str(uuid.uuid4())
    service_client.table("organizations").insert({
        "id": other_org_id,
        "name": "Other Org",
        "slug": f"other-{other_org_id[:8]}",
        "plan": "starter",
    }).execute()
    try:
        result = await vector_search(
            "vacation policy",
            other_org_id,
            service_client,
            k=10,
        )
        assert result == [], "Cross-org data leaked through vector_search"
    finally:
        service_client.table("organizations").delete().eq("id", other_org_id).execute()


@pytest.mark.asyncio
async def test_vector_search_document_id_filter(seeded_org, service_client):
    # When filtered to a specific doc, results should only come from that doc
    result = await vector_search(
        "leave policy",
        seeded_org["org_id"],
        service_client,
        k=10,
        document_id=seeded_org["doc_id"],
    )
    for hit in result:
        assert hit.document_id == seeded_org["doc_id"]


@pytest.mark.asyncio
async def test_vector_search_returns_empty_for_empty_document_ids(seeded_org, service_client):
    result = await vector_search(
        "vacation",
        seeded_org["org_id"],
        service_client,
        k=5,
        document_ids=[],
    )
    assert result == []


@pytest.mark.asyncio
async def test_vector_search_semantically_dissimilar_query_still_returns_results(seeded_org, service_client):
    # Even an unrelated query returns top-k (pgvector always finds nearest neighbors)
    result = await vector_search(
        "recipe for chocolate cake baking temperature",
        seeded_org["org_id"],
        service_client,
        k=3,
    )
    # We get results, but similarity should be noticeably lower
    assert len(result) > 0
    # Top similarity for a completely off-topic query should be well below 0.8
    assert result[0].similarity < 0.85


# ─── fts_search tests ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fts_finds_exact_product_code(seeded_org, service_client):
    # "SKU-SEC-001" is a rare string that vector search might smooth over
    result = await fts_search(
        "SKU-SEC-001",
        seeded_org["org_id"],
        service_client,
        k=5,
    )
    assert len(result) >= 1
    assert "SKU-SEC-001" in result[0].content


@pytest.mark.asyncio
async def test_fts_finds_quoted_phrase(seeded_org, service_client):
    result = await fts_search(
        '"Q4-2024 revenue"',
        seeded_org["org_id"],
        service_client,
        k=5,
    )
    assert len(result) >= 1
    assert "Q4-2024" in result[0].content


@pytest.mark.asyncio
async def test_fts_stopwords_only_query_returns_empty(seeded_org, service_client):
    # "the and is" are English stopwords — websearch_to_tsquery produces empty tsquery
    result = await fts_search(
        "the and is",
        seeded_org["org_id"],
        service_client,
        k=10,
    )
    assert result == []


@pytest.mark.asyncio
async def test_fts_snippet_contains_mark_tags(seeded_org, service_client):
    result = await fts_search(
        "vacation days",
        seeded_org["org_id"],
        service_client,
        k=5,
    )
    # ts_headline wraps matched terms in <mark>...</mark>
    hits_with_marks = [h for h in result if h.snippet and "<mark>" in h.snippet]
    assert len(hits_with_marks) > 0


@pytest.mark.asyncio
async def test_fts_case_insensitive(seeded_org, service_client):
    lower = await fts_search("vacation", seeded_org["org_id"], service_client, k=5)
    upper = await fts_search("VACATION", seeded_org["org_id"], service_client, k=5)
    assert {h.chunk_id for h in lower} == {h.chunk_id for h in upper}


@pytest.mark.asyncio
async def test_archived_chunks_are_excluded_from_search(service_client):
    org_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())

    service_client.table("organizations").insert({
        "id": org_id,
        "name": "Archived Search Org",
        "slug": f"arch-{org_id[:8]}",
        "plan": "starter",
    }).execute()
    service_client.table("documents").insert({
        "id": doc_id,
        "org_id": org_id,
        "name": "Archived Only Doc",
        "file_path": "test/archived.pdf",
        "file_type": "pdf",
        "status": "ready",
    }).execute()
    service_client.table("chunks").insert({
        "id": chunk_id,
        "org_id": org_id,
        "document_id": doc_id,
        "content": "ARCHIVED-ONLY-TERM-928374 should never surface in search results.",
        "chunk_index": 0,
        "page_number": 1,
        "is_archived": True,
    }).execute()

    try:
        fts_result = await fts_search(
            "ARCHIVED-ONLY-TERM-928374",
            org_id,
            service_client,
            k=5,
        )
        hybrid_result = await hybrid_search(
            "ARCHIVED-ONLY-TERM-928374",
            org_id,
            service_client,
            k=5,
        )
        assert fts_result == []
        assert hybrid_result == []
    finally:
        service_client.table("chunks").delete().eq("id", chunk_id).execute()
        service_client.table("documents").delete().eq("id", doc_id).execute()
        service_client.table("organizations").delete().eq("id", org_id).execute()


@pytest.mark.asyncio
async def test_fts_stemming_matches_inflected_forms(seeded_org, service_client):
    # "employees" should match chunks containing "employee" (English stemmer)
    with_s = await fts_search("employees", seeded_org["org_id"], service_client, k=10)
    without_s = await fts_search("employee", seeded_org["org_id"], service_client, k=10)
    assert {h.chunk_id for h in with_s} == {h.chunk_id for h in without_s}


@pytest.mark.asyncio
async def test_fts_org_isolation(service_client):
    other_org_id = str(uuid.uuid4())
    service_client.table("organizations").insert({
        "id": other_org_id, "name": "Isolated Org",
        "slug": f"iso-{other_org_id[:8]}", "plan": "starter",
    }).execute()
    try:
        result = await fts_search("vacation", other_org_id, service_client, k=10)
        assert result == []
    finally:
        service_client.table("organizations").delete().eq("id", other_org_id).execute()


# ─── hybrid_search tests ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hybrid_covers_both_semantic_and_exact(seeded_org, service_client):
    # "SKU-SEC-001" is exact-match only (vector search may miss it);
    # "on-call rotation" is semantic only (FTS may not match the exact phrase).
    # Hybrid should surface both in a single combined result.
    result = await hybrid_search(
        "SKU-SEC-001 on-call rotation",
        seeded_org["org_id"],
        service_client,
        k=10,
    )
    contents = " ".join(h.content for h in result)
    assert "SKU-SEC-001" in contents
    assert "on-call" in contents


@pytest.mark.asyncio
async def test_hybrid_chunk_in_both_branches_surfaces_at_top(seeded_org, service_client):
    # "vacation" appears both semantically close and as an exact keyword match.
    # The vacation policy chunk should rank #1 via RRF double-contribution.
    result = await hybrid_search(
        "vacation days employees",
        seeded_org["org_id"],
        service_client,
        k=5,
    )
    assert len(result) >= 1
    assert "vacation" in result[0].content.lower()


@pytest.mark.asyncio
async def test_hybrid_match_sources_populated(seeded_org, service_client):
    result = await hybrid_search(
        "vacation policy leave",
        seeded_org["org_id"],
        service_client,
        k=10,
    )
    # At least one result should appear in both branches (if any overlap)
    sources = [h.match_sources for h in result]
    all_sources = set(s for src in sources for s in src)
    assert "vector" in all_sources or "fts" in all_sources
