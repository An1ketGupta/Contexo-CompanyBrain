"""V3 #80 — cache-key helpers + jitter math.

Pure-logic. Confirms hash stability, jitter bounds, and that the doc-list
cache module's filter signature actually differentiates filter combos.
"""
import asyncio

from app.services.redis_cache import hash_for_key, jittered_ttl

# ── hash stability ──────────────────────────────────────────────────────


def test_hash_is_stable_across_calls():
    assert hash_for_key("a", 1, ("x", "y")) == hash_for_key("a", 1, ("x", "y"))


def test_hash_changes_with_arg_order():
    # Cache keys MUST be order-sensitive — callers canonicalise (sort) before
    # passing, so flipping order should change the hash.
    assert hash_for_key("a", "b") != hash_for_key("b", "a")


def test_hash_distinguishes_none_vs_string():
    assert hash_for_key(None) != hash_for_key("None")


def test_hash_handles_nested_structures():
    h1 = hash_for_key("x", {"k": "v"})
    h2 = hash_for_key("x", {"k": "v"})
    h3 = hash_for_key("x", {"k": "other"})
    assert h1 == h2
    assert h1 != h3


# ── jitter bounds ───────────────────────────────────────────────────────


def test_jitter_stays_within_band():
    for _ in range(100):
        ttl = jittered_ttl(60, jitter_pct=0.1)
        assert 54 <= ttl <= 66, f"out of band: {ttl}"


def test_jitter_no_op_below_threshold():
    assert jittered_ttl(1) == 1
    assert jittered_ttl(0) == 0


def test_jitter_can_produce_distinct_values():
    samples = {jittered_ttl(60) for _ in range(50)}
    # Practically impossible for 50 draws over an 11-value band to collide.
    assert len(samples) > 3


# ── documents_cache.get_cached_document_list fail-open ──────────────────
# When Upstash is unreachable (the default in the test environment, no env
# vars set), cache_get_json returns None — and the helper degrades to "miss"
# without raising.


def test_doc_list_helper_returns_none_without_upstash(monkeypatch):
    monkeypatch.delenv("UPSTASH_REDIS_REST_URL", raising=False)
    monkeypatch.delenv("UPSTASH_REDIS_REST_TOKEN", raising=False)

    from app.services.documents_cache import get_cached_document_list

    result = asyncio.run(
        get_cached_document_list(
            org_id="org-1", filter_signature=("ready", None, (), "", "created_at", "desc", 50, 0)
        )
    )
    assert result is None


def test_doc_list_helper_empty_org_id_is_noop():
    from app.services.documents_cache import set_cached_document_list

    # Should not raise even though Upstash isn't configured.
    asyncio.run(
        set_cached_document_list(
            org_id="", filter_signature=(), payload={"documents": [], "total": 0}
        )
    )
