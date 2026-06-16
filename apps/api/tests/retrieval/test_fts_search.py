"""Unit tests for FTSRetriever.

Mocks the Supabase client — no DB needed. Tests cover: short-circuit paths,
RPC param construction, snippet handling, and error propagation.

Key FTS behaviors verified here (without hitting real Postgres):
- Empty / stopword-only queries are handled on the Python side (empty string
  check). The SQL-level empty-tsquery guard is tested in test_integration.py.
- Snippet field is populated when the DB row includes it; None when absent.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from app.services.retrieval.fts_search import FTSRetriever, SearchHit


# ─── fixtures ────────────────────────────────────────────────────────────────

ORG_ID = "org-aaaaaaaa-0000-0000-0000-000000000001"
DOC_ID = "doc-bbbbbbbb-0000-0000-0000-000000000002"


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
        "content": "The Q4-2024 revenue target is $5 million.",
        "document_id": DOC_ID,
        "document_name": "Finance Deck",
        "chunk_index": 2,
        "page_number": 5,
        "section_heading": "Revenue Targets",
        "similarity": 0.42,
        "snippet": "The <mark>Q4-2024</mark> revenue target is $5 million.",
    }
    return {**defaults, **overrides}


@pytest.fixture
def retriever():
    return FTSRetriever()


# ─── short-circuit paths ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_query_returns_empty(retriever):
    client = make_mock_client([])
    result = await retriever.search(query="", org_id=ORG_ID, client=client)
    assert result == []
    client.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_whitespace_only_query_returns_empty(retriever):
    client = make_mock_client([])
    result = await retriever.search(query="  \n\t  ", org_id=ORG_ID, client=client)
    assert result == []
    client.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_empty_document_ids_short_circuits(retriever):
    client = make_mock_client([])
    result = await retriever.search(
        query="revenue target",
        org_id=ORG_ID,
        client=client,
        document_ids=[],
    )
    assert result == []
    client.rpc.assert_not_called()


# ─── happy path ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_normal_search_returns_hits(retriever):
    client = make_mock_client([sample_row()])
    result = await retriever.search(query="Q4-2024 revenue", org_id=ORG_ID, client=client)
    assert len(result) == 1
    assert isinstance(result[0], SearchHit)
    assert result[0].chunk_id == "chunk-1"


@pytest.mark.asyncio
async def test_snippet_populated_from_row(retriever):
    client = make_mock_client([sample_row()])
    result = await retriever.search(query="Q4-2024", org_id=ORG_ID, client=client)
    assert result[0].snippet == "The <mark>Q4-2024</mark> revenue target is $5 million."


@pytest.mark.asyncio
async def test_snippet_none_when_absent_from_row(retriever):
    row = sample_row()
    del row["snippet"]
    client = make_mock_client([row])
    result = await retriever.search(query="revenue", org_id=ORG_ID, client=client)
    assert result[0].snippet is None


@pytest.mark.asyncio
async def test_snippet_none_when_db_returns_null(retriever):
    client = make_mock_client([sample_row(snippet=None)])
    result = await retriever.search(query="revenue", org_id=ORG_ID, client=client)
    assert result[0].snippet is None


@pytest.mark.asyncio
async def test_similarity_converted_to_float(retriever):
    client = make_mock_client([sample_row(similarity="0.37")])
    result = await retriever.search(query="revenue", org_id=ORG_ID, client=client)
    assert isinstance(result[0].similarity, float)
    assert result[0].similarity == pytest.approx(0.37)


@pytest.mark.asyncio
async def test_multiple_rows_returned_in_order(retriever):
    rows = [sample_row(chunk_id=f"chunk-{i}", similarity=0.9 - i * 0.1) for i in range(4)]
    client = make_mock_client(rows)
    result = await retriever.search(query="revenue target", org_id=ORG_ID, client=client)
    assert [h.chunk_id for h in result] == [f"chunk-{i}" for i in range(4)]


@pytest.mark.asyncio
async def test_empty_db_result_returns_empty_list(retriever):
    client = make_mock_client([])
    result = await retriever.search(query="xylophone", org_id=ORG_ID, client=client)
    assert result == []


# ─── RPC parameter construction ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_base_params(retriever):
    client = make_mock_client([])
    await retriever.search(query="revenue target", org_id=ORG_ID, client=client, k=15)
    params = client.rpc.call_args[0][1]
    assert params["query_text"] == "revenue target"
    assert params["match_org_id"] == ORG_ID
    assert params["match_count"] == 15


@pytest.mark.asyncio
async def test_document_id_param_sent_when_provided(retriever):
    client = make_mock_client([])
    await retriever.search(query="revenue", org_id=ORG_ID, client=client, document_id=DOC_ID)
    params = client.rpc.call_args[0][1]
    assert params["match_document_id"] == DOC_ID


@pytest.mark.asyncio
async def test_document_id_absent_when_none(retriever):
    client = make_mock_client([])
    await retriever.search(query="revenue", org_id=ORG_ID, client=client)
    params = client.rpc.call_args[0][1]
    assert "match_document_id" not in params


@pytest.mark.asyncio
async def test_document_ids_param_sent(retriever):
    client = make_mock_client([])
    ids = [DOC_ID, "doc-other"]
    await retriever.search(query="revenue", org_id=ORG_ID, client=client, document_ids=ids)
    params = client.rpc.call_args[0][1]
    assert params["match_document_ids"] == ids


@pytest.mark.asyncio
async def test_rpc_called_with_function_name_fts_search(retriever):
    client = make_mock_client([])
    await retriever.search(query="anything", org_id=ORG_ID, client=client)
    called_fn = client.rpc.call_args[0][0]
    assert called_fn == "fts_search"


# ─── error propagation ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rpc_exception_propagates(retriever):
    client = MagicMock()
    client.rpc.side_effect = RuntimeError("network error")
    with pytest.raises(RuntimeError, match="network error"):
        await retriever.search(query="revenue", org_id=ORG_ID, client=client)


@pytest.mark.asyncio
async def test_execute_exception_propagates(retriever):
    rpc_result = MagicMock()
    rpc_result.execute.side_effect = Exception("timeout reading from DB")
    client = MagicMock()
    client.rpc.return_value = rpc_result
    with pytest.raises(Exception, match="timeout"):
        await retriever.search(query="revenue", org_id=ORG_ID, client=client)


# ─── optional row fields ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_optional_page_number_none_when_missing(retriever):
    row = sample_row()
    del row["page_number"]
    client = make_mock_client([row])
    result = await retriever.search(query="revenue", org_id=ORG_ID, client=client)
    assert result[0].page_number is None


@pytest.mark.asyncio
async def test_optional_section_heading_none_when_missing(retriever):
    row = sample_row()
    del row["section_heading"]
    client = make_mock_client([row])
    result = await retriever.search(query="revenue", org_id=ORG_ID, client=client)
    assert result[0].section_heading is None
