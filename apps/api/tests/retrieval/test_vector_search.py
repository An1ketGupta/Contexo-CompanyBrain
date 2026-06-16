"""Unit tests for VectorRetriever.

Mocks the Supabase client and the embedder so no network calls are made.
Tests cover: short-circuit paths, RPC param construction, error propagation,
and correct mapping from raw DB rows to SearchHit objects.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from app.services.retrieval.vector_search import VectorRetriever, SearchHit


# ─── fixtures ────────────────────────────────────────────────────────────────

FAKE_VECTOR = [0.1] * 768
ORG_ID = "org-aaaaaaaa-0000-0000-0000-000000000001"
DOC_ID = "doc-bbbbbbbb-0000-0000-0000-000000000002"


def make_mock_embedder(vectors: list[list[float]] | None = None) -> AsyncMock:
    embedder = AsyncMock()
    embedder.embed_texts = AsyncMock(return_value=vectors if vectors is not None else [FAKE_VECTOR])
    return embedder


def make_mock_client(rows: list[dict]) -> MagicMock:
    response = MagicMock()
    response.data = rows
    rpc_result = MagicMock()
    rpc_result.execute.return_value = response
    client = MagicMock()
    client.rpc.return_value = rpc_result
    return client


def sample_row(**overrides) -> dict:
    defaults = {
        "chunk_id": "chunk-1",
        "content": "Employees get 20 vacation days per year.",
        "document_id": DOC_ID,
        "document_name": "HR Handbook",
        "chunk_index": 0,
        "page_number": 3,
        "section_heading": "Leave Policy",
        "similarity": 0.85,
    }
    return {**defaults, **overrides}


@pytest.fixture
def retriever():
    return VectorRetriever(embedder=make_mock_embedder())


# ─── short-circuit paths (no DB call) ────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_query_returns_empty(retriever):
    client = make_mock_client([])
    result = await retriever.search(query="", org_id=ORG_ID, client=client)
    assert result == []
    client.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_whitespace_only_query_returns_empty(retriever):
    client = make_mock_client([])
    result = await retriever.search(query="   \t\n  ", org_id=ORG_ID, client=client)
    assert result == []
    client.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_empty_document_ids_list_returns_empty_without_rpc(retriever):
    client = make_mock_client([])
    result = await retriever.search(
        query="vacation policy",
        org_id=ORG_ID,
        client=client,
        document_ids=[],
    )
    assert result == []
    client.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_embedder_returns_empty_list_returns_empty():
    embedder = make_mock_embedder(vectors=[])
    retriever = VectorRetriever(embedder=embedder)
    client = make_mock_client([])
    result = await retriever.search(query="what is our policy", org_id=ORG_ID, client=client)
    assert result == []
    client.rpc.assert_not_called()


# ─── happy path ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_normal_search_returns_search_hits(retriever):
    client = make_mock_client([sample_row()])
    result = await retriever.search(query="vacation", org_id=ORG_ID, client=client)
    assert len(result) == 1
    hit = result[0]
    assert isinstance(hit, SearchHit)
    assert hit.chunk_id == "chunk-1"
    assert hit.document_name == "HR Handbook"


@pytest.mark.asyncio
async def test_similarity_converted_to_float(retriever):
    # Supabase returns JSON numbers; ensure they're float not Decimal/str
    client = make_mock_client([sample_row(similarity="0.92")])  # str edge case
    result = await retriever.search(query="policy", org_id=ORG_ID, client=client)
    assert isinstance(result[0].similarity, float)
    assert result[0].similarity == pytest.approx(0.92)


@pytest.mark.asyncio
async def test_vector_similarity_equals_similarity_for_vector_branch(retriever):
    client = make_mock_client([sample_row(similarity=0.78)])
    result = await retriever.search(query="policy", org_id=ORG_ID, client=client)
    # vector branch sets both fields to the same cosine value
    assert result[0].vector_similarity == pytest.approx(0.78)


@pytest.mark.asyncio
async def test_multiple_rows_all_returned(retriever):
    rows = [sample_row(chunk_id=f"chunk-{i}", similarity=0.9 - i * 0.05) for i in range(5)]
    client = make_mock_client(rows)
    result = await retriever.search(query="vacation", org_id=ORG_ID, client=client)
    assert len(result) == 5
    assert [h.chunk_id for h in result] == [f"chunk-{i}" for i in range(5)]


@pytest.mark.asyncio
async def test_empty_db_result_returns_empty_list(retriever):
    client = make_mock_client([])
    result = await retriever.search(query="nonexistent topic", org_id=ORG_ID, client=client)
    assert result == []


# ─── RPC parameter construction ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_base_params_always_present(retriever):
    client = make_mock_client([])
    await retriever.search(query="test", org_id=ORG_ID, client=client, k=7)
    called_params = client.rpc.call_args[0][1]
    assert called_params["match_org_id"] == ORG_ID
    assert called_params["match_count"] == 7
    assert "query_embedding" in called_params


@pytest.mark.asyncio
async def test_document_id_param_included_when_provided(retriever):
    client = make_mock_client([])
    await retriever.search(query="test", org_id=ORG_ID, client=client, document_id=DOC_ID)
    params = client.rpc.call_args[0][1]
    assert params["match_document_id"] == DOC_ID


@pytest.mark.asyncio
async def test_document_id_absent_from_params_when_none(retriever):
    client = make_mock_client([])
    await retriever.search(query="test", org_id=ORG_ID, client=client)
    params = client.rpc.call_args[0][1]
    assert "match_document_id" not in params


@pytest.mark.asyncio
async def test_document_ids_param_included_when_provided(retriever):
    client = make_mock_client([])
    ids = [DOC_ID, "doc-cccccccc-0000-0000-0000-000000000003"]
    await retriever.search(query="test", org_id=ORG_ID, client=client, document_ids=ids)
    params = client.rpc.call_args[0][1]
    assert params["match_document_ids"] == ids


@pytest.mark.asyncio
async def test_both_document_filters_can_be_sent_together(retriever):
    client = make_mock_client([])
    ids = [DOC_ID]
    await retriever.search(
        query="test", org_id=ORG_ID, client=client,
        document_id=DOC_ID, document_ids=ids,
    )
    params = client.rpc.call_args[0][1]
    assert "match_document_id" in params
    assert "match_document_ids" in params


# ─── embedder task_type ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_type_is_retrieval_query():
    embedder = make_mock_embedder()
    retriever = VectorRetriever(embedder=embedder)
    client = make_mock_client([])
    await retriever.search(query="what is the leave policy", org_id=ORG_ID, client=client)
    _, kwargs = embedder.embed_texts.call_args
    # embed_texts called as embed_texts([query], task_type=...)
    # check either positional or keyword arg
    args = embedder.embed_texts.call_args
    all_args = list(args[0]) + list(args[1].values())
    assert "RETRIEVAL_QUERY" in all_args


# ─── error propagation ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rpc_exception_propagates(retriever):
    client = MagicMock()
    client.rpc.side_effect = RuntimeError("DB connection refused")
    with pytest.raises(RuntimeError, match="DB connection refused"):
        await retriever.search(query="policy", org_id=ORG_ID, client=client)


@pytest.mark.asyncio
async def test_execute_exception_propagates(retriever):
    rpc_result = MagicMock()
    rpc_result.execute.side_effect = Exception("timeout")
    client = MagicMock()
    client.rpc.return_value = rpc_result
    with pytest.raises(Exception, match="timeout"):
        await retriever.search(query="policy", org_id=ORG_ID, client=client)


@pytest.mark.asyncio
async def test_embedder_exception_propagates():
    embedder = AsyncMock()
    embedder.embed_texts.side_effect = Exception("quota exceeded")
    retriever = VectorRetriever(embedder=embedder)
    client = make_mock_client([])
    with pytest.raises(Exception, match="quota exceeded"):
        await retriever.search(query="policy", org_id=ORG_ID, client=client)


# ─── optional fields in row ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_optional_row_fields_none_when_missing(retriever):
    row = sample_row()
    del row["page_number"]
    del row["section_heading"]
    client = make_mock_client([row])
    result = await retriever.search(query="policy", org_id=ORG_ID, client=client)
    assert result[0].page_number is None
    assert result[0].section_heading is None
